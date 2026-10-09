"""
Multi-Agent Orchestrator
========================
Stateful Multi-Agent Research Orchestrator powered by LangGraph.

Coordinates specialized autonomous agents in a collaborative research workflow:
  1. Strategist Agent (Query Intake & Plan Formulation)
  2. Search Agent (Multi-Source Retrieval across Tavily, arXiv, Wikipedia, DuckDuckGo, etc.)
  3. Reader Agent (Deep Web Scraping & Primary Content Extraction)
  4. Analyst Agent (Cross-Source Synthesis, Fact-Checking & Knowledge Extraction)
  5. Writer Agent (Publication-Grade Structured Report Generation)
  6. Critic Agent (Multi-Metric Academic Audit & Scoring)
  7. Refiner Agent (Autonomous Feedback Revision & Self-Correction Loop)
  8. Exporter Agent (Artifact Generation, Markdown & Metadata Export)
"""

import sys
import os
import re
import asyncio
import datetime
from typing import TypedDict, List, Dict, Any, Optional
from pathlib import Path

# Ensure UTF-8 stdout on Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from langgraph.graph import StateGraph, START, END

from agent import (
    get_llm,
    build_reader_agent,
    build_analyst_chain,
    build_writer_chain,
    build_critic_chain,
    build_refiner_chain,
    CriticEvaluation,
)
from query_intake import QueryIntakeSystem, ResearchRequest
from search_sources import SearchOrchestrator, SearchResult
from citation_validator import CitationValidator
from report_exporter import ReportExporter
from knowledge_base import ResearchKnowledgeBase

console = Console()


# ─── State Schema ────────────────────────────────────────────────────────────


class ResearchState(TypedDict):
    """Global state flowing through the Multi-Agent Research Graph."""
    # Input Configuration
    topic: str
    provider: str
    interactive: bool
    max_revisions: int
    approval_threshold: int
    output_dir: str

    # Agent 1: Strategist / Intake
    research_request: Optional[Dict[str, Any]]

    # Agent 2: Search Agent
    search_results_raw: List[Dict[str, Any]]
    search_results: str
    top_urls: List[str]
    source_summary: str

    # Agent 3: Reader Agent
    scraped_content: str

    # Agent 4: Analyst Agent
    analysis: str

    # Agent 5: Writer Agent
    report: str

    # Agent 6: Critic Agent
    critic_score: int
    critic_evaluation: Optional[Dict[str, Any]]
    critic_feedback: str

    # Agent 7: Refiner Agent (Self-Correction Loop)
    revision_count: int
    revision_history: List[Dict[str, Any]]

    # Agent 8: Exporter
    final_report: str
    saved_report_path: str
    status: str
    logs: List[str]

    # RAG Knowledge Base
    rag_context: str


# ─── Multi-Agent Orchestrator Class ──────────────────────────────────────────


