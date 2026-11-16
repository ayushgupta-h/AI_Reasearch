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
import time
import functools
import uuid
from typing import TypedDict, List, Dict, Any, Optional, Iterator, Mapping
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
from langgraph.errors import NodeInterrupt

from app.orchestration.instrumentation import instrument_node
from app.observability import get_logger
from app.schemas.enums import ClaimStatus

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
from app.checkpointing import PostgresCheckpointer
from app.services.source_credibility import (
    SourceCredibilityService,
    format_credibility_appendix,
    format_credibility_context,
)
from app.services.contradiction_detection import (
    ContradictionDetectionService,
    format_contradictions_appendix,
    format_contradictions_context,
)
from app.services.claim_verification import (
    ClaimExtractor,
    EvidenceAssociator,
    ClaimVerifier,
    ExtractedClaim,
    EvidencePassage,
    VerificationResult,
)
from app.services.report_generation_service import (
    GeneratedReport,
    ReportContext,
    ReportGenerationService,
)
from app.services.research_memory_service import ResearchMemoryService

console = Console()
logger = get_logger("orchestrator")


# ─── State Schema ────────────────────────────────────────────────────────────


class ResearchState(TypedDict):
    """Global state flowing through the Multi-Agent Research Graph."""

    # ── Execution identity ─────────────────────────────────────────────────
    # Research session ID (also the LangGraph thread_id for checkpointing).
    session_id: str
    # Authenticated user / owner ID (enforces checkpoint isolation).
    user_id: str
    # Optional project container ID.
    project_id: Optional[str]

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
    source_credibility: List[Dict[str, Any]]

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

    # ── Reliability & recovery ────────────────────────────────────────────
    # Error history: each entry {node, error, attempt, timestamp}.
    errors: List[Dict[str, Any]]
    # Per-node retry counters: {node_name: attempt_count}.
    node_retry_counts: Dict[str, int]
    # Execution metadata: started_at, completed_at, token_budget_used, etc.
    execution_metadata: Dict[str, Any]
    # Token budget (0 = unlimited).
    token_budget: int
    # Execution time budget in seconds (0 = unlimited).
    execution_budget_seconds: int

    # ── Research plan review ─────────────────────────────────────────────
    # Proposed plan produced by the strategist before any research executes.
    research_plan: Optional[Dict[str, Any]]
    # pending_review | approved | changes_requested | rejected
    plan_status: str
    plan_review_comment: Optional[str]
    plan_reviewed_by: Optional[str]
    plan_reviewed_at: Optional[str]

    # ── Human interruption ───────────────────────────────────────────────
    # Whether the workflow is paused awaiting human approval.
    human_approval_required: bool
    # Which stage requires approval (e.g. "writer_node", "exporter_node").
    human_approval_stage: Optional[str]
    # Whether the user granted approval on resume.
    human_approval_granted: Optional[bool]
    # Timestamp when the workflow was paused.
    paused_at: Optional[str]
    # Opaque token presented to the client for resume.
    resume_token: Optional[str]

    # ── Claim-level evidence verification ────────────────────────────────
    # Extracted atomic claims from the research findings.
    claims: List[Dict[str, Any]]
    # Verification results for each claim.
    claim_verification_results: List[Dict[str, Any]]

    # ── Persistent research memory ───────────────────────────────────────
    # Previously retrieved relevant memories (semantic search results).
    retrieved_memories: List[Dict[str, Any]]
    # Memories stored from this run (new findings to persist).
    memories_to_store: List[Dict[str, Any]]
    # Deterministic, unresolved claim disagreements for report generation.
    contradictions: List[Dict[str, Any]]


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
        recorder: Any = None,
        event_publisher: Any = None,
        credibility_service: Optional[SourceCredibilityService] = None,
        contradiction_service: Optional[ContradictionDetectionService] = None,
        checkpointer: Optional[PostgresCheckpointer] = None,
        max_node_retries: int = 3,
        node_timeout_seconds: int = 300,
        token_budget: int = 0,
        execution_budget_seconds: int = 0,
        interrupt_before: Optional[List[str]] = None,
        require_plan_approval: bool = False,
    ):
        self.provider = provider
        self.max_revisions = max_revisions
        self.approval_threshold = approval_threshold
        self.output_dir = output_dir
        self.interactive = interactive
        # Optional execution-history recorder, supplied by the composition root.
        self.recorder = recorder
        # Optional distributed-event publisher, bound to this run by the worker.
        self.event_publisher = event_publisher
        # Explainable, deterministic source-quality evaluation. This is kept
        # injectable so callers can supply different weights/rules.
        self.credibility_service = credibility_service or SourceCredibilityService()
        self.contradiction_service = (
            contradiction_service or ContradictionDetectionService()
        )
        # The claim layer is deterministic unless a caller explicitly supplies
        # an LLM verifier. Contradiction classification itself never calls an
        # LLM, so a model cannot decide which scientific claim wins.
        self.claim_extractor = ClaimExtractor()
        self.evidence_associator = EvidenceAssociator()
        # Persistent research memory service.
        self.memory_service = ResearchMemoryService()
        # Evidence-grounded report generation service.
        self.report_generator = ReportGenerationService()
        # Persistent checkpointer for PostgreSQL-backed resume/recovery.
        self.checkpointer = checkpointer
        # Per-node retry limits.
        self.max_node_retries = max_node_retries
        # Per-node execution timeout in seconds.
        self.node_timeout_seconds = node_timeout_seconds
        # Token budget (0 = unlimited).
        self.token_budget = token_budget
        # Execution time budget in seconds (0 = unlimited).
        self.execution_budget_seconds = execution_budget_seconds
        # Nodes before which the graph should pause for human approval.
        self.interrupt_before = interrupt_before or []
        # A plan gate is only meaningful when the caller can resume from a
        # checkpoint. Without a checkpointer the gate still ends the graph
        # rather than silently running research without approval.
        self.require_plan_approval = (
            require_plan_approval
            or "plan_approval_gate_node" in self.interrupt_before
            or "search_node" in self.interrupt_before
        )

        # Initialize LLM & Specialized Chains for this orchestrator instance
        self.llm = get_llm(provider=self.provider)
        self.claim_verifier = ClaimVerifier(llm=self.llm)
        self.reader_agent = build_reader_agent(model=self.llm)
        self.analyst_chain = build_analyst_chain(model=self.llm)
        self.writer_chain = build_writer_chain(model=self.llm)
        self.critic_chain = build_critic_chain(model=self.llm)
        self.refiner_chain = build_refiner_chain(model=self.llm)

        # Build & compile LangGraph workflow
        self.graph = self._build_graph()

    # ─── Graph Node Implementations ──────────────────────────────────────────

    def _publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.event_publisher is None:
            return
        try:
            self.event_publisher.publish(event_type, payload)
        except Exception:
            # Event transport must never break research execution.
            pass

    # ─── Reliability helpers ──────────────────────────────────────────────

    def _wrap_node_with_retry(
        self, name: str, fn: Any
    ) -> Any:
        """Wrap a node function with retry, timeout, budget, and error handling.

        On failure, records the error in state and retries up to
        ``max_node_retries`` times. When retries are exhausted, returns a
        state update that routes to the failure handler instead of raising.
        """

        @functools.wraps(fn)
        def wrapper(state: ResearchState) -> Dict[str, Any]:
            # ── Budget checks ───────────────────────────────────────────
            meta = dict(state.get("execution_metadata", {}))
            started_at = meta.get("started_at")
            if started_at is None:
                started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                meta["started_at"] = started_at

            if self.execution_budget_seconds > 0 and started_at:
                try:
                    elapsed = (
                        datetime.datetime.now(datetime.timezone.utc)
                        - datetime.datetime.fromisoformat(started_at)
                    ).total_seconds()
                    if elapsed > self.execution_budget_seconds:
                        return self._record_error(
                            state, name,
                            f"Execution budget exceeded ({elapsed:.0f}s > "
                            f"{self.execution_budget_seconds}s)",
                            is_fatal=True,
                        )
                except Exception:
                    pass

            # ── Token budget check ─────────────────────────────────────
            token_used = meta.get("token_budget_used", 0)
            if self.token_budget > 0 and token_used >= self.token_budget:
                return self._record_error(
                    state, name,
                    f"Token budget exhausted ({token_used} >= {self.token_budget})",
                    is_fatal=True,
                )

            # ── Human approval gate ────────────────────────────────────
            if state.get("human_approval_required") and name in (
                "writer_node", "exporter_node",
            ):
                granted = state.get("human_approval_granted")
                if granted is not True:
                    return {
                        "status": "awaiting_human_approval",
                        "human_approval_stage": name,
                        "paused_at": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                        "resume_token": str(uuid.uuid4()),
                        "logs": state.get("logs", [])
                        + [f"Paused before {name} awaiting human approval"],
                    }

            # ── Retry loop ─────────────────────────────────────────────
            retry_counts = dict(state.get("node_retry_counts", {}))
            attempt = retry_counts.get(name, 0)
            last_exc: Optional[Exception] = None

            while attempt <= self.max_node_retries:
                try:
                    result = fn(state)
                    # Reset retry count on success
                    if name in retry_counts:
                        del retry_counts[name]
                    return result
                except NodeInterrupt:
                    # Human interruption requested — surface as awaiting state.
                    return {
                        "status": "awaiting_human_approval",
                        "human_approval_stage": name,
                        "paused_at": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                        "resume_token": str(uuid.uuid4()),
                        "logs": state.get("logs", [])
                        + [f"Interrupted before {name} for human approval"],
                    }
                except Exception as exc:
                    last_exc = exc
                    attempt += 1
                    retry_counts[name] = attempt
                    if attempt <= self.max_node_retries:
                        # Log and retry
                        console.print(
                            f"  [dim yellow]⚠ {name} attempt {attempt} failed: "
                            f"{exc}[/dim yellow]"
                        )
                        continue
                    # Exhausted — record error and route to failure handler
                    return self._record_error(
                        state, name,
                        f"Node failed after {self.max_node_retries} retries: {exc}",
                        is_fatal=True,
                    )

            # Should not reach here, but be safe
            return self._record_error(
                state, name,
                f"Node {name} failed unexpectedly",
                is_fatal=True,
            )

        return wrapper

    def _record_error(
        self, state: ResearchState, node: str, message: str, *,
        is_fatal: bool = False
    ) -> Dict[str, Any]:
        """Append an error entry to state and return a failure-state update."""
        errors = list(state.get("errors", []))
        errors.append({
            "node": node,
            "error": message,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "fatal": is_fatal,
        })
        return {
            "errors": errors,
            "status": "failed" if is_fatal else state.get("status", "running"),
            "logs": state.get("logs", [])
            + [f"Error in {node}: {message}"],
        }

    def _check_budgets(self, state: ResearchState) -> Optional[Dict[str, Any]]:
        """Return a failure update if a budget is exhausted, else None."""
        meta = state.get("execution_metadata", {})
        token_used = meta.get("token_budget_used", 0)
        if self.token_budget > 0 and token_used >= self.token_budget:
            return self._record_error(
                state, "budget_check",
                f"Token budget exhausted ({token_used} >= {self.token_budget})",
                is_fatal=True,
            )
        return None

    def failure_handler_node(self, state: ResearchState) -> Dict[str, Any]:
        """Terminal failure node: records the error and marks the run as failed."""
        errors = list(state.get("errors", []))
        fatal_errors = [e for e in errors if e.get("fatal")]
        last_error = fatal_errors[-1] if fatal_errors else (errors[-1] if errors else {})
        return {
            "status": "failed",
            "logs": state.get("logs", [])
            + [f"Workflow failed: {last_error.get('error', 'unknown error')}"],
        }

    def human_approval_gate_node(self, state: ResearchState) -> Dict[str, Any]:
        """Gate node that pauses for human approval before critical stages.

        If approval has already been granted, passes through. Otherwise,
        sets status to ``awaiting_human_approval`` and stops the graph.
        """
        stage = state.get("human_approval_stage")
        granted = state.get("human_approval_granted")
        if granted is True:
            return {
                "human_approval_required": False,
                "human_approval_stage": None,
                "human_approval_granted": None,
                "status": state.get("status", "running"),
                "logs": state.get("logs", [])
                + [f"Human approval granted for {stage}, resuming"],
            }
        return {
            "status": "awaiting_human_approval",
            "human_approval_stage": stage,
            "paused_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "resume_token": str(uuid.uuid4()),
            "logs": state.get("logs", [])
            + [f"Awaiting human approval at {stage}"],
        }

    def plan_approval_gate_node(self, state: ResearchState) -> Dict[str, Any]:
        """Gate between plan generation and any research execution.

        The graph is compiled with ``interrupt_before`` pointing at this node
        when persistent checkpointing is enabled, so the initial run stops
        here. ``resume_plan`` updates ``plan_status`` in the checkpoint before
        invoking the graph again; only an approved plan is allowed to reach
        retrieval/search.
        """
        status = str(state.get("plan_status") or "pending_review")
        if status == "approved":
            return {
                "status": "plan_approved",
                "human_approval_required": False,
                "human_approval_stage": None,
                "human_approval_granted": True,
                "logs": state.get("logs", [])
                + ["Research plan approved; starting research execution"],
            }
        return {
            "status": (
                "awaiting_plan_approval"
                if status not in ("rejected", "changes_requested")
                else status
            ),
            "human_approval_required": True,
            "human_approval_stage": "plan_approval_gate_node",
            "human_approval_granted": None,
            "paused_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "resume_token": str(uuid.uuid4()),
            "logs": state.get("logs", [])
            + [f"Research plan is {status}; awaiting human decision"],
        }

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
            "research_plan": request_dict,
            "plan_status": (
                "pending_review" if self.require_plan_approval else "approved"
            ),
            "plan_review_comment": None,
            "plan_reviewed_by": None,
            "plan_reviewed_at": None,
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

        refined_query = req_data.get("refined_query", state["topic"])
        credibility_evaluations: List[Any] = []
        credibility_by_url: Dict[str, Any] = {}
        for result in unique_results:
            try:
                evaluation = self.credibility_service.evaluate(
                    result, query=refined_query
                )
            except Exception:
                # Evaluation is advisory and must never drop a source or stop
                # retrieval. Leave the source with no score rather than guessing.
                continue
            credibility_evaluations.append(evaluation)
            if evaluation.source_url:
                credibility_by_url[evaluation.source_url] = evaluation

        raw_results: List[Dict[str, Any]] = []
        for result in unique_results:
            raw = result.model_dump(mode="json")
            evaluation = credibility_by_url.get(result.url)
            if evaluation is not None:
                raw["credibility"] = evaluation.model_dump(mode="json")
            raw_results.append(raw)

        source_breakdown = orchestrator.get_results_by_source(unique_results)
        source_summary = "\n".join(
            f"- {name}: {len(items)} retrieved items"
            for name, items in source_breakdown.items()
        )

        formatted_search = orchestrator.format_results(unique_results)
        self._publish(
            "source.discovered",
            {
                "count": len(unique_results),
                "sources": raw_results[:5],
                "credibility_evaluated": len(credibility_evaluations),
            },
        )

        return {
            "search_results_raw": raw_results,
            "search_results": formatted_search,
            "top_urls": top_urls,
            "source_summary": source_summary,
            "source_credibility": [
                evaluation.model_dump(mode="json")
                for evaluation in credibility_evaluations
            ],
            "status": "search_completed",
            "logs": state.get("logs", []) + [
                f"Retrieved {len(unique_results)} unique search results (async parallel); "
                f"evaluated credibility for {len(credibility_evaluations)}"
            ],
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

        extracted = ""
        try:
            import concurrent.futures
            import requests
            from bs4 import BeautifulSoup

            # Fast parallel scraping of top candidate URLs with strict 5-second per-URL timeout
            scraped_items: List[str] = []
            valid_urls = [u for u in top_urls if isinstance(u, str) and u.startswith("http")][:4]

            def _scrape_page(u: str) -> Optional[str]:
                try:
                    resp = requests.get(
                        u,
                        timeout=5,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                    )
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                            tag.decompose()
                        body_text = soup.get_text(separator=" ", strip=True)
                        if len(body_text) > 80:
                            return f"Source: {u}\nContent:\n{body_text[:2200]}"
                except Exception:
                    pass
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_scrape_page, u): u for u in valid_urls}
                done_futs, _ = concurrent.futures.wait(futures.keys(), timeout=9)
                for f in done_futs:
                    txt = f.result()
                    if txt:
                        scraped_items.append(txt)

            if scraped_items:
                combined_scraped = "\n\n---\n\n".join(scraped_items)
                console.print(f"  [cyan]Scraped {len(scraped_items)} URLs successfully in parallel.[/cyan]")
                # Single-pass fast LLM extraction for key facts and statistics
                try:
                    prompt = (
                        f"Research topic: '{refined_query}'\n\n"
                        f"Extract key data, technical mechanisms, statistics, and factual findings from these authoritative pages:\n\n"
                        f"{combined_scraped[:6000]}"
                    )
                    res = self.llm.invoke(prompt)
                    raw_extracted = getattr(res, "content", str(res))
                    if isinstance(raw_extracted, list):
                        extracted = "\n".join(
                            (b.get("text") if isinstance(b, dict) and b.get("text") else str(b))
                            for b in raw_extracted
                        )
                    else:
                        extracted = str(raw_extracted)
                except Exception:
                    extracted = combined_scraped[:4000]
            else:
                console.print("  [yellow]No external pages returned readable content. Using search snippets.[/yellow]")
                extracted = f"Primary search snippets summary: {search_results[:3000]}"
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

        # Claims are produced by claims_node after this synthesis step. If a
        # caller already supplied verified claims, still analyze them here so
        # direct node invocation remains useful.
        contradictions: List[Dict[str, Any]] = []
        claims = state.get("claims", [])
        if claims:
            try:
                contradictions = [
                    analysis.model_dump(mode="json")
                    for analysis in self.contradiction_service.detect(claims)
                ]
            except Exception:
                # Contradiction analysis is advisory; it must not stop report
                # generation or hide either claim/source.
                pass

        return {
            "analysis": analysis_output,
            "contradictions": contradictions,
            "status": "analyst_completed",
            "logs": state.get("logs", []) + [
                "Generated RAG-augmented analytical briefing",
                f"Detected {len(contradictions)} unresolved claim disagreement(s)",
            ],
        }

    def writer_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 5: Writer Agent - Drafts an evidence-grounded research report.

        Uses the ReportGenerationService to produce a structured Markdown
        report where every factual finding cites actual retrieved sources.
        Verification statuses from claim-level verification are honoured:
        contradicted claims are never presented as established facts,
        unverified claims are never presented as verified, and claims lacking
        sufficient evidence are explicitly flagged.

        Falls back to the LLM writer_chain when report-generation inputs are
        unavailable, preserving existing behaviour.
        """
        req_data = state.get("research_request", {})
        refined_query = req_data.get("refined_query", state["topic"])
        research_type = req_data.get("research_type", "comprehensive research")
        focus_areas = ", ".join(req_data.get("focus_areas", ["Core Findings"]))

        console.print(Panel(
            "[bold white]✍️  AGENT 5: EVIDENCE-GROUNDED REPORT AUTHOR[/bold white]\n"
            "[dim]Drafting structured report where every finding cites actual sources...[/dim]",
            title="[cyan]Evidence-Grounded Report Generation[/cyan]",
            style="bold blue"
        ))

        claims = state.get("claims", [])
        verification_results = state.get("claim_verification_results", [])
        contradictions = state.get("contradictions", [])
        credibility = state.get("source_credibility", [])
        evidence_map = state.get("evidence_map", {})
        memories = state.get("retrieved_memories", [])

        # Report-generation output (defaults for the fallback path)
        generated_citations: List[Dict[str, Any]] = []
        generated_verified = 0
        generated_unverified = 0
        generated_contradicted = 0
        generated_source_count = 0

        # Build evidence_map from claims if not already present
        if not evidence_map and claims:
            evidence_map = {}
            for claim in claims:
                cid = claim.get("claim_id", "")
                sup = claim.get("supporting_evidence", [])
                con = claim.get("contradicting_evidence", [])
                evidence_map[cid] = list(sup) + list(con)

        context = ReportContext(
            topic=refined_query,
            research_question=refined_query,
            claims=claims,
            verification_results=verification_results,
            contradictions=contradictions,
            credibility_evaluations=credibility,
            evidence_map=evidence_map,
            retrieved_memories=memories,
            critic_score=state.get("critic_score"),
            revision_count=state.get("revision_count", 0),
            max_revisions=state.get("max_revisions", self.max_revisions),
            provider=state.get("provider", self.provider),
            session_id=state.get("session_id"),
            project_id=state.get("project_id"),
            search_source_count=len(state.get("search_results_raw", [])),
            rag_available=bool(state.get("rag_context")),
        )

        try:
            # The deterministic report generator is evidence-grounded. Without
            # extracted claims/verification results there is no claim-level
            # evidence to ground a report in, so use the LLM writer fallback
            # rather than emitting an empty evidence report.
            if not claims and not verification_results:
                raise ValueError("No evidence-grounded report inputs available")
            generated = self.report_generator.generate(context)
            drafted_report = generated.content_markdown
            generated_citations = generated.citations
            generated_verified = generated.verified_claim_count
            generated_unverified = generated.unverified_claim_count
            generated_contradicted = generated.contradicted_claim_count
            generated_source_count = generated.source_count
            console.print(
                f"  [green]✅ Evidence-grounded report drafted: "
                f"{generated.word_count} words, "
                f"{generated_verified} verified, "
                f"{generated_unverified} unverified, "
                f"{generated_contradicted} contradicted claims, "
                f"{generated_source_count} sources cited.[/green]"
            )
        except Exception as exc:
            console.print(f"  [dim yellow]⚠ Report generation fell back to LLM writer: {exc}[/dim yellow]")
            # Fallback: existing LLM-based writer chain
            combined_research = (
                f"RESEARCH METADATA:\n"
                f"- Topic: {refined_query}\n"
                f"- Type: {research_type}\n\n"
                f"PRIMARY SEARCH SNIPPETS:\n{state.get('search_results', '')[:3500]}\n\n"
                f"DEEP PRIMARY SOURCES EXTRACT:\n{state.get('scraped_content', '')[:3500]}"
            )
            credibility_context = format_credibility_context(
                state.get("source_credibility", [])
            )
            if credibility_context:
                combined_research += "\n\n" + credibility_context
            contradictions_context = format_contradictions_context(
                state.get("contradictions", [])
            )
            if contradictions_context:
                combined_research += "\n\n" + contradictions_context

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
            "report_citations": generated_citations,
            "report_verified_count": generated_verified,
            "report_unverified_count": generated_unverified,
            "report_contradicted_count": generated_contradicted,
            "report_source_count": generated_source_count,
            "logs": state.get("logs", []) + ["Drafted evidence-grounded research report"],
        }

    def critic_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 6: Critic Agent - Rigorous academic quality review and multi-factor audit.

        Evaluates the report for:
        - Evidence support (no contradicted claims presented as facts)
        - Citation correctness (every citation traces to an actual source)
        - Logical consistency
        - Contradictions documented transparently
        - Missing sections
        - Unsupported conclusions
        - Research question coverage
        """
        req_data = state.get("research_request", {})
        refined_query = req_data.get("refined_query", state["topic"])
        focus_areas = ", ".join(req_data.get("focus_areas", ["General"]))

        console.print(Panel(
            "[bold white]🧐 AGENT 6: ACADEMIC CRITIC & AUDITOR[/bold white]\n"
            "[dim]Scoring factual accuracy, source attribution, depth, and structural rigor...[/dim]",
            title="[cyan]Quality Assurance & Peer Review Phase[/cyan]",
            style="bold blue"
        ))

        # ── Evidence-based pre-audit (deterministic, never fabricates) ────
        # Only applies deductions when there is actual claim/evidence data to
        # evaluate. A minimal or empty report (e.g. in unit tests) is not
        # penalized for missing sections it was never asked to produce.
        claims = state.get("claims", [])
        verification_map = {
            vr.get("claim_id"): vr for vr in state.get("claim_verification_results", [])
        }
        contradictions = state.get("contradictions", [])
        citations = state.get("report_citations", [])

        audit_deductions = 0
        audit_issues: List[str] = []
        audit_strengths: List[str] = []

        has_claims = bool(claims)

        # Check for contradicted claims presented as findings
        contradicted_presented = [
            c for c in claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.CONTRADICTED.value
        ]
        if contradicted_presented:
            audit_deductions += 1
            audit_issues.append(
                f"{len(contradicted_presented)} contradicted claim(s) appear "
                f"in the report; these must not be presented as established facts."
            )
        elif has_claims:
            audit_strengths.append("No contradicted claims presented as established findings.")

        # Check for unverified claims presented as verified
        unverified_presented = [
            c for c in claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            in (ClaimStatus.INSUFFICIENT_CONTEXT.value, ClaimStatus.UNVERIFIED.value)
        ]
        if unverified_presented:
            audit_deductions += 1
            audit_issues.append(
                f"{len(unverified_presented)} claim(s) lack sufficient evidence "
                f"and are flagged as unverified."
            )
        elif has_claims:
            audit_strengths.append("All claims carry verification status.")

        # Check citation traceability (only when claims exist)
        uncited = [
            c for c in claims
            if not verification_map.get(c.get("claim_id", ""), {}).get("supporting_evidence_ids")
            and not verification_map.get(c.get("claim_id", ""), {}).get("contradicting_evidence_ids")
        ]
        if uncited:
            audit_deductions += 1
            audit_issues.append(
                f"{len(uncited)} claim(s) have no traceable evidence citations."
            )
        elif has_claims:
            audit_strengths.append("All claims reference traceable evidence.")

        # Check contradictions documented
        if contradictions:
            audit_strengths.append(
                f"{len(contradictions)} contradiction(s) documented transparently."
            )
        elif has_claims:
            audit_strengths.append("No contradictions detected among verified claims.")

        # Check report sections present (only when claims exist)
        if has_claims:
            report = state.get("report", "")
            required_sections = [
                "Executive Summary",
                "Research Question",
                "Methodology",
                "Key Findings",
                "Conclusion",
                "References",
            ]
            missing_sections = [
                s for s in required_sections
                if s.lower() not in report.lower()
            ]
            if missing_sections:
                audit_deductions += min(len(missing_sections), 2)
                audit_issues.append(
                    f"Missing report section(s): {', '.join(missing_sections)}."
                )
            else:
                audit_strengths.append("All required report sections present.")

        # Research question coverage (only when claims exist)
        if has_claims:
            report = state.get("report", "")
            refined_query = req_data.get("refined_query", state["topic"])
            if refined_query.lower() in report.lower() or "research question" in report.lower():
                audit_strengths.append("Research question is addressed in the report.")
            else:
                audit_deductions += 1
                audit_issues.append("Research question coverage is unclear.")

        # ── LLM critic invocation ─────────────────────────────────────────
        try:
            critique_raw = self.critic_chain.invoke({
                "topic": refined_query,
                "focus_areas": focus_areas,
                "report": state["report"],
                "evidence_audit_issues": "; ".join(audit_issues) if audit_issues else "None",
                "evidence_audit_strengths": "; ".join(audit_strengths) if audit_strengths else "None",
            })
            if isinstance(critique_raw, dict):
                evaluation = CriticEvaluation(**critique_raw)
            else:
                evaluation = critique_raw
        except Exception as e:
            # Fallback evaluation if JSON parsing failed
            console.print(f"  [dim yellow]⚠ Critic structured fallback triggered: {e}[/dim yellow]")
            evaluation = CriticEvaluation(
                score=max(1, min(10, 8 - audit_deductions)),
                passed=audit_deductions == 0,
                strengths=audit_strengths or ["Comprehensive overview of the subject", "Includes source URLs"],
                areas_for_improvement=audit_issues or ["Could expand further on quantitative metrics"],
                actionable_revisions=audit_issues or ["Add deeper empirical metrics where available"],
                verdict="Evidence-audited report review.",
            )

        # Apply evidence-based adjustments to the LLM score
        llm_score = evaluation.score
        # Cap evidence deductions so they inform but never override the LLM
        # assessment entirely; the LLM score remains the primary signal.
        capped_deductions = min(audit_deductions, 3)
        score = max(0, min(10, llm_score - capped_deductions))
        passed = evaluation.passed or score >= state.get("approval_threshold", 8)

        # Visual display
        score_color = "green" if score >= 8 else "yellow" if score >= 6 else "red"
        status_label = "[bold green]APPROVED FOR PUBLICATION[/bold green]" if passed else "[bold yellow]NEEDS REVISION[/bold yellow]"

        feedback_summary = (
            f"[bold]Quality Score:[/bold] [{score_color}]{score}/10[/{score_color}] — {status_label}\n\n"
            f"[bold green]Strengths:[/bold green]\n"
            + "\n".join(f"  ✓ {s}" for s in (evaluation.strengths or audit_strengths))
            + f"\n\n[bold yellow]Areas for Improvement:[/bold yellow]\n"
            + "\n".join(f"  ⚠ {w}" for w in (evaluation.areas_for_improvement or audit_issues))
            + f"\n\n[bold cyan]Actionable Revisions:[/bold cyan]\n"
            + "\n".join(f"  ↳ {r}" for r in (evaluation.actionable_revisions or audit_issues))
            + f"\n\n[bold]Evidence Audit:[/bold] {audit_deductions} deduction(s) applied.\n"
            + f"\n\n[bold]Verdict:[/bold] [italic]{evaluation.verdict}[/italic]"
        )

        console.print(Panel(feedback_summary, title=f"📋 Critic Audit (Iteration {state.get('revision_count', 0)})", style=score_color))

        if not passed and state.get("revision_count", 0) < state.get("max_revisions", self.max_revisions):
            self._publish(
                "review.required",
                {
                    "critic_score": score,
                    "approval_threshold": state.get("approval_threshold", self.approval_threshold),
                    "revision_count": state.get("revision_count", 0),
                },
            )

        return {
            "critic_score": score,
            "critic_evaluation": evaluation.model_dump(),
            "critic_feedback": feedback_summary,
            "status": "critic_completed",
            "logs": state.get("logs", []) + [f"Critic audited draft: Score {score}/10 (Passed: {passed}), evidence deductions: {audit_deductions}"],
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

        # Evidence context for the refiner: contradictions, unverified claims, citation gaps
        verification_map = {
            vr.get("claim_id"): vr for vr in state.get("claim_verification_results", [])
        }
        unverified_claims = [
            c.get("text", c.get("claim_text", ""))
            for c in state.get("claims", [])
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            in (ClaimStatus.INSUFFICIENT_CONTEXT.value, ClaimStatus.UNVERIFIED.value)
        ]
        contradicted_claims = [
            c.get("text", c.get("claim_text", ""))
            for c in state.get("claims", [])
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.CONTRADICTED.value
        ]
        evidence_context = (
            f"ANALYST BRIEFING:\n{state.get('analysis', '')[:3000]}\n\n"
            f"PRIMARY RESEARCH:\n{state.get('scraped_content', '')[:3000]}\n\n"
            f"UNVERIFIED CLAIMS (do not present as facts):\n"
            + "\n".join(f"- {c}" for c in unverified_claims[:5])
            + ("\n" if unverified_claims else "")
            + f"CONTRADICTED CLAIMS (do not present as established):\n"
            + "\n".join(f"- {c}" for c in contradicted_claims[:5])
            + ("\n" if contradicted_claims else "")
            + f"CRITIC FEEDBACK:\n{eval_data.get('verdict', 'Refine and polish')}\n"
        )

        refined_report = self.refiner_chain.invoke({
            "topic": refined_query,
            "report": state["report"],
            "critic_score": state.get("critic_score", 7),
            "strengths": strengths_str or "Solid foundation",
            "areas_for_improvement": improvements_str or "General depth",
            "actionable_revisions": revisions_str or "Address data gaps and citation completeness",
            "verdict": eval_data.get("verdict", "Refine and polish"),
            "research": evidence_context,
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

        # Credibility is advisory: append the explanation rather than removing
        # or rejecting any source from the report.
        credibility_appendix = format_credibility_appendix(
            state.get("source_credibility", [])
        )
        if credibility_appendix:
            final_report_with_citations += credibility_appendix
        contradictions_appendix = format_contradictions_appendix(
            state.get("contradictions", [])
        )
        if contradictions_appendix:
            final_report_with_citations += contradictions_appendix

        # ── Create reports directory ─────────────────────────────────────────
        out_dir = Path(state.get("output_dir", "reports"))
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = f"report_{timestamp}_{slug}.md"
        filepath = out_dir / filename

        # ── YAML frontmatter (now includes citation stats) ───────────────────
        score = state.get("critic_score", "N/A")
        revisions = state.get("revision_count", 0)
        source_count = len(state.get("search_results_raw", []))
        credibility_count = len(state.get("source_credibility", []))
        contradiction_count = len(state.get("contradictions", []))

        frontmatter = (
            f"---\n"
            f"title: \"{topic}\"\n"
            f"generated_at: \"{datetime.datetime.now().isoformat()}\"\n"
            f"provider: \"{state.get('provider', 'gemini')}\"\n"
            f"critic_score: {score}/10\n"
            f"revisions: {revisions}\n"
            f"sources_analyzed: {source_count}\n"
            f"source_credibility_evaluated: {credibility_count}\n"
            f"contradictions_detected: {contradiction_count}\n"
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
        self._publish(
            "report.generated",
            {
                "saved_report_path": str(filepath.resolve()),
                "critic_score": score,
                "citations_verified": citations_verified,
                "citations_total": citations_total,
            },
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

    def evaluate_refiner_decision(self, state: ResearchState) -> str:
        """Determines whether to trigger another critic evaluation or proceed to export."""
        revision_count = state.get("revision_count", 0)
        max_revisions = state.get("max_revisions", self.max_revisions)
        if revision_count >= max_revisions:
            console.print(f"  [yellow]Reached maximum revision limit ({max_revisions}). Finalizing report.[/yellow]")
            return "exporter_node"
        return "critic_node"

    # ─── Claim Verification Node ──────────────────────────────────────────

    def claims_node(self, state: ResearchState) -> Dict[str, Any]:
        """
        Node: Claim-Level Evidence Verification.

        Extracts atomic factual claims from the analyst's synthesis, associates
        each claim with retrieved evidence passages, and verifies every claim
        against its evidence. Verified claims and their evidence are stored in
        state for the writer to cite and for downstream persistence.
        """
        console.print(Panel(
            "[bold white]🔍 CLAIM-LEVEL EVIDENCE VERIFICATION[/bold white]\n"
            "[dim]Extracting atomic claims and verifying each against retrieved evidence...[/dim]",
            title="[cyan]Claim Verification[/cyan]",
            style="bold cyan"
        ))

        topic = state.get("topic", "")
        analysis = state.get("analysis", "")
        search_results = state.get("search_results_raw", [])
        rag_context = state.get("rag_context", "")

        # Build a combined text from analysis + RAG context + search snippets
        source_texts: List[str] = []
        if analysis:
            source_texts.append(analysis)
        if rag_context:
            source_texts.append(rag_context)
        for sr in search_results:
            snippet = sr.get("snippet", "") if isinstance(sr, dict) else ""
            if snippet:
                source_texts.append(snippet)

        combined_text = "\n\n".join(t for t in source_texts if t.strip())
        if not combined_text:
            return {
                "claims": [],
                "claim_verification_results": [],
                "contradictions": [],
                "logs": state.get("logs", [])
                + ["Claim verification skipped: no source text available"],
                "status": state.get("status", "running"),
            }

        # ── Step 1: Extract atomic claims ────────────────────────────────
        extracted = self.claim_extractor.extract(
            combined_text,
            session_id=state.get("session_id"),
            source_refs=search_results,
        )

        # ── Step 2: Build evidence passages from search results ─────────
        evidence_passages_raw: List[Mapping[str, Any]] = []
        for sr in search_results:
            if not isinstance(sr, dict):
                continue
            snippet = sr.get("snippet", "")
            url = sr.get("url", "")
            title = sr.get("title", sr.get("source_name", ""))
            if snippet:
                evidence_passages_raw.append({
                    "quote": snippet,
                    "url": url,
                    "title": title,
                    "source_name": sr.get("source_name", ""),
                    "source_id": sr.get("source_id"),
                })

        # Also add RAG context as evidence
        if rag_context:
            evidence_passages_raw.append({
                "quote": rag_context[:2000],
                "url": "",
                "title": "RAG Context",
                "source_name": "knowledge_base",
            })

        # ── Step 3: Associate evidence with claims ──────────────────────
        evidence_associations = self.evidence_associator.associate(
            extracted, evidence_passages_raw
        )

        # Build evidence map keyed by claim_id for verification
        evidence_map: Dict[str, List[EvidencePassage]] = {}
        for ep in evidence_associations:
            cid = ep.claim_id
            if cid:
                evidence_map.setdefault(cid, []).append(ep)

        # ── Step 4: Verify each claim against its evidence ──────────────
        verification_results = self.claim_verifier.verify_all(
            extracted, evidence_map
        )

        # Convert to serializable state dicts, retaining each claim's
        # verification status and both evidence sets for contradiction reports.
        verification_by_id = {r.claim_id: r for r in verification_results}
        claims_state = []
        for claim in extracted:
            evidence = evidence_map.get(claim.claim_id, [])
            verification = verification_by_id.get(claim.claim_id)
            claims_state.append(
                {
                    "claim_id": claim.claim_id,
                    "text": claim.text,
                    "subject": topic,
                    "research_question": (
                        state.get("research_request", {}) or {}
                    ).get("refined_query") or topic,
                    "conclusion": claim.text,
                    "claim_type": claim.claim_type,
                    "confidence": claim.confidence,
                    "status": verification.status if verification else "unverified",
                    "evidence_ids": [item.evidence_id for item in evidence],
                    "supporting_evidence": [
                        {
                            "evidence_id": item.evidence_id,
                            "source_id": item.source_id,
                            "quote": item.quote,
                            "url": item.source_url,
                            "stance": item.stance,
                            "strength": item.strength,
                        }
                        for item in evidence
                        if item.stance == "supports"
                    ],
                    "contradicting_evidence": [
                        {
                            "evidence_id": item.evidence_id,
                            "source_id": item.source_id,
                            "quote": item.quote,
                            "url": item.source_url,
                            "stance": item.stance,
                            "strength": item.strength,
                        }
                        for item in evidence
                        if item.stance == "contradicts"
                    ],
                }
            )

        contradictions: List[Dict[str, Any]] = []
        if claims_state:
            try:
                contradictions = [
                    analysis.model_dump(mode="json")
                    for analysis in self.contradiction_service.detect(claims_state)
                ]
            except Exception:
                # A contradiction failure must never discard claims, evidence,
                # or stop report generation.
                logger.exception("claims.contradiction_detection_failed")

        results_state = [
            {
                "claim_id": r.claim_id,
                "claim_text": r.claim_text,
                "status": r.status,
                "confidence": r.confidence,
                "reasoning": r.reasoning,
                "supporting_evidence_ids": r.supporting_evidence_ids,
                "contradicting_evidence_ids": r.contradicting_evidence_ids,
                "evidence_quotes": r.evidence_quotes,
            }
            for r in verification_results
        ]

        # Count statuses for logging
        status_counts: Dict[str, int] = {}
        for r in verification_results:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1

        console.print(
            f"  [green]✅ Verified {len(verification_results)} claim(s): "
            + ", ".join(f"{k}={v}" for k, v in status_counts.items())
            + ".[/green]"
        )

        return {
            "claims": claims_state,
            "claim_verification_results": results_state,
            "contradictions": contradictions,
            "logs": state.get("logs", [])
            + [
                f"Claim verification complete: {len(verification_results)} claims verified",
                f"Detected {len(contradictions)} unresolved claim disagreement(s)",
            ],
            "status": state.get("status", "running"),
        }

    # ─── Memory Retrieval Node ──────────────────────────────────────────

    def memory_retrieval_node(self, state: ResearchState) -> Dict[str, Any]:
        """
        Node: Persistent Research Memory Retrieval.

        Runs before search so agents can reuse verified findings from previous
        research sessions. Results are strictly owner-scoped — a user can only
        retrieve their own memory. Memory supplements fresh research; it never
        replaces live search when current information is required.
        """
        console.print(Panel(
            "[bold white]🧠 PERSISTENT RESEARCH MEMORY RETRIEVAL[/bold white]\n"
            "[dim]Searching previous findings relevant to this topic...[/dim]",
            title="[cyan]Memory Retrieval[/cyan]",
            style="bold cyan"
        ))

        topic = state.get("topic", "")
        user_id = state.get("user_id", "anonymous")
        session_id = state.get("session_id")
        project_id = state.get("project_id")

        if not topic or not user_id or user_id == "anonymous":
            return {
                "retrieved_memories": [],
                "memories_to_store": [],
                "logs": state.get("logs", [])
                + ["Memory retrieval skipped: no topic or authenticated user"],
                "status": state.get("status", "running"),
            }

        try:
            memories = self.memory_service.retrieve_memories(
                user_id=user_id,
                query=topic,
                k=6,
                project_id=project_id,
                session_id=session_id,
                verified_only=False,
            )
        except Exception as exc:
            logger.exception("memory.retrieval_failed")
            return {
                "retrieved_memories": [],
                "memories_to_store": [],
                "logs": state.get("logs", [])
                + [f"Memory retrieval skipped: {exc}"],
                "status": state.get("status", "running"),
            }

        memories_state = [
            {
                "id": m.id,
                "content": m.content_text,
                "content_type": m.content_type,
                "verification_status": m.verification_status,
                "confidence": m.confidence,
                "source_id": m.source_id,
                "project_id": m.project_id,
                "session_id": m.session_id,
                "created_at": m.created_at,
            }
            for m in memories
        ]

        verified_count = sum(
            1 for m in memories if m.verification_status == "verified"
        )

        console.print(
            f"  [green]✅ Retrieved {len(memories)} memory record(s) "
            f"({verified_count} verified).[/green]"
        )

        return {
            "retrieved_memories": memories_state,
            "memories_to_store": [],
            "logs": state.get("logs", [])
            + [f"Memory retrieval complete: {len(memories)} records found"],
            "status": state.get("status", "running"),
        }

    # ─── LangGraph Construction ──────────────────────────────────────────────

    def _build_graph(self) -> Any:
        """Constructs and compiles the full multi-agent StateGraph workflow."""
        builder = StateGraph(ResearchState)

        # Wrap each agent node with retry, timeout, budget, and error handling.
        # Instrumentation wraps the retried node so stage logging still fires.
        agent_nodes = {
            "intake_node": self.intake_node,
            "memory_retrieval_node": self.memory_retrieval_node,
            "search_node": self.search_node,
            "reader_node": self.reader_node,
            "rag_node": self.rag_node,
            "analyst_node": self.analyst_node,
            "claims_node": self.claims_node,
            "writer_node": self.writer_node,
            "critic_node": self.critic_node,
            "refiner_node": self.refiner_node,
            "exporter_node": self.exporter_node,
        }
        for name, fn in agent_nodes.items():
            retried = self._wrap_node_with_retry(name, fn)
            instrumented = instrument_node(name, retried, self.recorder, self.event_publisher)
            builder.add_node(name, instrumented)

        # Reliability nodes.
        builder.add_node(
            "failure_handler_node",
            instrument_node(
                "failure_handler",
                self.failure_handler_node,
                self.recorder,
                self.event_publisher,
            ),
        )
        builder.add_node(
            "human_approval_gate_node",
            instrument_node(
                "human_approval_gate",
                self.human_approval_gate_node,
                self.recorder,
                self.event_publisher,
            ),
        )
        builder.add_node(
            "plan_approval_gate_node",
            instrument_node(
                "plan_approval_gate",
                self.plan_approval_gate_node,
                self.recorder,
                self.event_publisher,
            ),
        )

        # Flow edges. The plan gate is before memory/search so no research
        # execution happens until a human has approved the proposed plan.
        builder.add_edge(START, "intake_node")
        if self.require_plan_approval:
            builder.add_edge("intake_node", "plan_approval_gate_node")
            builder.add_conditional_edges(
                "plan_approval_gate_node",
                lambda state: (
                    "memory_retrieval_node"
                    if state.get("plan_status") == "approved"
                    else "END"
                ),
                {"memory_retrieval_node": "memory_retrieval_node", "END": END},
            )
        else:
            builder.add_edge("intake_node", "memory_retrieval_node")
        builder.add_edge("memory_retrieval_node", "search_node")
        builder.add_edge("search_node", "reader_node")
        builder.add_edge("reader_node", "rag_node")           # reader → RAG
        builder.add_edge("rag_node", "analyst_node")          # RAG → analyst
        builder.add_edge("analyst_node", "claims_node")       # analyst → claims

        # Human approval gate before writer (critical stage).
        if "writer_node" in self.interrupt_before:
            builder.add_edge("claims_node", "human_approval_gate_node")
            builder.add_conditional_edges(
                "human_approval_gate_node",
                lambda state: (
                    "writer_node"
                    if state.get("human_approval_granted") is True
                    else "END"
                ),
                {"writer_node": "writer_node", "END": END},
            )
        else:
            builder.add_edge("claims_node", "writer_node")

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

        # Refiner feeds into exporter when max revisions reached, otherwise re-evaluates with critic
        builder.add_conditional_edges(
            "refiner_node",
            self.evaluate_refiner_decision,
            {
                "exporter_node": "exporter_node",
                "critic_node": "critic_node",
            }
        )

        # Export leads to final graph completion
        builder.add_edge("exporter_node", END)

        # Compile with checkpointer when available (persistent resume support).
        # For a plan gate, interrupt *before* the gate so the checkpoint holds
        # the completed intake state. A resumed graph then executes the gate
        # with the reviewer's updated plan_status.
        if self.checkpointer is not None:
            interrupts = (
                ["plan_approval_gate_node"]
                if self.require_plan_approval
                else None
            )
            return builder.compile(
                checkpointer=self.checkpointer,
                interrupt_before=interrupts,
            )
        return builder.compile()

    # ─── Execution Method ────────────────────────────────────────────────────

    def run(
        self,
        topic: str,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        resume_from: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes the multi-agent research orchestration workflow.

        Args:
            topic: Raw research prompt / topic.
            session_id: Research session ID (used as LangGraph thread_id for
                checkpointing/resume). Defaults to a fresh UUID.
            user_id: Authenticated user/owner ID (enforces checkpoint isolation).
            project_id: Optional project container ID.
            config: LangGraph runtime config (e.g. ``{"configurable": {
                "thread_id": ..., "owner_id": ...}}``) for checkpoint-backed
                resume. When provided, the graph resumes from the last saved
                checkpoint.
            resume_from: Optional prior state dict to resume from (used when
                no checkpointer is configured but the caller wants to restart
                from a known state).

        Returns:
            Dictionary containing the final ResearchState.
        """
        session_id = session_id or str(uuid.uuid4())
        user_id = user_id or "anonymous"
        project_id = project_id or None

        started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if resume_from is not None:
            initial_state: ResearchState = dict(resume_from)
            # Preserve identity fields
            initial_state["session_id"] = session_id
            initial_state["user_id"] = user_id
            if project_id:
                initial_state["project_id"] = project_id
        else:
            initial_state: ResearchState = {
                "session_id": session_id,
                "user_id": user_id,
                "project_id": project_id,
                # Input Configuration
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
                "source_credibility": [],
                "claims": [],
                "claim_verification_results": [],
                "contradictions": [],
                "retrieved_memories": [],
                "memories_to_store": [],
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
                # Reliability & recovery
                "errors": [],
                "node_retry_counts": {},
                "execution_metadata": {},
                "token_budget": self.token_budget,
                "execution_budget_seconds": self.execution_budget_seconds,
                # Human interruption
                "human_approval_required": False,
                "human_approval_stage": None,
                "human_approval_granted": None,
                "paused_at": None,
                "resume_token": None,
                # Research plan approval
                "research_plan": None,
                "plan_status": (
                    "pending_review" if self.require_plan_approval else "approved"
                ),
                "plan_review_comment": None,
                "plan_reviewed_by": None,
                "plan_reviewed_at": None,
            }
            initial_state["execution_metadata"]["started_at"] = started_at

        # Build LangGraph runtime config for checkpointing
        runtime_config: Dict[str, Any] = {
            "configurable": {
                "thread_id": session_id,
                "owner_id": user_id,
            }
        }
        if config:
            runtime_config["configurable"].update(config.get("configurable", {}))

        console.print(Panel(
            f"[bold white]🚀 LAUNCHING MULTI-AGENT RESEARCH PIPELINE[/bold white]\n"
            f"[dim]Topic:[/dim] [cyan]{topic}[/cyan]\n"
            f"[dim]Session:[/dim] {session_id} | "
            f"[dim]User:[/dim] {user_id}\n"
            f"[dim]LLM Provider:[/dim] [yellow]{self.provider}[/yellow] | "
            f"[dim]Max Revisions:[/dim] {self.max_revisions} | "
            f"[dim]Pass Threshold:[/dim] {self.approval_threshold}/10",
            style="bold magenta"
        ))

        if self.checkpointer is not None:
            # Checkpoint-backed run: resume from last saved state if present.
            final_state = self.graph.invoke(initial_state, config=runtime_config)
        else:
            final_state = self.graph.invoke(initial_state)

        # LangGraph exposes static interrupts through the checkpoint's pending
        # ``next`` nodes (and, in some versions, ``__interrupt__``). The HTTP/SSE
        # caller needs an explicit paused status, not "started".
        interrupted = isinstance(final_state, Mapping) and bool(
            final_state.get("__interrupt__")
        )
        if self.checkpointer is not None:
            try:
                snapshot = self.graph.get_state(runtime_config)
                pending = tuple(getattr(snapshot, "next", ()) or ())
                interrupted = interrupted or "plan_approval_gate_node" in pending
            except Exception:
                logger.debug("orchestrator.interrupt_state_unavailable")
        if interrupted:
            final_state = dict(final_state)
            final_state["status"] = "awaiting_plan_approval"
            final_state["plan_status"] = "pending_review"
            final_state["human_approval_required"] = True
            final_state["human_approval_stage"] = "plan_approval_gate_node"
            final_state["paused_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
        return dict(final_state)

    def resume_plan(
        self,
        *,
        session_id: str,
        user_id: str,
        decision: Any,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resume an interrupted plan-review checkpoint with a human decision.

        Decisions are validated and written into the checkpoint *before* the
        graph runs again. A rejected/change-requested plan routes to END; an
        approved plan continues into memory retrieval and search.
        """
        if self.checkpointer is None:
            raise ValueError(
                "Plan review requires PostgreSQL checkpointing to resume."
            )
        decision_value = str(getattr(decision, "value", decision)).strip().lower()
        allowed = {"approved", "changes_requested", "rejected"}
        if decision_value not in allowed:
            raise ValueError("decision must be approved, changes_requested, or rejected")
        if decision_value != "approved" and not (comment or "").strip():
            raise ValueError("changes_requested and rejected decisions require a comment")

        runtime_config: Dict[str, Any] = {
            "configurable": {"thread_id": session_id, "owner_id": user_id}
        }
        self.graph.update_state(
            runtime_config,
            {
                "plan_status": decision_value,
                "plan_review_comment": comment,
                "plan_reviewed_by": user_id,
                "plan_reviewed_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                "human_approval_required": True,
                "human_approval_granted": decision_value == "approved",
                "human_approval_stage": "plan_approval_gate_node",
            },
        )
        final_state = self.graph.invoke(None, config=runtime_config)
        return dict(final_state) if isinstance(final_state, Mapping) else dict(final_state)


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
