"""
Research Knowledge Base (RAG)
==============================
Persistent vector knowledge base for the Research AI pipeline.

Uses ChromaDB as the vector store and LangChain embeddings for semantic
similarity search. All indexed documents are persisted to disk so the
knowledge base can be reused across pipeline runs.

Architecture
------------
- Embedding model : ``sentence-transformers/all-MiniLM-L6-v2`` (local, free)
  Falls back to LangChain's ``FakeEmbeddings`` if sentence-transformers is
  not installed, allowing the pipeline to run without GPU or API keys.
- Vector store    : ChromaDB — Cloud (api.trychroma.com) when CHROMA_API_KEY is set,
                   otherwise local persistent directory ``./chroma_db/``
- Collection      : One collection per research topic (slug-ified name)

Usage
-----
    from knowledge_base import ResearchKnowledgeBase

    kb = ResearchKnowledgeBase(topic="quantum computing")

    # Index search result snippets + scraped content
    kb.index_search_results(search_results_raw)   # list of dicts
    kb.index_scraped_content(scraped_text)         # raw string

    # Retrieve the top-k most relevant chunks for a query
    rag_context = kb.retrieve_for_analysis(query="quantum error correction", k=8)

    # String-formatted context ready to feed into the analyst LLM chain
    print(rag_context)
"""

from __future__ import annotations

import os
import re
import sys
import hashlib
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()

from crypto_utils import encrypt_text, decrypt_text, is_encrypted

# Default persistent directory for ChromaDB files
_DEFAULT_PERSIST_DIR = str(Path(__file__).parent / "chroma_db")

# How many characters per document chunk fed to the embedder
_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 100


# ─── Embedding Model ─────────────────────────────────────────────────────────


def _get_embeddings():
    """
    Return a LangChain-compatible embeddings object.

    Priority:
      1. HuggingFaceEmbeddings (sentence-transformers, local, best quality)
      2. FakeEmbeddings (zero-dep fallback, random vectors — for CI / testing)
    """
    try:
        from langchain_huggingface import HuggingFaceEmbeddings  # type: ignore
        console.print(
            "  [dim]🔢 Loading embedding model: all-MiniLM-L6-v2 (sentence-transformers)...[/dim]"
        )
        return HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    except Exception as e:
        console.print(f"  [yellow]⚠ HuggingFace embeddings unavailable ({e}). Using FakeEmbeddings.[/yellow]")
        from langchain_core.embeddings import FakeEmbeddings  # type: ignore
        return FakeEmbeddings(size=384)


# ─── Text Chunker ─────────────────────────────────────────────────────────────


def _chunk_text(
    text: str,
    chunk_size: int = _CHUNK_SIZE,
    overlap: int = _CHUNK_OVERLAP,
) -> List[str]:
    """
    Split *text* into overlapping chunks of approximately *chunk_size* characters.
    Chunks are split on sentence/paragraph boundaries where possible.
    """
    if not text or not text.strip():
        return []

    # Prefer splitting on paragraph breaks, then sentence ends
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) < chunk_size:
            current = current + "\n\n" + para if current else para
        else:
            if current:
                chunks.append(current.strip())
                # Keep last *overlap* chars for context continuity
                current = current[-overlap:] + "\n\n" + para if overlap else para
            else:
                # Single paragraph too long — split by sentence
                sentences = re.split(r"(?<=[.!?])\s+", para)
                for sent in sentences:
                    if len(current) + len(sent) < chunk_size:
                        current = current + " " + sent if current else sent
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = current[-overlap:] + " " + sent if overlap else sent

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if len(c.strip()) > 30]  # drop trivially short chunks


def _make_doc_id(text: str, prefix: str = "") -> str:
    """Create a short stable ID for a document chunk."""
    h = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{prefix}_{h}" if prefix else h


# ─── Knowledge Base Class ────────────────────────────────────────────────────