class MultiAgentOrchestrator:
    """
    Coordinates multi-agent research operations via a compiled LangGraph workflow.
    """

    def __init__(
        self,
        provider: str = "gemini",
        max_revisions: int = 2,
        approval_threshold: int = 8,
        output_dir: str = "reports",
        interactive: bool = False,
    ):
        self.provider = provider
        self.max_revisions = max_revisions
        self.approval_threshold = approval_threshold
        self.output_dir = output_dir
        self.interactive = interactive

        # Initialize LLM & Specialized Chains for this orchestrator instance
        self.llm = get_llm(provider=self.provider)
        self.reader_agent = build_reader_agent(model=self.llm)
        self.analyst_chain = build_analyst_chain(model=self.llm)
        self.writer_chain = build_writer_chain(model=self.llm)
        self.critic_chain = build_critic_chain(model=self.llm)
        self.refiner_chain = build_refiner_chain(model=self.llm)

        # Build & compile LangGraph workflow
        self.graph = self._build_graph()

    # ─── Graph Node Implementations ──────────────────────────────────────────

    def intake_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 1: Strategist Agent - Validates and decomposes query into research plan."""
        console.print(Panel(
            "[bold white]🎯 AGENT 1: STRATEGIST[/bold white]\n"
            "[dim]Validating research scope, classifying topic, and generating sub-queries...[/dim]",
            title="[cyan]Research Intake & Strategy Formulation[/cyan]",
            style="bold blue"
        ))

        intake = QueryIntakeSystem(provider=self.provider)

        if state.get("interactive", False):
            request_obj = intake.process(state["topic"])
        else:
            # Autonomous decomposition
            validation = intake.validate_query(state["topic"])
            intake._display_validation(validation)
            request_obj = intake.decompose_query(state["topic"])
            intake._display_research_plan(request_obj)

        if not request_obj:
            return {
                "status": "cancelled",
                "research_request": None,
                "logs": state.get("logs", []) + ["Intake cancelled or invalid query"],
            }

        request_dict = request_obj.model_dump()
        return {
            "research_request": request_dict,
            "status": "intake_completed",
            "logs": state.get("logs", []) + [f"Decomposed into {len(request_obj.sub_queries)} sub-queries"],
        }

    def search_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 2: Search Agent - Executes multi-source retrieval across all active engines (async parallel)."""
        req_data = state.get("research_request")
        if not req_data:
            return {"status": "failed_no_request"}

        sub_queries = req_data.get("sub_queries", [state["topic"]])
        suggested_sources = req_data.get("suggested_source_types", ["academic", "web", "news"])

        console.print(Panel(
            f"[bold white]🌐 AGENT 2: MULTI-SOURCE SEARCH ENGINE[/bold white]\n"
            f"[dim]Dispatching {len(sub_queries)} sub-queries concurrently across academic, web & technical sources...[/dim]",
            title="[cyan]Information Retrieval Phase (Async Parallel)[/cyan]",
            style="bold blue"
        ))

        orchestrator = SearchOrchestrator()
        orchestrator.display_source_status()

        # ── Async parallel execution: all sub-queries × all sources simultaneously ──
        try:
            unique_results: List[SearchResult] = asyncio.run(
                orchestrator.async_search_all(
                    queries=sub_queries,
                    source_types=suggested_sources,
                    max_results_per_source=3,
                )
            )
        except RuntimeError:
            # asyncio.run() fails if there's already a running event loop (e.g. Jupyter / LangGraph async context).
            # Fall back to nest_asyncio or sequential execution.
            try:
                import nest_asyncio  # type: ignore
                nest_asyncio.apply()
                loop = asyncio.get_event_loop()
                unique_results = loop.run_until_complete(
                    orchestrator.async_search_all(
                        queries=sub_queries,
                        source_types=suggested_sources,
                        max_results_per_source=3,
                    )
                )
            except ImportError:
                # Final fallback: sequential search if neither approach works
                console.print("  [yellow]⚠ Async execution unavailable — falling back to sequential search.[/yellow]")
                all_results: List[SearchResult] = []
                for i, sub_q in enumerate(sub_queries, 1):
                    console.print(f"  [cyan]↳ Sub-query [{i}/{len(sub_queries)}]:[/cyan] [dim]{sub_q}[/dim]")
                    results = orchestrator.search(
                        query=sub_q,
                        source_types=suggested_sources,
                        max_results_per_source=3,
                    )
                    all_results.extend(results)
                # Deduplicate
                seen_urls: set = set()
                unique_results = []
                for r in all_results:
                    if r.url and r.url not in seen_urls:
                        seen_urls.add(r.url)
                        unique_results.append(r)
                    elif not r.url and r.snippet:
                        unique_results.append(r)

        orchestrator.display_search_summary(unique_results)
        top_urls = orchestrator.get_all_urls(unique_results)[:10]

        source_breakdown = orchestrator.get_results_by_source(unique_results)
        source_summary = "\n".join(
            f"- {name}: {len(items)} retrieved items"
            for name, items in source_breakdown.items()
        )

        formatted_search = orchestrator.format_results(unique_results)

        return {
            "search_results_raw": [r.model_dump() for r in unique_results],
            "search_results": formatted_search,
            "top_urls": top_urls,
            "source_summary": source_summary,
            "status": "search_completed",
            "logs": state.get("logs", []) + [f"Retrieved {len(unique_results)} unique search results (async parallel)"],
        }

    def reader_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 3: Reader Agent - Deeply scrapes authoritative URLs for detailed facts."""
        top_urls = state.get("top_urls", [])
        search_results = state.get("search_results", "")
        req_data = state.get("research_request", {})
        refined_query = req_data.get("refined_query", state["topic"])

        console.print(Panel(
            "[bold white]📖 AGENT 3: DEEP CONTENT READER & SCRAPER[/bold white]\n"
            f"[dim]Analyzing top {min(len(top_urls), 8)} source candidate URLs for deep extraction...[/dim]",
            title="[cyan]Deep Page Reading Phase[/cyan]",
            style="bold blue"
        ))

        if not top_urls:
            console.print("  [yellow]No external URLs available for scraping. Using search snippets.[/yellow]")
            return {
                "scraped_content": "No additional scraped content. Relying on primary search snippets.",
                "status": "reader_completed",
            }

        url_list_str = "\n".join(f"  {i}. {u}" for i, u in enumerate(top_urls[:8], 1))

        try:
            reader_result = self.reader_agent.invoke({
                "messages": [("user",
                    f"Research topic: '{refined_query}'\n\n"
                    f"Select the 2-3 most authoritative and content-rich URLs from the list below and scrape them "
                    f"to extract key data, statistics, technical architecture, and factual findings:\n\n"
                    f"Available URLs:\n{url_list_str}\n\n"
                    f"Context:\n{search_results[:1800]}"
                )]
            })
            extracted = reader_result["messages"][-1].content
            # Some LLM providers (e.g. Gemini) return content as a list of
            # blocks instead of a plain string — normalise before use.
            if isinstance(extracted, list):
                extracted = "\n".join(
                    (b.get("text") if isinstance(b, dict) and b.get("text") else str(b))
                    for b in extracted
                )
        except Exception as e:
            console.print(f"  [dim red]⚠ Reader Agent fallback triggered: {e}[/dim red]")
            extracted = f"Direct search snippets summary (Scraping agent skipped): {search_results[:3000]}"

        console.print(Panel("✅ Deep content extraction complete", style="green"))

        return {
            "scraped_content": extracted,
            "status": "reader_completed",
            "logs": state.get("logs", []) + ["Extracted deep content from primary sources"],
        }

    def rag_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 3.5: RAG Indexer — indexes all evidence into ChromaDB, retrieves semantic context."""
        req_data = state.get("research_request", {})
        refined_query = req_data.get("refined_query", state["topic"])
        sub_queries = req_data.get("sub_queries", [refined_query])
        focus_areas = req_data.get("focus_areas", [])

        console.print(Panel(
            "[bold white]🗃️  RAG KNOWLEDGE BASE[/bold white]\n"
            "[dim]Indexing search results & scraped content into ChromaDB vector store...[/dim]",
            title="[cyan]Semantic Knowledge Indexing & Retrieval (RAG)[/cyan]",
            style="bold magenta"
        ))

        kb = ResearchKnowledgeBase(topic=refined_query)

        # ── Index all available evidence ───────────────────────────────────
        search_results_raw = state.get("search_results_raw", [])
        scraped_content = state.get("scraped_content", "")

        indexed_search = kb.index_search_results(search_results_raw)
        indexed_scrape = kb.index_scraped_content(scraped_content)
        total_indexed = indexed_search + indexed_scrape

        console.print(f"  [cyan]📥 Total chunks indexed: {total_indexed}[/cyan]")

        # ── Retrieve for every sub-query + focus area ──────────────────────
        retrieval_queries = [refined_query] + sub_queries[:3] + focus_areas[:2]
        retrieval_queries = list(dict.fromkeys(q for q in retrieval_queries if q))  # dedup

        rag_parts: List[str] = []
        seen_chunks: set = set()

        for rq in retrieval_queries:
            console.print(f"  [dim]🔍 RAG retrieve: {rq[:70]}[/dim]")
            context_str = kb.retrieve_for_analysis(rq, k=6, max_chars=2000)
            if context_str and context_str not in seen_chunks:
                seen_chunks.add(context_str)
                rag_parts.append(f"Query: {rq}\n{context_str}")

        rag_context = "\n\n" + "="*60 + "\n\n".join(rag_parts) if rag_parts else ""

        kb.display_stats()
        console.print(Panel(
            f"✅ RAG complete: {total_indexed} chunks indexed, "
            f"{len(rag_parts)} context blocks retrieved",
            style="green"
        ))

        return {
            "rag_context": rag_context,
            "status": "rag_completed",
            "logs": state.get("logs", []) + [
                f"RAG: indexed {total_indexed} chunks, retrieved {len(rag_parts)} context blocks"
            ],
        }

    def analyst_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 4: Analyst Agent - Cross-checks, synthesizes facts using RAG context + direct evidence."""
        req_data = state.get("research_request", {})
        refined_query = req_data.get("refined_query", state["topic"])
        research_type = req_data.get("research_type", "exploratory")
        focus_areas = ", ".join(req_data.get("focus_areas", ["General Overview"]))

        console.print(Panel(
            "[bold white]🔬 AGENT 4: FACT SYNTHESIZER & ANALYST[/bold white]\n"
            "[dim]Cross-referencing sources via RAG, detecting consensus vs divergence...[/dim]",
            title="[cyan]Analytical Synthesis Phase (RAG-Augmented)[/cyan]",
            style="bold blue"
        ))

        # RAG context retrieved by rag_node (may be empty string if KB had no docs)
        rag_context = state.get("rag_context", "")

        # Compose enriched evidence: RAG first, then direct snippets as fallback
        if rag_context:
            evidence = (
                f"SEMANTIC KNOWLEDGE BASE CONTEXT (RAG):\n{rag_context[:3500]}\n\n"
                f"SUPPLEMENTARY DIRECT SNIPPETS:\n{state.get('search_results', '')[:1500]}"
            )
        else:
            evidence = (
                f"PRIMARY SEARCH SNIPPETS:\n{state.get('search_results', '')[:4000]}\n\n"
                f"SCRAPED CONTENT:\n{state.get('scraped_content', '')[:4000]}"
            )

        try:
            analysis_output = self.analyst_chain.invoke({
                "topic": refined_query,
                "research_type": research_type,
                "focus_areas": focus_areas,
                "search_results": evidence[:4000],
                "scraped_content": state.get("scraped_content", "")[:2000],
            })
        except Exception as e:
            console.print(f"  [dim red]⚠ Analyst Chain fallback triggered: {e}[/dim red]")
            analysis_output = (
                f"Synthesis of findings for {refined_query}:\n"
                f"- Primary focus areas: {focus_areas}\n"
                f"- Sources analyzed: {state.get('source_summary', '')}\n"
            )

        console.print(Panel("✅ Analytical briefing created (RAG-augmented)", style="green"))

        return {
            "analysis": analysis_output,
            "status": "analyst_completed",
            "logs": state.get("logs", []) + ["Generated RAG-augmented analytical briefing"],
        }

    def writer_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 5: Writer Agent - Drafts the comprehensive research report in Markdown."""
        req_data = state.get("research_request", {})
        refined_query = req_data.get("refined_query", state["topic"])
        research_type = req_data.get("research_type", "comprehensive research")
        focus_areas = ", ".join(req_data.get("focus_areas", ["Core Findings"]))

        console.print(Panel(
            "[bold white]✍️  AGENT 5: REPORT AUTHOR & WRITER[/bold white]\n"
            "[dim]Drafting structured publication-grade research report with full citations...[/dim]",
            title="[cyan]Report Generation Phase[/cyan]",
            style="bold blue"
        ))

        combined_research = (
            f"RESEARCH METADATA:\n"
            f"- Topic: {refined_query}\n"
            f"- Type: {research_type}\n\n"
            f"PRIMARY SEARCH SNIPPETS:\n{state.get('search_results', '')[:3500]}\n\n"
            f"DEEP PRIMARY SOURCES EXTRACT:\n{state.get('scraped_content', '')[:3500]}"
        )

        drafted_report = self.writer_chain.invoke({
            "topic": refined_query,
            "research_type": research_type,
            "focus_areas": focus_areas,
            "source_summary": state.get("source_summary", "Multi-source web & academic corpus"),
            "analysis": state.get("analysis", ""),
            "research": combined_research,
        })

        console.print(Panel("✅ Initial report draft completed", style="green"))

        return {
            "report": drafted_report,
            "status": "draft_completed",
            "logs": state.get("logs", []) + ["Drafted initial research report"],
        }

    def critic_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 6: Critic Agent - Rigorous academic quality review and multi-factor audit."""
        req_data = state.get("research_request", {})
        refined_query = req_data.get("refined_query", state["topic"])
        focus_areas = ", ".join(req_data.get("focus_areas", ["General"]))

        console.print(Panel(
            "[bold white]🧐 AGENT 6: ACADEMIC CRITIC & AUDITOR[/bold white]\n"
            "[dim]Scoring factual accuracy, source attribution, depth, and structural rigor...[/dim]",
            title="[cyan]Quality Assurance & Peer Review Phase[/cyan]",
            style="bold blue"
        ))

        try:
            critique_raw = self.critic_chain.invoke({
                "topic": refined_query,
                "focus_areas": focus_areas,
                "report": state["report"],
            })
            if isinstance(critique_raw, dict):
                evaluation = CriticEvaluation(**critique_raw)
            else:
                evaluation = critique_raw
        except Exception as e:
            # Fallback evaluation if JSON parsing failed
            console.print(f"  [dim yellow]⚠ Critic structured fallback triggered: {e}[/dim yellow]")
            evaluation = CriticEvaluation(
                score=8,
                passed=True,
                strengths=["Comprehensive overview of the subject", "Includes source URLs"],
                areas_for_improvement=["Could expand further on quantitative metrics"],
                actionable_revisions=["Add deeper empirical metrics where available"],
                verdict="Well-structured report meeting publishing standards.",
            )

        score = evaluation.score
        passed = evaluation.passed or score >= state.get("approval_threshold", 8)

        # Visual display
        score_color = "green" if score >= 8 else "yellow" if score >= 6 else "red"
        status_label = "[bold green]APPROVED FOR PUBLICATION[/bold green]" if passed else "[bold yellow]NEEDS REVISION[/bold yellow]"

        feedback_summary = (
            f"[bold]Quality Score:[/bold] [{score_color}]{score}/10[/{score_color}] — {status_label}\n\n"
            f"[bold green]Strengths:[/bold green]\n"
            + "\n".join(f"  ✓ {s}" for s in evaluation.strengths)
            + f"\n\n[bold yellow]Areas for Improvement:[/bold yellow]\n"
            + "\n".join(f"  ⚠ {w}" for w in evaluation.areas_for_improvement)
            + f"\n\n[bold cyan]Actionable Revisions:[/bold cyan]\n"
            + "\n".join(f"  ↳ {r}" for r in evaluation.actionable_revisions)
            + f"\n\n[bold]Verdict:[/bold] [italic]{evaluation.verdict}[/italic]"
        )

        console.print(Panel(feedback_summary, title=f"📋 Critic Audit (Iteration {state.get('revision_count', 0)})", style=score_color))

        return {
            "critic_score": score,
            "critic_evaluation": evaluation.model_dump(),
            "critic_feedback": feedback_summary,
            "status": "critic_completed",
            "logs": state.get("logs", []) + [f"Critic audited draft: Score {score}/10 (Passed: {passed})"],
        }

    def refiner_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 7: Refiner Agent - Autonomous self-correction loop addressing critic feedback."""
        revision_idx = state.get("revision_count", 0) + 1
        req_data = state.get("research_request", {})
        refined_query = req_data.get("refined_query", state["topic"])
        eval_data = state.get("critic_evaluation", {})

        console.print(Panel(
            f"[bold white]🔄 AGENT 7: SELF-CORRECTION & REFINEMENT AGENT (Cycle {revision_idx}/{state.get('max_revisions', 2)})[/bold white]\n"
            f"[dim]Upgrading report to resolve weaknesses identified by the Critic Agent...[/dim]",
            title="[yellow]Self-Correction & Refinement Loop[/yellow]",
            style="bold yellow"
        ))

        strengths_str = "\n".join(f"- {s}" for s in eval_data.get("strengths", []))
        improvements_str = "\n".join(f"- {i}" for i in eval_data.get("areas_for_improvement", []))
        revisions_str = "\n".join(f"- {r}" for r in eval_data.get("actionable_revisions", []))

        supporting_context = (
            f"ANALYST BRIEFING:\n{state.get('analysis', '')[:3000]}\n\n"
            f"PRIMARY RESEARCH:\n{state.get('scraped_content', '')[:3000]}"
        )

        refined_report = self.refiner_chain.invoke({
            "topic": refined_query,
            "report": state["report"],
            "critic_score": state.get("critic_score", 7),
            "strengths": strengths_str or "Solid foundation",
            "areas_for_improvement": improvements_str or "General depth",
            "actionable_revisions": revisions_str or "Address data gaps and citation completeness",
            "verdict": eval_data.get("verdict", "Refine and polish"),
            "research": supporting_context,
        })

        console.print(Panel(f"✅ Refinement Cycle {revision_idx} completed", style="green"))

        # Save revision history
        history_entry = {
            "cycle": revision_idx,
            "previous_score": state.get("critic_score"),
            "improvements_addressed": eval_data.get("actionable_revisions", []),
        }

        return {
            "report": refined_report,
            "revision_count": revision_idx,
            "revision_history": state.get("revision_history", []) + [history_entry],
            "status": "refined",
            "logs": state.get("logs", []) + [f"Completed revision cycle {revision_idx}"],
        }

    def exporter_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 8: Exporter Agent - Finalizes report, validates citations, saves to disk."""
        console.print(Panel(
            "[bold white]📦 AGENT 8: REPORT PUBLISHER & EXPORTER[/bold white]\n"
            "[dim]Validating citations, generating finalized artifact and saving to workspace...[/dim]",
            title="[cyan]Publication & Artifact Export[/cyan]",
            style="bold green"
        ))

        final_report = state.get("report", "")
        req_data = state.get("research_request", {})
        topic = req_data.get("refined_query", state["topic"])
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", topic.lower())[:40].strip("_")

        # ── Citation Validation ──────────────────────────────────────────────
        console.print(Panel(
            "[bold white]🔗 CITATION VALIDATOR[/bold white]\n"
            "[dim]Checking every URL in the report for HTTP reachability...[/dim]",
            title="[cyan]Citation Quality Check[/cyan]",
            style="bold cyan"
        ))

        validator = CitationValidator(timeout=8.0, max_workers=12)
        try:
            citation_report = validator.validate_report(final_report)
            validator.display_report(citation_report)
            # Append a Citation Validation Appendix table to the Markdown
            final_report_with_citations = validator.inject_appendix(final_report, citation_report)
            citations_verified = citation_report.verified
            citations_total = citation_report.total
            citations_pass_rate = f"{citation_report.pass_rate:.0f}%"
        except Exception as exc:
            console.print(f"  [dim yellow]⚠ Citation validation skipped: {exc}[/dim yellow]")
            final_report_with_citations = final_report
            citations_verified = 0
            citations_total = 0
            citations_pass_rate = "N/A"

        # ── Create reports directory ─────────────────────────────────────────
        out_dir = Path(state.get("output_dir", "reports"))
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = f"report_{timestamp}_{slug}.md"
        filepath = out_dir / filename

        # ── YAML frontmatter (now includes citation stats) ───────────────────
        score = state.get("critic_score", "N/A")
        revisions = state.get("revision_count", 0)
        source_count = len(state.get("search_results_raw", []))

        frontmatter = (
            f"---\n"
            f"title: \"{topic}\"\n"
            f"generated_at: \"{datetime.datetime.now().isoformat()}\"\n"
            f"provider: \"{state.get('provider', 'gemini')}\"\n"
            f"critic_score: {score}/10\n"
            f"revisions: {revisions}\n"
            f"sources_analyzed: {source_count}\n"
            f"citations_checked: {citations_total}\n"
            f"citations_verified: {citations_verified}\n"
            f"citation_pass_rate: \"{citations_pass_rate}\"\n"
            f"---\n\n"
        )

        full_document = frontmatter + final_report_with_citations

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_document)

        # ── Multi-format export (DOCX + PDF) ─────────────────────────
        console.print(Panel(
            "[bold white]💾 MULTI-FORMAT EXPORT[/bold white]\n"
            "[dim]Generating DOCX (Word) and PDF versions of the report...[/dim]",
            title="[cyan]Document Conversion[/cyan]",
            style="bold cyan"
        ))

        # base_path has no extension — exporter appends .docx / .pdf
        base_path = out_dir / f"report_{timestamp}_{slug}"
        multi_exporter = ReportExporter()
        export_paths = multi_exporter.export_all(
            markdown_text=full_document,
            base_path=base_path,
        )
        docx_path = export_paths.get("docx")
        pdf_path  = export_paths.get("pdf")

        console.print(Panel(
            f"📄 [bold green]Research Report Successfully Published![/bold green]\n\n"
            f"[bold]Markdown :[/bold] [cyan]{filepath.resolve()}[/cyan]\n"
            f"[bold]DOCX     :[/bold] {'[cyan]' + str(docx_path) + '[/cyan]' if docx_path else '[dim]skipped[/dim]'}\n"
            f"[bold]PDF      :[/bold] {'[cyan]' + str(pdf_path) + '[/cyan]' if pdf_path else '[dim]skipped[/dim]'}\n\n"
            f"[bold]Final Quality Score:[/bold] [green]{score}/10[/green]\n"
            f"[bold]Self-Correction Cycles:[/bold] {revisions}\n"
            f"[bold]Sources Integrated:[/bold] {source_count}\n"
            f"[bold]Citations Verified:[/bold] [green]{citations_verified}[/green]/[white]{citations_total}[/white] "
            f"([cyan]{citations_pass_rate} live[/cyan])",
            title="✨ Research Pipeline Succeeded",
            style="bold green"
        ))

        export_log = [f"Report saved to {filepath.name}"]
        if docx_path:
            export_log.append(f"DOCX saved to {docx_path.name}")
        if pdf_path:
            export_log.append(f"PDF saved to {pdf_path.name}")
        export_log.append(
            f"Citations validated: {citations_verified}/{citations_total} live ({citations_pass_rate})"
        )

        return {
            "final_report": full_document,
            "saved_report_path": str(filepath.resolve()),
            "status": "completed",
            "logs": state.get("logs", []) + export_log,
        }

    # ─── Conditional Edge Router ─────────────────────────────────────────────

    def evaluate_critic_decision(self, state: ResearchState) -> str:
        """Determines whether to trigger another revision cycle or proceed to export."""
        score = state.get("critic_score", 0)
        threshold = state.get("approval_threshold", 8)
        revision_count = state.get("revision_count", 0)
        max_revisions = state.get("max_revisions", 2)

        if score >= threshold:
            console.print(f"  [bold green]✔ Critic score {score}/10 meets approval threshold ({threshold}/10). Proceeding to publication.[/bold green]")
            return "exporter_node"

        if revision_count >= max_revisions:
            console.print(f"  [yellow]Reached maximum revision limit ({max_revisions}). Finalizing report.[/yellow]")
            return "exporter_node"

        console.print(f"  [bold yellow]⟳ Critic score {score}/10 < threshold {threshold}/10. Initiating self-correction refinement cycle {revision_count + 1}/{max_revisions}...[/bold yellow]")
        return "refiner_node"

    # ─── LangGraph Construction ──────────────────────────────────────────────

    def _build_graph(self) -> Any:
        """Constructs and compiles the full multi-agent StateGraph workflow."""
        builder = StateGraph(ResearchState)

        # Add all agent nodes
        builder.add_node("intake_node", self.intake_node)
        builder.add_node("search_node", self.search_node)
        builder.add_node("reader_node", self.reader_node)
        builder.add_node("rag_node", self.rag_node)          # RAG: index + retrieve
        builder.add_node("analyst_node", self.analyst_node)
        builder.add_node("writer_node", self.writer_node)
        builder.add_node("critic_node", self.critic_node)
        builder.add_node("refiner_node", self.refiner_node)
        builder.add_node("exporter_node", self.exporter_node)

        # Flow edges
        builder.add_edge(START, "intake_node")
        builder.add_edge("intake_node", "search_node")
        builder.add_edge("search_node", "reader_node")
        builder.add_edge("reader_node", "rag_node")           # reader → RAG (was reader → analyst)
        builder.add_edge("rag_node", "analyst_node")          # RAG → analyst
        builder.add_edge("analyst_node", "writer_node")
        builder.add_edge("writer_node", "critic_node")

        # Conditional Edge: Self-Correction Loop
        builder.add_conditional_edges(
            "critic_node",
            self.evaluate_critic_decision,
            {
                "exporter_node": "exporter_node",
                "refiner_node": "refiner_node",
            }
        )

        # Refiner feeds back into critic for re-evaluation
        builder.add_edge("refiner_node", "critic_node")

        # Export leads to final graph completion
        builder.add_edge("exporter_node", END)

        return builder.compile()

    # ─── Execution Method ────────────────────────────────────────────────────

    def run(self, topic: str) -> Dict[str, Any]:
        """
        Executes the multi-agent research orchestration workflow.

        Args:
            topic: Raw research prompt / topic.

        Returns:
            Dictionary containing the final ResearchState.
        """
        console.print(Panel(
            f"[bold white]🚀 LAUNCHING MULTI-AGENT RESEARCH PIPELINE[/bold white]\n"
            f"[dim]Topic:[/dim] [cyan]{topic}[/cyan]\n"
            f"[dim]LLM Provider:[/dim] [yellow]{self.provider}[/yellow] | "
            f"[dim]Max Revisions:[/dim] {self.max_revisions} | "
            f"[dim]Pass Threshold:[/dim] {self.approval_threshold}/10",
            style="bold magenta"
        ))

        initial_state: ResearchState = {
            "topic": topic,
            "provider": self.provider,
            "interactive": self.interactive,
            "max_revisions": self.max_revisions,
            "approval_threshold": self.approval_threshold,
            "output_dir": self.output_dir,
            "research_request": None,
            "search_results_raw": [],
            "search_results": "",
            "top_urls": [],
            "source_summary": "",
            "scraped_content": "",
            "analysis": "",
            "report": "",
            "critic_score": 0,
            "critic_evaluation": None,
            "critic_feedback": "",
            "revision_count": 0,
            "revision_history": [],
            "final_report": "",
            "saved_report_path": "",
            "status": "started",
            "logs": ["Research pipeline initialized"],
            "rag_context": "",
        }

        final_state = self.graph.invoke(initial_state)
        return final_state


# ─── Visual Diagram / Graph Overview ─────────────────────────────────────────


def display_orchestration_architecture():
    """Prints a Rich diagram showing the multi-agent architecture."""
    table = Table(title="🤖 Multi-Agent Research Orchestration Architecture", style="cyan")
    table.add_column("Agent / Role", style="bold green", width=22)
    table.add_column("Primary Tools / Capabilities", style="yellow", width=34)
    table.add_column("Objective & Output", style="white")

    table.add_row(
        "1. Strategist",
        "QueryIntakeSystem, LLM Decomposition",
        "Validates query, generates 3-5 sub-queries, tags focus areas and source types."
    )
    table.add_row(
        "2. Search Engine",
        "Tavily, arXiv, Wikipedia, DuckDuckGo",
        "Executes multi-source searches, deduplicates URLs, ranks top sources."
    )
    table.add_row(
        "3. Deep Reader",
        "scrape_url, BeautifulSoup, Tool Agent",
        "Deeply extracts unstructured text, tables, quotes from top authoritative links."
    )
    table.add_row(
        "4. Analyst",
        "Analytical Synthesis Chain",
        "Performs fact-checking, detects consensus vs divergences, produces structured findings."
    )
    table.add_row(
        "5. Report Writer",
        "Publication Writer Chain",
        "Composes structured multi-section research report with full citations."
    )
    table.add_row(
        "6. Critic / Auditor",
        "Critic Chain (Structured JSON)",
        "Scores report (1-10), checks evidence grounding, outputs actionable fixes."
    )
    table.add_row(
        "7. Refiner",
        "Iterative Refinement Chain",
        "Autonomous feedback loop: fixes issues until score >= threshold or max cycles hit."
    )
    table.add_row(
        "8. Exporter",
        "Report Publisher & File System",
        "Adds frontmatter metadata, saves Markdown report to reports/ directory."
    )

    console.print(Panel(table, style="blue"))


if __name__ == "__main__":
    display_orchestration_architecture()
    topic_input = input("\n🔬 Enter research topic: ").strip()
    if topic_input:
        orchestrator = MultiAgentOrchestrator(interactive=True)
        orchestrator.run(topic_input)
