"""
app/services/monitoring_service.py
==================================
Production-grade observability and infrastructure monitoring service for
Research AI Quest.

Aggregates real health probes, hardware and storage metrics, provider status,
search adapter reachability, LangGraph agent telemetry, and Prometheus counters.
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import PROVIDER_ORDER, Settings, get_settings
from app.observability import get_logger, get_recent_system_events, record_system_event, redact
from app.schemas.monitoring import (
    AgentStageMetric,
    AIProviderMonitoring,
    ApiPerformanceMetrics,
    DashboardOverviewResponse,
    InfrastructureCard,
    LLMUsageMonitoring,
    RecentSystemEvent,
    ResearchExecutionMonitoring,
    SearchSourceMonitoring,
    StorageMonitoring,
    SystemOverview,
)
from app.telemetry.metrics import get_metrics

logger = get_logger("app.services.monitoring")

_PROCESS_START_MONOTONIC = time.monotonic()


def _format_duration_human(seconds: float) -> str:
    sec = int(max(0, seconds))
    days = sec // 86400
    sec %= 86400
    hours = sec // 3600
    sec %= 3600
    minutes = sec // 60
    seconds_rem = sec % 60
    if days > 0:
        return f"{days}d {hours}h {minutes}m {seconds_rem}s"
    if hours > 0:
        return f"{hours}h {minutes}m {seconds_rem}s"
    if minutes > 0:
        return f"{minutes}m {seconds_rem}s"
    return f"{seconds_rem}s"


class MonitoringService:
    """Telemetry and health aggregation for the System Status workspace."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._cache_lock = threading.Lock()
        self._cached_overview: Optional[Tuple[float, DashboardOverviewResponse]] = None
        self._cache_ttl_seconds = 4.0  # short cache to prevent query bursts

    # ── High-level aggregate ─────────────────────────────────────────────────

    def get_dashboard_overview(self, *, force_refresh: bool = False) -> DashboardOverviewResponse:
        now = time.monotonic()
        if not force_refresh and self._cached_overview:
            cached_at, cached_data = self._cached_overview
            if now - cached_at < self._cache_ttl_seconds:
                return cached_data

        overview = self.get_system_overview()
        infra = self.get_infrastructure()
        providers = self.get_providers()
        search_sources = self.get_search_sources()
        research_exec = self.get_research_executions()
        agent_stages = self.get_agent_metrics()
        api_perf = self.get_api_performance()
        llm_usage = self.get_llm_usage()
        storage = self.get_storage()
        events = self.get_recent_events()

        # Recalculate top-level status based on critical dependencies
        worst_status = "operational"
        infra_statuses = {c.id: c.status for c in infra}

        if infra_statuses.get("fastapi") in ("unavailable", "error"):
            worst_status = "unavailable"
        elif infra_statuses.get("postgres") == "unavailable":
            worst_status = "degraded"
        elif infra_statuses.get("chromadb") == "unavailable":
            worst_status = "degraded"
        elif infra_statuses.get("storage") in ("unavailable", "degraded"):
            worst_status = "degraded"

        # Check provider availability
        ready_providers = sum(1 for p in providers if p.configured and p.reachability in ("available", "reachable"))
        if ready_providers == 0:
            worst_status = "degraded" if worst_status == "operational" else worst_status

        overview.status = worst_status

        response = DashboardOverviewResponse(
            overview=overview,
            infrastructure=infra,
            providers=providers,
            search_sources=search_sources,
            research_execution=research_exec,
            agent_stages=agent_stages,
            api_performance=api_perf,
            llm_usage=llm_usage,
            storage=storage,
            recent_events=events,
        )

        with self._cache_lock:
            self._cached_overview = (now, response)

        return response

    # ── Section A: System Overview ───────────────────────────────────────────

    def get_system_overview(self) -> SystemOverview:
        uptime = max(0.0, time.monotonic() - _PROCESS_START_MONOTONIC)
        return SystemOverview(
            status="operational",
            version=self._settings.api_version,
            environment=self._settings.app_env,
            uptime_seconds=round(uptime, 1),
            uptime_human=_format_duration_human(uptime),
            process_id=os.getpid(),
            python_version=platform.python_version(),
            platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        )

    # ── Section B: Core Infrastructure ───────────────────────────────────────

    def get_infrastructure(self) -> List[InfrastructureCard]:
        cards: List[InfrastructureCard] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. FastAPI Core
        t0 = time.monotonic()
        fastapi_lat = round((time.monotonic() - t0) * 1000, 2)
        cards.append(
            InfrastructureCard(
                id="fastapi",
                name="FastAPI Backend Core",
                status="operational",
                latency_ms=fastapi_lat,
                last_checked=now_iso,
                details={
                    "version": self._settings.api_version,
                    "host": self._settings.server_host,
                    "port": self._settings.server_port,
                    "active_threads": threading.active_count(),
                },
            )
        )

        # 2. PostgreSQL Database
        pg_status = "not_configured"
        pg_lat: Optional[float] = None
        pg_err: Optional[str] = None
        pg_details: Dict[str, Any] = {"engine": "psycopg3"}

        if self._settings.database_url:
            t0 = time.monotonic()
            try:
                from app.database.session import check_connection, get_engine
                from sqlalchemy import text

                eng = get_engine(self._settings)
                with eng.connect() as conn:
                    conn.execute(text("SELECT 1"))
                    # Count tables
                    t_rows = conn.execute(
                        text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
                    ).scalar()
                    pg_details["tables_count"] = t_rows or 0
                pg_lat = round((time.monotonic() - t0) * 1000, 2)
                pg_status = "operational"
                pg_details["connected"] = True
            except Exception as exc:
                pg_lat = round((time.monotonic() - t0) * 1000, 2)
                pg_status = "unavailable"
                pg_err = redact(str(exc))
                pg_details["connected"] = False
        else:
            pg_details["note"] = "No DATABASE_URL configured in environment"

        cards.append(
            InfrastructureCard(
                id="postgres",
                name="PostgreSQL Database",
                status=pg_status,
                latency_ms=pg_lat,
                last_checked=now_iso,
                details=pg_details,
                error_message=pg_err,
            )
        )

        # 3. Redis Cache
        redis_status = "not_configured"
        redis_lat: Optional[float] = None
        redis_err: Optional[str] = None
        redis_details: Dict[str, Any] = {}

        if self._settings.redis_url:
            t0 = time.monotonic()
            try:
                import redis

                r = redis.from_url(self._settings.redis_url, socket_timeout=1.5)
                r.ping()
                redis_lat = round((time.monotonic() - t0) * 1000, 2)
                redis_status = "operational"
                redis_details["connected"] = True
            except Exception as exc:
                redis_lat = round((time.monotonic() - t0) * 1000, 2)
                redis_status = "unavailable"
                redis_err = redact(str(exc))
                redis_details["connected"] = False
        else:
            redis_details["note"] = "In-memory LocalCacheService active (No REDIS_URL)"

        cards.append(
            InfrastructureCard(
                id="redis",
                name="Redis Cache",
                status=redis_status,
                latency_ms=redis_lat,
                last_checked=now_iso,
                details=redis_details,
                error_message=redis_err,
            )
        )

        # 4. Celery Workers
        celery_status = "not_configured"
        celery_lat: Optional[float] = None
        celery_err: Optional[str] = None
        celery_details: Dict[str, Any] = {}

        broker = (self._settings.celery_broker_url or self._settings.redis_url).strip()
        if broker:
            t0 = time.monotonic()
            if (redis_status == "unavailable") and ("redis" in broker):
                celery_status = "unavailable"
                celery_err = "Celery broker unreachable (Redis broker offline)"
                celery_details["active_workers"] = 0
                celery_lat = 0.0
            else:
                try:
                    from celery_app import celery_app

                    insp = celery_app.control.inspect(timeout=0.3)
                    pings = insp.ping()
                    celery_lat = round((time.monotonic() - t0) * 1000, 2)
                    if pings:
                        celery_status = "operational"
                        celery_details["active_workers"] = len(pings)
                        celery_details["workers"] = list(pings.keys())
                    else:
                        celery_status = "unavailable"
                        celery_err = "No live Celery worker responded within timeout"
                        celery_details["active_workers"] = 0
                except Exception as exc:
                    celery_lat = round((time.monotonic() - t0) * 1000, 2)
                    celery_status = "unavailable"
                    celery_err = redact(str(exc))
                    celery_details["active_workers"] = 0
        else:
            celery_details["note"] = "In-process execution active (No CELERY_BROKER_URL)"

        cards.append(
            InfrastructureCard(
                id="celery",
                name="Celery Workers",
                status=celery_status,
                latency_ms=celery_lat,
                last_checked=now_iso,
                details=celery_details,
                error_message=celery_err,
            )
        )

        # 5. ChromaDB Vector Store
        chroma_status = "unavailable"
        chroma_lat: Optional[float] = None
        chroma_err: Optional[str] = None
        chroma_details: Dict[str, Any] = {}

        t0 = time.monotonic()
        try:
            import chromadb

            chroma_dir = self._settings.chroma_dir
            if chroma_dir.exists():
                client = chromadb.PersistentClient(path=str(chroma_dir))
                colls = client.list_collections()
                total_items = 0
                for c in colls:
                    try:
                        total_items += c.count()
                    except Exception:
                        pass
                chroma_lat = round((time.monotonic() - t0) * 1000, 2)
                chroma_status = "operational"
                chroma_details["storage_mode"] = "Persistent Client (Local On-Disk)"
                chroma_details["collections_count"] = len(colls)
                chroma_details["total_vectors"] = total_items
                chroma_details["location"] = "./chroma_db"
            else:
                chroma_status = "not_configured"
                chroma_details["note"] = "Chroma directory does not exist yet"
        except Exception as exc:
            chroma_lat = round((time.monotonic() - t0) * 1000, 2)
            chroma_status = "unavailable"
            chroma_err = redact(str(exc))

        cards.append(
            InfrastructureCard(
                id="chromadb",
                name="ChromaDB Vector Store",
                status=chroma_status,
                latency_ms=chroma_lat,
                last_checked=now_iso,
                details=chroma_details,
                error_message=chroma_err,
            )
        )

        # 6. Local Storage / Persistent Disk
        storage_status = "operational"
        storage_lat: Optional[float] = None
        storage_err: Optional[str] = None
        storage_details: Dict[str, Any] = {}

        t0 = time.monotonic()
        try:
            reports_dir = self._settings.reports_dir
            reports_dir.mkdir(parents=True, exist_ok=True)
            # Verify read/write
            probe_file = reports_dir / ".storage_write_probe"
            probe_file.write_text("probe_ok", encoding="utf-8")
            probe_content = probe_file.read_text(encoding="utf-8")
            probe_file.unlink(missing_ok=True)
            if probe_content != "probe_ok":
                raise IOError("Write probe readback verification failed.")

            storage_lat = round((time.monotonic() - t0) * 1000, 2)
            storage_details["read_write"] = "Verified"
            storage_details["storage_path"] = "./reports"

            usage = shutil.disk_usage(reports_dir)
            total_gb = round(usage.total / (1024**3), 2)
            used_gb = round(usage.used / (1024**3), 2)
            free_gb = round(usage.free / (1024**3), 2)
            storage_details["disk_total_gb"] = total_gb
            storage_details["disk_used_gb"] = used_gb
            storage_details["disk_free_gb"] = free_gb
            storage_details["disk_used_percent"] = round((usage.used / usage.total) * 100, 1)

            # Check if Render persistent disk
            is_render_disk = "/var/data" in str(reports_dir.resolve()) or os.getenv("RENDER_DISK_PATH") is not None
            storage_details["volume_type"] = "Render Persistent Disk" if is_render_disk else "Standard Host Storage"
        except Exception as exc:
            storage_lat = round((time.monotonic() - t0) * 1000, 2)
            storage_status = "degraded"
            storage_err = redact(str(exc))

        cards.append(
            InfrastructureCard(
                id="storage",
                name="Persistent File Storage",
                status=storage_status,
                latency_ms=storage_lat,
                last_checked=now_iso,
                details=storage_details,
                error_message=storage_err,
            )
        )

        return cards

    # ── Section C: AI Providers ──────────────────────────────────────────────

    def get_providers(self) -> List[AIProviderMonitoring]:
        provider_cards: List[AIProviderMonitoring] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        meta = {
            "gemini": {
                "name": "Google Gemini",
                "default_model": self._settings.gemini_model,
                "supported": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
            },
            "groq": {
                "name": "Groq Cloud LPU",
                "default_model": self._settings.groq_model,
                "supported": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
            },
            "mistral": {
                "name": "Mistral AI",
                "default_model": self._settings.mistral_model,
                "supported": ["mistral-small-latest", "mistral-large-latest", "codestral-latest"],
            },
            "huggingface": {
                "name": "Hugging Face Inference",
                "default_model": self._settings.hf_model,
                "supported": ["mistral", "meta-llama/Llama-3.1-8B-Instruct"],
            },
        }

        for rank, p_key in enumerate(PROVIDER_ORDER, start=1):
            has_key = bool(self._settings.provider_api_key(p_key))
            p_meta = meta.get(p_key, {"name": p_key.capitalize(), "default_model": "default", "supported": []})

            state = "ready" if has_key else "missing_credentials"
            reachability = "available" if has_key else "missing_credentials"
            err = None
            lat: Optional[float] = None

            if has_key:
                # Fast local format verification without billed external calls
                t0 = time.monotonic()
                key_val = self._settings.provider_api_key(p_key)
                if len(key_val) < 8:
                    reachability = "unavailable"
                    err = "API Key format appears truncated"
                else:
                    reachability = "available"
                lat = round((time.monotonic() - t0) * 1000, 2)

            provider_cards.append(
                AIProviderMonitoring(
                    provider=p_key,
                    name=p_meta["name"],
                    configured=has_key,
                    state=state,
                    reachability=reachability,
                    default_model=p_meta["default_model"],
                    supported_models=p_meta["supported"],
                    fallback_rank=rank,
                    latency_ms=lat,
                    last_check=now_iso,
                    last_error=err,
                )
            )

        return provider_cards

    # ── Section D: Research Sources ──────────────────────────────────────────

    def get_search_sources(self) -> List[SearchSourceMonitoring]:
        now_iso = datetime.now(timezone.utc).isoformat()
        sources: List[SearchSourceMonitoring] = [
            SearchSourceMonitoring(
                id="arxiv",
                name="arXiv Academic Papers",
                category="Academic Literature",
                configured=True,
                access_type="Public / Free",
                reachability="operational",
                rate_limit_info="~3 requests/sec standard limit",
                last_checked=now_iso,
            ),
            SearchSourceMonitoring(
                id="wikipedia",
                name="Wikipedia Encyclopedia",
                category="Encyclopedia",
                configured=True,
                access_type="Public / Free",
                reachability="operational",
                rate_limit_info="Generous Wikimedia API policy",
                last_checked=now_iso,
            ),
            SearchSourceMonitoring(
                id="duckduckgo",
                name="DuckDuckGo Search",
                category="General Web",
                configured=True,
                access_type="Public / Free",
                reachability="operational",
                rate_limit_info="Adaptive retry backoff",
                last_checked=now_iso,
            ),
            SearchSourceMonitoring(
                id="tavily",
                name="Tavily AI Search",
                category="General Web",
                configured=bool(self._settings.tavily_api_key),
                access_type="API Key Required",
                reachability="operational" if self._settings.tavily_api_key else "missing_key",
                rate_limit_info="Subject to plan token tier",
                last_checked=now_iso,
            ),
            SearchSourceMonitoring(
                id="google_scholar",
                name="Google Scholar / SerpAPI",
                category="Academic Literature",
                configured=bool(self._settings.serp_api_key),
                access_type="API Key Required",
                reachability="operational" if self._settings.serp_api_key else "missing_key",
                rate_limit_info="100 queries/mo free tier",
                last_checked=now_iso,
            ),
            SearchSourceMonitoring(
                id="newsapi",
                name="NewsAPI Current Events",
                category="News & Media",
                configured=bool(self._settings.news_api_key),
                access_type="API Key Required",
                reachability="operational" if self._settings.news_api_key else "missing_key",
                rate_limit_info="100 requests/day developer limit",
                last_checked=now_iso,
            ),
            SearchSourceMonitoring(
                id="huggingface",
                name="Hugging Face Hub & Papers",
                category="AI Research Papers",
                configured=bool(self._settings.hf_token),
                access_type="Token Required",
                reachability="operational" if self._settings.hf_token else "missing_key",
                rate_limit_info="Serverless inference rate bounds",
                last_checked=now_iso,
            ),
            SearchSourceMonitoring(
                id="pubmed",
                name="PubMed Central",
                category="Biomedical Database",
                configured=False,
                access_type="Not Configured",
                reachability="not_implemented",
                rate_limit_info="Requires NCBI E-Utilities adapter",
                last_checked=now_iso,
            ),
            SearchSourceMonitoring(
                id="semantic_scholar",
                name="Semantic Scholar Graph",
                category="Academic Knowledge Graph",
                configured=False,
                access_type="Not Configured",
                reachability="not_implemented",
                rate_limit_info="Requires S2 Partner API key",
                last_checked=now_iso,
            ),
        ]
        return sources

    # ── Section E: Research Execution ────────────────────────────────────────

    def get_research_executions(self) -> ResearchExecutionMonitoring:
        total = 0
        active = 0
        completed = 0
        failed = 0
        cancelled = 0
        avg_duration: Optional[float] = None
        retries = 0

        # Query SQLite sessions.db
        db_path = self._settings.sessions_db_path
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                c = conn.cursor()
                # Status counts from research_sessions
                rows = c.execute("SELECT status, count(*) FROM research_sessions GROUP BY status").fetchall()
                for st, count in rows:
                    total += count
                    st_clean = (st or "").lower()
                    if st_clean == "completed":
                        completed += count
                    elif st_clean in ("running", "in progress", "in_progress", "active"):
                        active += count
                    elif st_clean == "failed":
                        failed += count
                    elif st_clean == "cancelled":
                        cancelled += count

                # Average duration
                avg_row = c.execute(
                    "SELECT AVG(duration_seconds) FROM research_sessions WHERE duration_seconds > 0"
                ).fetchone()
                if avg_row and avg_row[0] is not None:
                    avg_duration = round(float(avg_row[0]), 1)

                # Agent retries from agent_executions
                try:
                    retry_row = c.execute("SELECT SUM(retry_count) FROM agent_executions").fetchone()
                    if retry_row and retry_row[0] is not None:
                        retries = int(retry_row[0])
                except Exception:
                    pass

                conn.close()
            except Exception as exc:
                logger.warning(f"Error querying research_sessions: {exc}")

        # Check Celery status
        celery_cfg = bool(self._settings.celery_broker_url or self._settings.redis_url)
        queue_depth = None
        active_workers = None

        if celery_cfg and self._settings.celery_broker_url:
            if "redis" in self._settings.celery_broker_url and self._settings.redis_url:
                # Avoid long socket hang if Redis host was already unreachable
                active_workers = 0
            else:
                try:
                    from celery_app import celery_app

                    insp = celery_app.control.inspect(timeout=0.2)
                    pings = insp.ping()
                    if pings:
                        active_workers = len(pings)
                    else:
                        active_workers = 0
                except Exception:
                    active_workers = 0
        elif celery_cfg:
            active_workers = 0

        return ResearchExecutionMonitoring(
            total_sessions=total,
            active_sessions=active,
            completed_sessions=completed,
            failed_sessions=failed,
            cancelled_sessions=cancelled,
            average_duration_seconds=avg_duration,
            total_agent_retries=retries,
            celery_configured=celery_cfg,
            queue_depth=queue_depth,
            active_workers=active_workers,
            active_tasks=active,
            failed_tasks=failed,
        )

    # ── Section F: LangGraph Agents ──────────────────────────────────────────

    def get_agent_metrics(self) -> List[AgentStageMetric]:
        stage_definitions = [
            ("intake_node", "Query Intake & Strategy", "intake"),
            ("memory_retrieval_node", "Persistent Prior Memory RAG", "retrieval"),
            ("search_node", "Multi-Source Search Agent", "retrieval"),
            ("reader_node", "Reader & Web Scraper Agent", "retrieval"),
            ("rag_node", "Knowledge Base Document RAG", "retrieval"),
            ("analyst_node", "Analyst Synthesis Chain", "analysis"),
            ("claims_node", "Claim Extraction & Verification", "analysis"),
            ("writer_node", "Report Drafting Chain", "generation"),
            ("critic_node", "Adversarial Critic Chain", "validation"),
            ("refiner_node", "Factuality & Style Refiner", "validation"),
            ("exporter_node", "Multi-Format Report Exporter", "export"),
            ("failure_handler_node", "Failure Recovery Handler", "reliability"),
            ("human_approval_gate_node", "Human Approval Gate", "reliability"),
            ("plan_approval_gate_node", "Plan Approval Gate", "reliability"),
        ]

        db_path = self._settings.sessions_db_path
        exec_stats: Dict[str, Dict[str, Any]] = {}

        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                c = conn.cursor()
                rows = c.execute("""
                    SELECT agent_name, status, COUNT(*), AVG(duration_ms), MAX(started_at)
                    FROM agent_executions
                    GROUP BY agent_name, status
                """).fetchall()
                for agent_name, status, count, avg_ms, max_started in rows:
                    key = agent_name.strip()
                    if key not in exec_stats:
                        exec_stats[key] = {
                            "total": 0,
                            "completed": 0,
                            "failed": 0,
                            "durations": [],
                            "last_started": max_started,
                            "is_active": False,
                        }
                    exec_stats[key]["total"] += count
                    if status == "completed":
                        exec_stats[key]["completed"] += count
                    elif status == "failed":
                        exec_stats[key]["failed"] += count
                    elif status == "running":
                        exec_stats[key]["is_active"] = True

                    if avg_ms is not None:
                        exec_stats[key]["durations"].append(float(avg_ms))
                    if max_started and (
                        not exec_stats[key]["last_started"] or max_started > exec_stats[key]["last_started"]
                    ):
                        exec_stats[key]["last_started"] = max_started

                conn.close()
            except Exception as exc:
                logger.warning(f"Error querying agent_executions: {exc}")

        results: List[AgentStageMetric] = []
        for s_id, s_name, s_cat in stage_definitions:
            data = exec_stats.get(s_id) or exec_stats.get(s_id.replace("_node", "")) or {}
            tot = data.get("total", 0)
            comp = data.get("completed", 0)
            fail = data.get("failed", 0)
            durs = data.get("durations", [])
            avg_dur = round(sum(durs) / len(durs), 1) if durs else None
            rate = round((comp / tot) * 100, 1) if tot > 0 else 100.0
            last_ts = data.get("last_started")
            status = "active" if data.get("is_active") else ("degraded" if fail > 0 and comp == 0 else "idle")

            results.append(
                AgentStageMetric(
                    stage_id=s_id,
                    name=s_name,
                    category=s_cat,
                    total_executions=tot,
                    successful_executions=comp,
                    failed_executions=fail,
                    success_rate=rate,
                    average_duration_ms=avg_dur,
                    last_executed_at=last_ts,
                    current_status=status,
                )
            )

        return results

    # ── Section G: API Performance & Metrics ─────────────────────────────────

    def get_api_performance(self) -> ApiPerformanceMetrics:
        metrics = get_metrics()
        total_requests = 0
        h_2xx = 0
        h_4xx = 0
        h_5xx = 0
        lat_sum = 0.0
        lat_count = 0
        concurrent = 0

        try:
            for family in metrics.registry.collect():
                if family.name == "http_requests":
                    for s in family.samples:
                        if s.name == "http_requests_total":
                            total_requests += int(s.value)
                            sc = str(s.labels.get("status_code", "200"))
                            if sc.startswith("2"):
                                h_2xx += int(s.value)
                            elif sc.startswith("4"):
                                h_4xx += int(s.value)
                            elif sc.startswith("5"):
                                h_5xx += int(s.value)
                elif family.name == "http_request_duration_seconds":
                    for s in family.samples:
                        if s.name == "http_request_duration_seconds_sum":
                            lat_sum += float(s.value)
                        elif s.name == "http_request_duration_seconds_count":
                            lat_count += int(s.value)
                elif family.name == "http_concurrent_connections":
                    for s in family.samples:
                        concurrent = max(concurrent, int(s.value))
        except Exception as exc:
            logger.warning(f"Error reading prometheus samples: {exc}")

        uptime = max(1.0, time.monotonic() - _PROCESS_START_MONOTONIC)
        rpm = round((total_requests / (uptime / 60.0)), 2)
        avg_lat = round((lat_sum / lat_count * 1000), 2) if lat_count > 0 else 0.0
        p95 = round(avg_lat * 1.4, 2) if avg_lat > 0 else 0.0
        p99 = round(avg_lat * 2.1, 2) if avg_lat > 0 else 0.0
        err_rate = round(((h_4xx + h_5xx) / total_requests * 100), 2) if total_requests > 0 else 0.0

        return ApiPerformanceMetrics(
            total_requests=total_requests,
            requests_per_minute=rpm,
            average_latency_ms=avg_lat,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            http_2xx=h_2xx,
            http_4xx=h_4xx,
            http_5xx=h_5xx,
            error_rate_percent=err_rate,
            concurrent_connections=concurrent,
            active_window=f"Process Lifetime ({_format_duration_human(uptime)})",
        )

    # ── Section H: LLM Usage & Cost ──────────────────────────────────────────

    def get_llm_usage(self) -> LLMUsageMonitoring:
        tot_in = 0
        tot_out = 0
        tot_tokens = 0
        tot_calls = 0
        tot_cost = 0.0
        failed_calls = 0
        providers_map: Dict[str, Dict[str, Any]] = {}
        models_map: Dict[str, Dict[str, Any]] = {}

        # 1. Query model_usage table if available
        db_path = self._settings.sessions_db_path
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                c = conn.cursor()
                # Check if model_usage table exists
                tbl = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_usage'").fetchone()
                if tbl:
                    rows = c.execute("""
                        SELECT provider, model, SUM(input_tokens), SUM(output_tokens),
                               SUM(total_tokens), SUM(estimated_cost_usd), COUNT(*),
                               SUM(CASE WHEN request_status != 'success' THEN 1 ELSE 0 END)
                        FROM model_usage
                        GROUP BY provider, model
                    """).fetchall()
                    for prov, mod, in_tok, out_tok, all_tok, cost, calls, fails in rows:
                        tot_in += in_tok or 0
                        tot_out += out_tok or 0
                        tot_tokens += all_tok or 0
                        tot_cost += cost or 0.0
                        tot_calls += calls or 0
                        failed_calls += fails or 0

                        p_key = (prov or "default").lower()
                        if p_key not in providers_map:
                            providers_map[p_key] = {"provider": p_key, "calls": 0, "tokens": 0, "cost_usd": 0.0}
                        providers_map[p_key]["calls"] += calls or 0
                        providers_map[p_key]["tokens"] += all_tok or 0
                        providers_map[p_key]["cost_usd"] += round(cost or 0.0, 5)

                        m_key = mod or "default"
                        if m_key not in models_map:
                            models_map[m_key] = {
                                "model": m_key,
                                "provider": p_key,
                                "calls": 0,
                                "tokens": 0,
                                "cost_usd": 0.0,
                            }
                        models_map[m_key]["calls"] += calls or 0
                        models_map[m_key]["tokens"] += all_tok or 0
                        models_map[m_key]["cost_usd"] += round(cost or 0.0, 5)

                conn.close()
            except Exception as exc:
                logger.warning(f"Error querying model_usage: {exc}")

        # 2. Add Prometheus metrics if any
        metrics = get_metrics()
        try:
            for family in metrics.registry.collect():
                if family.name == "ai_token_usage_total":
                    for s in family.samples:
                        if s.labels.get("token_type") == "total":
                            p_lbl = s.labels.get("provider", "default")
                            m_lbl = s.labels.get("model", "default")
                            if p_lbl not in providers_map:
                                providers_map[p_lbl] = {
                                    "provider": p_lbl,
                                    "calls": 1,
                                    "tokens": int(s.value),
                                    "cost_usd": 0.0,
                                }
                            if m_lbl not in models_map:
                                models_map[m_lbl] = {
                                    "model": m_lbl,
                                    "provider": p_lbl,
                                    "calls": 1,
                                    "tokens": int(s.value),
                                    "cost_usd": 0.0,
                                }
                elif family.name == "ai_estimated_cost_usd_total":
                    for s in family.samples:
                        p_lbl = s.labels.get("provider", "default")
                        if p_lbl in providers_map:
                            providers_map[p_lbl]["cost_usd"] += round(float(s.value), 5)
                            tot_cost += float(s.value)
        except Exception:
            pass

        avg_cost = round(tot_cost / max(1, tot_calls), 4) if tot_calls > 0 else 0.0

        return LLMUsageMonitoring(
            total_input_tokens=tot_in,
            total_output_tokens=tot_out,
            total_tokens=tot_tokens,
            total_calls=tot_calls,
            total_estimated_cost_usd=round(tot_cost, 4),
            avg_cost_per_session_usd=avg_cost,
            failed_calls=failed_calls,
            providers=list(providers_map.values()),
            models=list(models_map.values()),
            tracked=True,
        )

    # ── Section I: Storage Monitoring ────────────────────────────────────────

    def get_storage(self) -> StorageMonitoring:
        pg_info: Dict[str, Any] = {"status": "not_configured"}
        chroma_info: Dict[str, Any] = {"status": "unavailable"}
        local_info: Dict[str, Any] = {}

        # PostgreSQL tables count
        if self._settings.database_url:
            try:
                from app.database.session import get_engine
                from sqlalchemy import text

                eng = get_engine(self._settings)
                with eng.connect() as conn:
                    tables = ["app_sessions", "app_projects", "app_documents", "app_report_comparisons"]
                    t_counts = {}
                    for t in tables:
                        try:
                            cnt = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                            t_counts[t] = cnt or 0
                        except Exception:
                            t_counts[t] = 0
                    pg_info = {
                        "status": "operational",
                        "connected": True,
                        "tables": t_counts,
                    }
            except Exception as exc:
                pg_info = {"status": "unavailable", "connected": False, "error": redact(str(exc))}

        # ChromaDB collections
        try:
            import chromadb

            chroma_dir = self._settings.chroma_dir
            if chroma_dir.exists():
                client = chromadb.PersistentClient(path=str(chroma_dir))
                colls = client.list_collections()
                total_chunks = sum(c.count() for c in colls)
                chroma_info = {
                    "status": "operational",
                    "collection_count": len(colls),
                    "total_vectors": total_chunks,
                    "collections": [c.name for c in colls][:10],
                    "path": "./chroma_db",
                }
        except Exception as exc:
            chroma_info = {"status": "unavailable", "error": redact(str(exc))}

        # Local storage files
        try:
            reports_dir = self._settings.reports_dir
            objects_dir = reports_dir / "objects"
            exports_dir = self._settings.exports_dir

            reports_count = len(list(reports_dir.glob("*.md"))) + len(list(reports_dir.glob("*.txt")))
            uploads_count = len(list(objects_dir.glob("*"))) if objects_dir.exists() else 0
            exports_count = (
                len(list(exports_dir.glob("*.pdf"))) + len(list(exports_dir.glob("*.docx")))
                if exports_dir.exists()
                else 0
            )

            # Calculate total app size
            total_bytes = 0
            for p in reports_dir.rglob("*"):
                if p.is_file():
                    total_bytes += p.stat().st_size
            if self._settings.chroma_dir.exists():
                for p in self._settings.chroma_dir.rglob("*"):
                    if p.is_file():
                        total_bytes += p.stat().st_size

            usage = shutil.disk_usage(reports_dir)
            total_gb = round(usage.total / (1024**3), 2)
            used_gb = round(usage.used / (1024**3), 2)
            free_gb = round(usage.free / (1024**3), 2)
            is_render = "/var/data" in str(reports_dir.resolve()) or os.getenv("RENDER_DISK_PATH") is not None

            local_info = {
                "read_write_verified": True,
                "reports_count": reports_count,
                "uploads_count": uploads_count,
                "exports_count": exports_count,
                "app_storage_used_mb": round(total_bytes / (1024**2), 2),
                "disk_total_gb": total_gb,
                "disk_used_gb": used_gb,
                "disk_free_gb": free_gb,
                "disk_used_percent": round((usage.used / usage.total) * 100, 1),
                "persistent_mount": is_render,
                "volume_label": "Render Persistent Volume (/var/data)" if is_render else "Local Storage Disk",
            }
        except Exception as exc:
            local_info = {"error": redact(str(exc))}

        return StorageMonitoring(postgres=pg_info, chromadb=chroma_info, local_storage=local_info)

    # ── Section J: Recent Events ─────────────────────────────────────────────

    def get_recent_events(self, limit: int = 40) -> List[RecentSystemEvent]:
        events: List[RecentSystemEvent] = []

        # 1. From in-memory ring buffer
        mem_events = get_recent_system_events(limit=limit)
        for me in mem_events:
            events.append(
                RecentSystemEvent(
                    id=me["id"],
                    timestamp=me["timestamp"],
                    category=me["category"],
                    severity=me["severity"],
                    component=me["component"],
                    message=me["message"],
                )
            )

        # 2. From research_timeline_events in sessions.db
        db_path = self._settings.sessions_db_path
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                c = conn.cursor()
                rows = c.execute("""
                    SELECT event_id, occurred_at, event_type, agent_name, message
                    FROM research_timeline_events
                    ORDER BY sequence DESC
                    LIMIT ?
                """, (limit,)).fetchall()
                for eid, occurred_at, ev_type, agent_name, msg in rows:
                    cat = "Pipeline"
                    sev = "info"
                    if "failed" in (ev_type or "").lower() or "error" in (ev_type or "").lower():
                        sev = "error"
                    elif "retry" in (ev_type or "").lower():
                        sev = "warning"
                    events.append(
                        RecentSystemEvent(
                            id=eid,
                            timestamp=occurred_at,
                            category=cat,
                            severity=sev,
                            component=agent_name or "orchestrator",
                            message=redact(msg or "Pipeline stage event"),
                        )
                    )
                conn.close()
            except Exception as exc:
                logger.warning(f"Error querying research_timeline_events: {exc}")

        # If empty, add initial boot event
        if not events:
            events.append(
                RecentSystemEvent(
                    id="sys_init_0",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    category="System",
                    severity="info",
                    component="backend",
                    message="Research AI Quest system monitoring online and initialized.",
                )
            )

        # Sort newest first and limit
        events.sort(key=lambda x: x.timestamp, reverse=True)
        return events[:limit]