class ResearchKnowledgeBase:
    """
    Persistent ChromaDB vector knowledge base for one research session.

    Parameters
    ----------
    topic       : str  — Research topic (used to name the ChromaDB collection).
    persist_dir : str  — Directory for ChromaDB on-disk storage.
    embeddings  : Any  — LangChain embeddings object (auto-loaded if None).
    """

    def __init__(
        self,
        topic: str,
        persist_dir: str = _DEFAULT_PERSIST_DIR,
        embeddings: Optional[Any] = None,
    ) -> None:
        self.topic = topic
        self.persist_dir = persist_dir
        self.embeddings = embeddings or _get_embeddings()
        self._collection_name = self._make_collection_name(topic)
        self._vectorstore = None          # lazy-initialised
        self._indexed_count: int = 0

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _make_collection_name(topic: str) -> str:
        """ChromaDB collection names must be 3-63 chars, alphanumeric + hyphens."""
        slug = re.sub(r"[^a-zA-Z0-9\-]", "-", topic.lower())[:55].strip("-")
        slug = re.sub(r"-{2,}", "-", slug)
        return f"rai-{slug}" if slug else "rai-default"

    def _get_vectorstore(self):
        """Lazy-initialise the ChromaDB LangChain vectorstore.

        If CHROMA_API_KEY, CHROMA_TENANT, and CHROMA_DATABASE are all present in
        the environment the vectorstore will connect to ChromaDB Cloud.  Otherwise
        it falls back to a local on-disk database (development / offline mode).
        """
        if self._vectorstore is None:
            try:
                from langchain_chroma import Chroma  # type: ignore
            except ImportError:
                from langchain_community.vectorstores import Chroma  # type: ignore

            chroma_api_key  = os.getenv("CHROMA_API_KEY")
            chroma_tenant   = os.getenv("CHROMA_TENANT")
            chroma_database = os.getenv("CHROMA_DATABASE")

            if chroma_api_key and chroma_tenant and chroma_database:
                # ── Cloud mode ────────────────────────────────────────
                try:
                    import chromadb  # type: ignore
                    chroma_client = chromadb.CloudClient(
                        tenant=chroma_tenant,
                        database=chroma_database,
                        api_key=chroma_api_key,
                    )
                    self._vectorstore = Chroma(
                        client=chroma_client,
                        collection_name=self._collection_name,
                        embedding_function=self.embeddings,
                    )
                    console.print(
                        f"  [dim]☁️  ChromaDB Cloud collection [cyan]{self._collection_name}[/cyan] "
                        f"(tenant=[green]{chroma_tenant}[/green], db=[green]{chroma_database}[/green])[/dim]"
                    )
                except Exception as cloud_err:
                    console.print(
                        f"  [yellow]⚠️  ChromaDB Cloud failed ({cloud_err}), falling back to local.[/yellow]"
                    )
                    Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
                    self._vectorstore = Chroma(
                        collection_name=self._collection_name,
                        embedding_function=self.embeddings,
                        persist_directory=self.persist_dir,
                    )
            else:
                # ── Local fallback ────────────────────────────────────
                Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
                self._vectorstore = Chroma(
                    collection_name=self._collection_name,
                    embedding_function=self.embeddings,
                    persist_directory=self.persist_dir,
                )
                console.print(
                    f"  [dim]🗃️  ChromaDB local collection [cyan]{self._collection_name}[/cyan] "
                    f"at [dim]{self.persist_dir}[/dim][/dim]"
                )

            # Wrap similarity_search to transparently decrypt page_content
            orig_similarity_search = self._vectorstore.similarity_search

            def _decrypting_similarity_search(*args, **kwargs):
                docs = orig_similarity_search(*args, **kwargs)
                for d in docs:
                    if hasattr(d, "page_content"):
                        d.page_content = decrypt_text(d.page_content)
                return docs

            self._vectorstore.similarity_search = _decrypting_similarity_search

        return self._vectorstore

    # ── Indexing ──────────────────────────────────────────────────

    def index(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        source_label: str = "source",
    ) -> int:
        """
        Add a list of raw text strings to the vector store.

        Automatically chunks each text and assigns stable IDs.
        Returns the number of chunks actually indexed.
        """
        if not texts:
            return 0

        all_chunks: List[str] = []
        all_metas: List[Dict[str, Any]] = []
        all_ids: List[str] = []

        for i, text in enumerate(texts):
            meta_base = (metadatas[i] if metadatas and i < len(metadatas) else {})
            chunks = _chunk_text(text)
            for j, chunk in enumerate(chunks):
                cid = _make_doc_id(chunk, prefix=f"{source_label}-{i}-{j}")
                all_chunks.append(chunk)
                all_metas.append({**meta_base, "source_label": source_label, "chunk_index": j})
                all_ids.append(cid)

        if not all_chunks:
            return 0

        vs = self._get_vectorstore()
        # Add in batches of 100 to avoid ChromaDB limits
        batch_size = 100
        for start in range(0, len(all_chunks), batch_size):
            end = start + batch_size
            batch_texts = all_chunks[start:end]
            batch_metas = all_metas[start:end]
            batch_ids = all_ids[start:end]

            # 1. Compute embeddings from the original PLAINTEXT texts
            # so vector similarity search continues working accurately
            batch_embeddings = self.embeddings.embed_documents(batch_texts)

            # 2. Encrypt the customer texts with AES-256-GCM
            # so the ChromaDB dashboard displays ciphertext
            encrypted_texts = [encrypt_text(t) for t in batch_texts]

            # 3. Store ciphertext documents with plaintext embeddings into ChromaDB
            vs._collection.upsert(
                ids=batch_ids,
                documents=encrypted_texts,
                embeddings=batch_embeddings,
                metadatas=batch_metas,
            )

        self._indexed_count += len(all_chunks)
        return len(all_chunks)

    def index_search_results(self, search_results_raw: List[Dict[str, Any]]) -> int:
        """
        Index a list of search result dicts (as stored in ``ResearchState``).

        Each dict is expected to have ``title``, ``snippet``, ``url``,
        ``source_name`` fields (matching ``SearchResult.model_dump()`` format).
        """
        if not search_results_raw:
            return 0

        texts: List[str] = []
        metas: List[Dict[str, Any]] = []

        for r in search_results_raw:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            source = r.get("source_name", "unknown")
            combined = f"Title: {title}\nSource: {source}\nURL: {url}\n\n{snippet}"
            texts.append(combined)
            metas.append({"url": url, "source_name": source, "type": "search_result"})

        count = self.index(texts, metas, source_label="search")
        console.print(
            f"  [green]✅ Indexed {count} chunks from {len(search_results_raw)} search results.[/green]"
        )
        return count

    def index_scraped_content(self, scraped_text, url: str = "") -> int:
        """
        Index a raw scraped content string (output from the Reader Agent).
        Accepts a str or a list of content blocks (some LLMs return lists).
        """
        if isinstance(scraped_text, list):
            parts = []
            for block in scraped_text:
                if isinstance(block, dict):
                    parts.append(str(block.get("text") or block.get("content") or block))
                else:
                    parts.append(str(block))
            scraped_text = "\n".join(parts)
        if not scraped_text or not str(scraped_text).strip():
            return 0
        scraped_text = str(scraped_text)

        count = self.index(
            texts=[scraped_text],
            metadatas=[{"url": url, "type": "scraped_content"}],
            source_label="scrape",
        )
        console.print(
            f"  [green]✅ Indexed {count} chunks from scraped content.[/green]"
        )
        return count

    # ── Retrieval ─────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        k: int = 6,
        filter_meta: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic similarity search over all indexed documents.

        Parameters
        ----------
        query       : str  — The query to embed and match.
        k           : int  — Number of top results to return.
        filter_meta : dict — Optional ChromaDB metadata filter dict.

        Returns
        -------
        List of dicts, each containing ``content`` and ``metadata`` keys.
        """
        vs = self._get_vectorstore()
        try:
            kwargs: Dict[str, Any] = {"k": min(k, max(self._indexed_count, 1))}
            if filter_meta:
                kwargs["filter"] = filter_meta
            docs = vs.similarity_search(query, **kwargs)
            return [
                {
                    "content": decrypt_text(d.page_content),
                    "metadata": d.metadata,
                }
                for d in docs
            ]
        except Exception as exc:
            console.print(f"  [dim red]⚠ RAG retrieval error: {exc}[/dim red]")
            return []

    def retrieve_for_analysis(
        self,
        query: str,
        k: int = 8,
        max_chars: int = 5000,
    ) -> str:
        """
        Retrieve top-*k* chunks and format them as a single string for the
        analyst LLM chain.

        Parameters
        ----------
        query     : str  — Analyst's focus query.
        k         : int  — Number of chunks to retrieve.
        max_chars : int  — Hard cap on total returned string length.

        Returns
        -------
        Formatted string: ``[Chunk N — source_label]\n<content>\n\n...``
        """
        if self._indexed_count == 0:
            return ""

        results = self.retrieve(query, k=k)
        if not results:
            return ""

        parts: List[str] = []
        total = 0
        for i, doc in enumerate(results, 1):
            meta = doc.get("metadata", {})
            source = meta.get("source_name") or meta.get("source_label") or meta.get("url") or "unknown"
            chunk_text = doc["content"].strip()
            entry = f"[Chunk {i} — {source}]\n{chunk_text}"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)

        return "\n\n".join(parts)

    # ── Introspection ─────────────────────────────────────────────

    def display_stats(self) -> None:
        """Print a Rich table showing knowledge base stats."""
        vs = self._get_vectorstore()
        try:
            count = vs._collection.count()
        except Exception:
            count = self._indexed_count

        table = Table(title="🗃️  Research Knowledge Base", show_header=True)
        table.add_column("Property", style="bold cyan")
        table.add_column("Value", style="white")
        table.add_row("Collection", self._collection_name)
        table.add_row("Topic", self.topic)
        table.add_row("Persist Dir", self.persist_dir)
        table.add_row("Chunks Indexed", str(count))
        console.print(Panel(table, style="blue"))

    def reset(self) -> None:
        """Delete all documents from the collection (non-destructive to disk schema)."""
        try:
            vs = self._get_vectorstore()
            vs._collection.delete(where={"source_label": {"$ne": ""}})
            self._indexed_count = 0
            console.print("  [dim yellow]⚠ Knowledge base collection cleared.[/dim yellow]")
        except Exception as exc:
            console.print(f"  [dim red]⚠ KB reset failed: {exc}[/dim red]")


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    kb = ResearchKnowledgeBase(topic="test knowledge base")

    docs = [
        "Large language models (LLMs) are trained on massive text corpora. "
        "GPT-4 has approximately 1.76 trillion parameters according to estimates.",
        "ChromaDB is an open-source vector database that supports local persistent storage. "
        "It integrates natively with LangChain for embedding-based retrieval.",
        "Retrieval-Augmented Generation (RAG) improves LLM outputs by grounding them "
        "in retrieved documents rather than relying solely on parametric memory.",
    ]

    n = kb.index(docs, source_label="test")
    console.print(f"Indexed {n} chunks.")

    results = kb.retrieve("how do LLMs work?", k=3)
    for r in results:
        console.print(f"  → {r['content'][:120]}...")

    rag_str = kb.retrieve_for_analysis("vector databases and RAG")
    console.print("\n--- RAG Context ---")
    console.print(rag_str[:500])

    kb.display_stats()
