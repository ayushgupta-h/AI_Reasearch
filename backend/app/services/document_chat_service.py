"""
app/services/document_chat_service.py
=====================================
Grounded conversational question-answering for research documents.

Provides single-document and multi-document comparative chat, strict
tenant-isolated vector retrieval, verifiable citations (page & section),
and persistent conversation history.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings, get_settings
from app.errors import LLMProviderError, NotFoundError, ValidationError
from app.observability import get_logger, redact
from app.repositories.documents import DocumentRepository
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.services.research_memory_store import ResearchMemoryStore

logger = get_logger("app.services.document_chat")

DOCUMENT_CHAT_SYSTEM_PROMPT = """You are Quest, an expert research analyst and conversational document intelligence assistant.
Your task is to answer the user's questions about the provided document(s) with extreme accuracy, clarity, and grounding.

RULES:
1. Base your answer EXCLUSIVELY on the provided document excerpts and context passages.
2. If the excerpts do not contain sufficient information to answer the question, state clearly: "Based on the provided document passages, this information is not specified." Do not hallucinate or extrapolate beyond what is stated.
3. Every factual claim, number, statistic, or quote MUST be supported by an inline citation bracket matching the source passage number (e.g. [1], [2]).
4. Structure your response professionally using clean GitHub-flavored Markdown:
   - Provide a direct answer or executive takeaway first.
   - Use clear markdown section headers (### Section Name) to divide distinct themes.
   - Format lists with markdown bullet points (- **Item / Concept:** Details [1]). Never output an unformatted wall of text or plain run-on lines.
   - For publication metadata, credits, statistics, or metrics, format them cleanly into bullet points or a markdown table.
   - Always leave blank lines between headings, paragraphs, and list blocks.
   - Bold critical concepts, entity names, figures, and conclusions.
5. Maintain conversational context across follow-up queries while keeping factual claims strictly tied to the source text.
"""

MULTI_DOC_SYSTEM_PROMPT = """You are Quest, an expert comparative research analyst.
Your task is to analyze and compare multiple documents provided by the user.

RULES:
1. Synthesize findings across the documents, clearly attributing facts to each specific document using citations (e.g. [1], [2]).
2. Highlight areas of agreement, contrasting points, and gaps between the documents.
3. Structure your response with clear markdown headings (### Section Title) and formatted comparison tables or bullet points.
4. If a document does not address a point, explicitly mention that.
5. Never invent or speculate facts not present in the excerpts.
"""


class DocumentChatService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        document_service: DocumentService,
        chat_service: ChatService,
        settings: Optional[Settings] = None,
    ) -> None:
        self._repository = repository
        self._doc_service = document_service
        self._chat_service = chat_service
        self._settings = settings or getattr(chat_service, "settings", None) or get_settings()

    # ── Single-Document Chat ──────────────────────────────────────────────

    def chat_with_document(
        self,
        *,
        user_id: str,
        document_id: str,
        question: str,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a grounded QA turn on a single document."""
        clean_q = (question or "").strip()
        if not clean_q:
            raise ValidationError("Question cannot be empty.")

        # 1. Verify document ownership
        doc_row = self._doc_service.get_document(document_id, user_id)
        if not doc_row:
            raise NotFoundError("Document not found or access denied.")

        raw_name = doc_row.get("original_filename", "document")
        filename = os.path.basename(raw_name.replace("\\", "/"))
        project_id = doc_row.get("project_id")

        # 2. Conversation management
        conv = None
        if conversation_id:
            conv = self._repository.get_conversation(conversation_id, user_id)
            if not conv:
                conv = self._repository.create_conversation(
                    owner_id=user_id,
                    document_id=document_id,
                    title=clean_q[:60],
                    project_id=project_id,
                )
                conversation_id = conv["conversation_id"]
        else:
            conv = self._repository.create_conversation(
                owner_id=user_id,
                document_id=document_id,
                title=clean_q[:60],
                project_id=project_id,
            )
            conversation_id = conv["conversation_id"]

        # 3. Retrieve relevant passages from ChromaDB
        memories = []
        try:
            store = ResearchMemoryStore(user_id=user_id, project_id=project_id)
            memories = store.search_memories(
                query=clean_q,
                k=8,
                document_id=document_id,
            )
        except Exception as exc:
            logger.warning(f"Vector search failed, using document preview fallback: {exc}")

        passages: List[Dict[str, Any]] = []
        if memories:
            for idx, mem in enumerate(memories):
                meta = mem.get("metadata") or {}
                passages.append({
                    "index": idx + 1,
                    "document_id": document_id,
                    "filename": filename,
                    "page_number": meta.get("page_number", 1),
                    "section": meta.get("section", f"Page {meta.get('page_number', 1)}"),
                    "text": mem.get("content", ""),
                    "snippet": (mem.get("content", "")[:200] + "...") if len(mem.get("content", "")) > 200 else mem.get("content", ""),
                })
        else:
            # Fallback: extract preview directly from document service
            try:
                preview = self._doc_service.get_document_preview(document_id, user_id)
                pages = preview.get("pages") or []
                if pages:
                    for idx, p in enumerate(pages[:6]):
                        ptxt = p.get("text", "")
                        if ptxt:
                            passages.append({
                                "index": idx + 1,
                                "document_id": document_id,
                                "filename": filename,
                                "page_number": p.get("page_number", idx + 1),
                                "section": f"Page {p.get('page_number', idx + 1)}",
                                "text": ptxt[:1500],
                                "snippet": ptxt[:200] + "...",
                            })
                elif preview.get("preview_text"):
                    txt = preview["preview_text"]
                    passages.append({
                        "index": 1,
                        "document_id": document_id,
                        "filename": filename,
                        "page_number": 1,
                        "section": "Document Text",
                        "text": txt[:2500],
                        "snippet": txt[:200] + "...",
                    })
            except Exception as exc:
                logger.warning(f"Fallback text extraction failed: {exc}")

        # 4. Fetch recent conversation history
        prior_messages = self._repository.get_messages(conversation_id, user_id, limit=6)

        # 5. Build prompt
        context_blocks = []
        for p in passages:
            context_blocks.append(
                f"[PASSAGE {p['index']}] (Document: '{p['filename']}', Page {p['page_number']}, Section: {p['section']}):\n{p['text']}"
            )
        context_str = "\n\n".join(context_blocks) if context_blocks else "No relevant document passages found."

        history_str = ""
        if prior_messages:
            lines = []
            for m in prior_messages[-4:]:
                lines.append(f"{m['role'].capitalize()}: {m['content']}")
            history_str = "\n".join(lines)

        user_prompt = f"""DOCUMENT CONTEXT:
========================================
{context_str}
========================================

PREVIOUS CONVERSATION CONTEXT:
{history_str or "No previous context."}

USER QUESTION:
{clean_q}

Provide a well-structured, grounded answer with bracketed citations [1], [2] referencing the source passages above:"""

        # 6. Execute completion via LLM fallback chain
        answer, provider = self._execute_chat(DOCUMENT_CHAT_SYSTEM_PROMPT, user_prompt, passages=passages)

        # 7. Identify active citations
        active_citations = []
        for p in passages:
            tag = f"[{p['index']}]"
            if tag in answer or len(passages) <= 2:
                clean_p = dict(p)
                clean_p["filename"] = os.path.basename((clean_p.get("filename") or "document").replace("\\", "/"))
                active_citations.append(clean_p)

        citations_json = json.dumps(active_citations)

        # 8. Persist turn in database
        user_msg = self._repository.add_message(
            conversation_id=conversation_id,
            owner_id=user_id,
            role="user",
            content=clean_q,
        )
        asst_msg = self._repository.add_message(
            conversation_id=conversation_id,
            owner_id=user_id,
            role="assistant",
            content=answer,
            citations_json=citations_json,
        )

        try:
            from app.infrastructure.postgres_bridge import persist_conversation, persist_message
            if conv:
                persist_conversation(conv)
            if user_msg:
                persist_message(user_msg)
            if asst_msg:
                persist_message(asst_msg)
        except Exception:
            pass

        return {
            "conversation_id": conversation_id,
            "message_id": asst_msg["message_id"],
            "answer": answer,
            "citations": active_citations,
            "provider": provider,
            "document_id": document_id,
            "document_title": filename,
            "created_at": asst_msg["created_at"],
        }

    # ── Multi-Document Comparative Chat ───────────────────────────────────

    def chat_multi_document(
        self,
        *,
        user_id: str,
        document_ids: List[str],
        question: str,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a comparative QA turn across multiple documents."""
        clean_q = (question or "").strip()
        if not clean_q:
            raise ValidationError("Question cannot be empty.")
        if not document_ids:
            raise ValidationError("At least one document ID must be specified.")

        # 1. Verify ownership of all documents
        doc_rows: Dict[str, Dict[str, Any]] = {}
        for did in document_ids:
            row = self._doc_service.get_document(did, user_id)
            if not row:
                raise NotFoundError(f"Document {did} not found or access denied.")
            doc_rows[did] = row

        first_doc_id = document_ids[0]
        primary_title = "Multi-Document Comparative Analysis"

        # 2. Conversation management
        conv = None
        if conversation_id:
            conv = self._repository.get_conversation(conversation_id, user_id)
        if not conv:
            conv = self._repository.create_conversation(
                owner_id=user_id,
                document_id=first_doc_id,
                title=clean_q[:60],
            )
            conversation_id = conv["conversation_id"]

        # 3. Retrieve chunks across all selected documents
        store = ResearchMemoryStore(user_id=user_id)
        passages: List[Dict[str, Any]] = []
        p_idx = 1

        for did, drow in doc_rows.items():
            fname = os.path.basename((drow.get("original_filename", "document")).replace("\\", "/"))
            mems = []
            try:
                mems = store.search_memories(
                    query=clean_q,
                    k=5,
                    document_id=did,
                )
            except Exception as exc:
                logger.warning(f"Multi-doc memory search failed for {did}: {exc}")
            if mems:
                for mem in mems:
                    meta = mem.get("metadata") or {}
                    passages.append({
                        "index": p_idx,
                        "document_id": did,
                        "filename": fname,
                        "page_number": meta.get("page_number", 1),
                        "section": meta.get("section", f"Page {meta.get('page_number', 1)}"),
                        "text": mem.get("content", ""),
                        "snippet": (mem.get("content", "")[:200] + "...") if len(mem.get("content", "")) > 200 else mem.get("content", ""),
                    })
                    p_idx += 1
            else:
                try:
                    prev = self._doc_service.get_document_preview(did, user_id)
                    txt = prev.get("preview_text") or ""
                    if txt:
                        passages.append({
                            "index": p_idx,
                            "document_id": did,
                            "filename": fname,
                            "page_number": 1,
                            "section": "Excerpt",
                            "text": txt[:2000],
                            "snippet": txt[:200] + "...",
                        })
                        p_idx += 1
                except Exception:
                    pass

        # 4. Build context
        context_blocks = []
        for p in passages:
            context_blocks.append(
                f"[PASSAGE {p['index']}] (Document: '{p['filename']}', Page {p['page_number']}):\n{p['text']}"
            )
        context_str = "\n\n".join(context_blocks) if context_blocks else "No passages found."

        user_prompt = f"""DOCUMENTS COMPARATIVE CONTEXT:
========================================
{context_str}
========================================

USER QUESTION:
{clean_q}

Provide a comprehensive comparative analysis with clear markdown tables and bracketed citations [1], [2]:"""

        answer, provider = self._execute_chat(MULTI_DOC_SYSTEM_PROMPT, user_prompt, passages=passages)

        active_citations = []
        for p in passages:
            if f"[{p['index']}]" in answer or len(passages) <= len(document_ids):
                clean_p = dict(p)
                clean_p["filename"] = os.path.basename((clean_p.get("filename") or "document").replace("\\", "/"))
                active_citations.append(clean_p)
        citations_json = json.dumps(active_citations)

        user_msg = self._repository.add_message(
            conversation_id=conversation_id,
            owner_id=user_id,
            role="user",
            content=clean_q,
        )
        asst_msg = self._repository.add_message(
            conversation_id=conversation_id,
            owner_id=user_id,
            role="assistant",
            content=answer,
            citations_json=citations_json,
        )

        try:
            from app.infrastructure.postgres_bridge import persist_conversation, persist_message
            if conv:
                persist_conversation(conv)
            if user_msg:
                persist_message(user_msg)
            if asst_msg:
                persist_message(asst_msg)
        except Exception:
            pass

        return {
            "conversation_id": conversation_id,
            "message_id": asst_msg["message_id"],
            "answer": answer,
            "citations": active_citations,
            "provider": provider,
            "document_ids": document_ids,
            "document_title": primary_title,
            "created_at": asst_msg["created_at"],
        }

    # ── Conversation History ──────────────────────────────────────────────

    def list_conversations(self, user_id: str, document_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._repository.list_conversations(user_id, document_id)

    def get_conversation_history(self, conversation_id: str, user_id: str) -> Dict[str, Any]:
        conv = self._repository.get_conversation(conversation_id, user_id)
        if not conv:
            raise NotFoundError("Conversation not found.")
        messages = self._repository.get_messages(conversation_id, user_id, limit=100)
        formatted_messages = []
        for m in messages:
            citations = []
            if m.get("citations_json"):
                try:
                    citations = json.loads(m["citations_json"])
                except Exception:
                    citations = []
            formatted_messages.append({
                "message_id": m["message_id"],
                "role": m["role"],
                "content": m["content"],
                "citations": citations,
                "created_at": m["created_at"],
            })
        return {
            "conversation": conv,
            "messages": formatted_messages,
        }

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        return self._repository.delete_conversation(conversation_id, user_id)

    # ── LLM Execution Helper ──────────────────────────────────────────────

    def _extractive_fallback(self, passages: List[Dict[str, Any]], user_prompt: str) -> Tuple[str, str]:
        if not passages:
            return (
                "The system was unable to reach the AI models to generate a synthesis, and no document passages were found.",
                "document-extractive",
            )
        lines = [
            "### Document Passages Summary\n",
            "*(Cloud AI model is temporarily unavailable; providing grounded analysis directly from extracted document passages)*\n",
        ]
        for p in passages[:4]:
            fn = p.get("filename", "Document")
            pg = p.get("page_number", 1)
            idx = p.get("index", 1)
            txt = p.get("text", "").strip()
            snippet = txt[:450].strip()
            if len(txt) > 450:
                snippet += "..."
            lines.append(f"**[{idx}] {fn} (Page {pg}):**\n> {snippet}\n")
        lines.append("You can ask follow-up questions or re-try to get a full generative synthesis.")
        return "\n".join(lines), "document-extractive"

    def _execute_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        passages: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[str, str]:
        """Invoke LLM chain with custom system prompt and return (reply, provider)."""
        errors = []
        for provider in self._chat_service.provider_chain():
            models_to_try: List[Optional[str]] = [None]
            if provider == "gemini":
                models_to_try = [self._settings.gemini_model]
                for fallback_m in ("gemini-2.0-flash", "gemini-1.5-flash"):
                    if fallback_m not in models_to_try:
                        models_to_try.append(fallback_m)

            for model_cand in models_to_try:
                for attempt in range(2):
                    try:
                        llm = self._chat_service._build_llm(provider, model_override=model_cand)
                        res = llm.invoke([("system", system_prompt), ("user", user_prompt)])
                        content = res.content
                        if isinstance(content, list):
                            content = "\n".join(
                                (b.get("text") if isinstance(b, dict) and b.get("text") else str(b))
                                for b in content
                            )
                        reply = str(content).strip()
                        if reply:
                            return reply, provider
                    except Exception as exc:
                        err_msg = str(exc).lower()
                        is_quota = any(
                            m in err_msg
                            for m in ("429", "resource_exhausted", "quota")
                        )
                        if is_quota:
                            break
                        is_transient = any(
                            m in err_msg
                            for m in (
                                "504", "503", "502", "deadline", "timeout", "timed out", "temporarily unavailable"
                            )
                        )
                        if is_transient and attempt == 0:
                            import time
                            time.sleep(2.0)
                            continue
                        logger.warning(f"document_chat.provider_failed: {provider} - {exc}")
                        errors.append(f"{provider}: {redact(exc)[:100]}")
                        break

        if passages:
            return self._extractive_fallback(passages, user_prompt)

        return (
            "### Document Analysis Notice\n\n"
            "Cloud AI model endpoints are currently experiencing high latency or rate limits. "
            "Please try asking your question again in a moment, or ensure the document text has been fully processed.",
            "system-fallback",
        )


__all__ = ["DocumentChatService"]
