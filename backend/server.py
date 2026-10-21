"""
Research AI — FastAPI REST Server
===================================
Exposes the Multi-Agent Research Orchestrator over HTTP with Server-Sent Events
(SSE) streaming so clients can observe each agent's progress in real time.

Endpoints
---------
POST /research
    Start a research job and stream progress as SSE events.

GET /health
    Simple liveness check.

GET /providers
    List supported LLM providers.

Usage
-----
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload

Then POST to the endpoint::

    curl -N -X POST http://localhost:8000/research \\
         -H "Content-Type: application/json" \\
         -d '{"topic": "Quantum computing breakthroughs 2024",
              "provider": "gemini",
              "max_revisions": 2,
              "approval_threshold": 8}'

SSE Event Schema
----------------
Each event is a JSON object with the shape::

    {
        "event":   "progress" | "agent_start" | "agent_done" | "complete" | "error",
        "agent":   "Strategist" | "Search" | ... | "Exporter" (optional),
        "message": "Human-readable status string",
        "data":    { ... }   (optional — only on "complete" and "error" events)
    }
"""

from __future__ import annotations

import sys
import os
import re
import hmac
import hashlib
import json
import time
import asyncio
import threading
import queue
import traceback
from typing import AsyncGenerator, Optional

# Ensure UTF-8 stdout on Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from dotenv import load_dotenv  # ensure API keys from backend/.env are visible server-wide

load_dotenv(_BACKEND_DIR / ".env")
load_dotenv(_BACKEND_DIR.parent / ".env")
load_dotenv()

from fastapi import FastAPI, HTTPException, Depends, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from auth_validator import (
    get_current_user,
    get_optional_user,
    get_supabase_url,
    get_supabase_anon_key,
)

# ─── App Initialisation ───────────────────────────────────────────────────────

app = FastAPI(
    title="Research AI API",
    description=(
        "Multi-Agent Research Orchestrator powered by LangGraph. "
        "Streams live agent progress via Server-Sent Events (SSE)."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8123",
        "http://127.0.0.1:8123",
        "http://localhost:3000",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Report Artifacts Directory ──────────────────────────────────────────────
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ─── Request / Response Models ────────────────────────────────────────────────


class ResearchRequest(BaseModel):
    """Body accepted by POST /research."""
    topic: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="The research topic or question to investigate.",
        examples=["Impact of large language models on scientific discovery"],
    )
    provider: str = Field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "groq"),
        description="LLM provider: 'groq', 'gemini', 'mistral', or 'huggingface'.",
        examples=["groq"],
    )
    max_revisions: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum self-correction revision cycles (0 = no revisions).",
    )
    approval_threshold: int = Field(
        default=8,
        ge=1,
        le=10,
        description="Critic score (out of 10) required to approve the report without revisions.",
    )
    output_dir: str = Field(
        default=str(Path(__file__).resolve().parent / "reports"),
        description="Directory where the final report artifacts (.md, .docx, .pdf) are saved.",
    )
    user_id: Optional[str] = Field(
        default=None,
        description="Authenticated user ID to associate with this research run.",
    )


class HealthResponse(BaseModel):
    status: str
    version: str


class ProvidersResponse(BaseModel):
    providers: list[str]
    default: str


class SessionsResponse(BaseModel):
    sessions: list[dict]
    count: int


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    reply: str
    provider: str


class ExportRequest(BaseModel):
    """Body accepted by POST /export."""
    content: str = Field(..., description="Draft markdown content to export.")
    topic: Optional[str] = Field("Research Report", description="Research topic or report title.")
    format: str = Field("pdf", description="Target format: 'pdf', 'docx', 'md', or 'txt'.")
    citation_style: Optional[str] = Field("apa", description="Citation style: 'apa', 'mla', or 'harvard'.")


def _get_share_secret() -> bytes:
    secret = os.getenv("SHARE_SECRET") or os.getenv("CHROMA_ENCRYPTION_KEY") or "quest_report_share_secret_2026"
    return secret.encode("utf-8")


def generate_share_token(filename: str) -> str:
    clean = Path(filename).name
    return hmac.new(_get_share_secret(), clean.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def verify_share_token(filename: str, token: str) -> bool:
    if not token:
        return False
    expected = generate_share_token(filename)
    return hmac.compare_digest(expected, token)


# ─── SSE Helpers ─────────────────────────────────────────────────────────────

def _sse_format(event_type: str, payload: dict) -> str:
    """Format a dictionary as a valid SSE event string."""
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _build_event(
    event: str,
    message: str,
    agent: Optional[str] = None,
    data: Optional[dict] = None,
) -> str:
    payload: dict = {"event": event, "message": message}
    if agent:
        payload["agent"] = agent
    if data:
        payload["data"] = data
    return _sse_format(event, payload)


# ─── SSE-Aware Rich Console Capture ──────────────────────────────────────────
# The orchestrator prints Rich console output. We intercept it by patching
# sys.stdout so each print line becomes an SSE "progress" event.

class _SSEWriter:
    """A file-like object that forwards writes to an SSE event queue."""

    def __init__(self, event_queue: queue.Queue, original_stdout) -> None:
        self._q = event_queue
        self._orig = original_stdout
        self._buf = ""

    def write(self, text: str) -> int:
        self._orig.write(text)  # also pass through to original stdout
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            clean = _strip_ansi(line.strip())
            if clean:
                self._q.put(("progress", clean))
        return len(text)

    def flush(self) -> None:
        self._orig.flush()

    def reconfigure(self, **kwargs) -> None:  # noqa: D401
        """No-op shim so Rich doesn't crash when it tries to reconfigure stdout."""
        pass


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from Rich-rendered terminal output."""
    import re
    ansi_escape = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")
    # Also strip Rich markup tags [bold], [/bold] etc. that leak through
    rich_tag = re.compile(r"\[/?[a-zA-Z0-9 _#]*\]")
    text = ansi_escape.sub("", text)
    text = rich_tag.sub("", text)
    return text.strip()


# ─── Research Runner (blocking, runs in thread) ───────────────────────────────

# Agent name → friendly label mapping (matched by log message substrings)
_AGENT_LABELS: list[tuple[str, str]] = [
    ("AGENT 1", "Strategist"),
    ("AGENT 2", "Search Engine"),
    ("AGENT 3", "Deep Reader"),
    ("AGENT 4", "Analyst"),
    ("AGENT 5", "Report Writer"),
    ("AGENT 6", "Critic"),
    ("AGENT 7", "Refiner"),
    ("AGENT 8", "Exporter"),
    ("CITATION VALIDATOR", "Citation Validator"),
    ("MULTI-FORMAT EXPORT", "Multi-Format Export"),
]


def _detect_agent(message: str) -> Optional[str]:
    """Return agent label if the message announces an agent start."""
    for key, label in _AGENT_LABELS:
        if key in message.upper():
            return label
    return None


def _is_provider_error(exc: Exception) -> bool:
    """True when an exception looks like a quota / auth / rate-limit / network
    failure that a different LLM provider could survive."""
    text = str(exc).lower()
    markers = (
        "429", "resource_exhausted", "quota", "rate limit", "rate_limit",
        "401", "403", "unauthorized", "api key", "api_key", "billing",
        "insufficient", "exceeded your current",
        # Network-level failures: a transient socket/proxy blip on one call
        # should let the next provider in the chain try, not kill the run.
        "winerror", "socket", "unreachable", "connection", "timed out",
        "timeout", "network", "ssl", "getaddrinfo failed", "name or service",
        "502", "503", "504", "overloaded", "temporarily unavailable",
        "max retries exceeded",
        "bad request", "invalid_request_error", "model_not_supported",
    )
    return any(m in text for m in markers)


def _chat_provider_chain() -> list[str]:
    """Providers that actually have keys configured, in preference order."""
    chain = []
    if os.getenv("GEMINI_API_KEY"):
        chain.append("gemini")
    if os.getenv("GROQ_API_KEY"):
        chain.append("groq")
    if os.getenv("MISTRAL_API_KEY"):
        chain.append("mistral")
    if os.getenv("HF_TOKEN"):
        chain.append("huggingface")
    return chain or ["gemini"]


def _chat_via_provider(provider: str, message: str) -> str:
    """Single-shot chat completion against one provider (blocking)."""
    system_prompt = (
        "You are Quest, the assistant inside a research desk. Answer clearly and "
        "concisely (under 180 words) in plain prose. If the user asks for a full "
        "research report, tell them to switch to Research mode and submit their "
        "question there — the 8-agent pipeline will build it."
    )
    if provider == "huggingface":
        from hf_provider import get_hf_llm
        llm = get_hf_llm()
    elif provider == "groq":
        from langchain_groq import ChatGroq
        llm = ChatGroq(
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            temperature=0.4,
        )
    elif provider == "mistral":
        from langchain_mistralai import ChatMistralAI
        llm = ChatMistralAI(model="mistral-small-latest", temperature=0.4)
    else:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
                model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
                google_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
                temperature=0.4,
                max_retries=0,
                timeout=15,
            )

    result = llm.invoke([("system", system_prompt), ("user", message)])
    content = result.content
    if isinstance(content, list):  # some providers return content blocks
        content = "\n".join(
            (b.get("text") if isinstance(b, dict) and b.get("text") else str(b))
            for b in content
        )
    return str(content).strip()


def _brief_err(exc: BaseException) -> str:
    """Collapse an exception into a short one-line string for error reports."""
    text = " ".join(str(exc).split())
    return (text[:160] + "…") if len(text) > 160 else text


def _run_research_in_thread(
    request: ResearchRequest,
    event_queue: queue.Queue,
) -> None:
    """
    Runs the full MultiAgentOrchestrator pipeline in a background thread,
    pushing SSE events onto *event_queue* as progress is made.

    Sends a sentinel ``None`` when finished (success or failure).
    """
    # Lazy import here so server starts fast even if some optional deps missing
    try:
        from orchestrator import MultiAgentOrchestrator
    except ImportError as e:
        event_queue.put(("error", f"Orchestrator import failed: {e}"))
        event_queue.put(None)  # sentinel
        return

    original_stdout = sys.stdout
    started_at = time.time()

    # Redirect stdout → SSE queue
    sse_writer = _SSEWriter(event_queue, original_stdout)
    sys.stdout = sse_writer  # type: ignore[assignment]

    try:
        # Preferred provider first, then every other keyed provider — a quota
        # or rate-limit failure on one no longer kills the whole run.
        provider_chain = [request.provider] + [
            p for p in ("gemini", "groq", "mistral", "huggingface") if p != request.provider
        ]
        final_state: Optional[dict] = None
        used_provider = request.provider
        last_exc: Optional[Exception] = None
        failures: list[str] = []

        for attempt, provider in enumerate(provider_chain):
            try:
                if attempt:
                    event_queue.put((
                        "agent_start",
                        f"Provider '{provider_chain[attempt - 1]}' is unavailable "
                        f"(quota / rate limit) — retrying with '{provider}'...",
                    ))
                else:
                    event_queue.put(("agent_start", f"Initialising Multi-Agent Orchestrator ({provider})..."))

                orchestrator = MultiAgentOrchestrator(
                    provider=provider,
                    max_revisions=request.max_revisions,
                    approval_threshold=request.approval_threshold,
                    output_dir=request.output_dir,
                    interactive=False,
                )

                event_queue.put(("agent_start", f"Launching research pipeline for: {request.topic}"))
                candidate = orchestrator.run(topic=request.topic)

                status = candidate.get("status", "")
                if status in ("completed", "cancelled"):
                    final_state = candidate
                    used_provider = provider
                    break
                last_exc = RuntimeError(f"Pipeline ended with status '{status}' on provider '{provider}'")
                failures.append(f"{provider}: pipeline ended with status '{status}'")
            except Exception as exc:
                last_exc = exc
                failures.append(f"{provider}: {_brief_err(exc)}")
                if not _is_provider_error(exc) or attempt == len(provider_chain) - 1:
                    break
                continue

        if final_state is None:
            # Every attempt is recorded in *failures*, so surface the full
            # per-provider breakdown instead of just the last raw exception.
            raise RuntimeError(
                "All LLM providers failed — " + " | ".join(failures)
            ) from last_exc

        # Persist the session so the History/Dashboard UI sees real runs
        try:
            from session_manager import SessionManager
            SessionManager().save_session(
                final_state,
                duration_seconds=time.time() - started_at,
                notes=f"provider={used_provider}",
                user_id=request.user_id,
            )
        except Exception as save_exc:
            event_queue.put(("progress", f"Session persistence skipped: {save_exc}"))

        # Push completion event with key result data
        result_data = {
            "status": final_state.get("status", "completed"),
            "provider": used_provider,
            "critic_score": final_state.get("critic_score"),
            "revision_count": final_state.get("revision_count", 0),
            "saved_report_path": final_state.get("saved_report_path", ""),
            "sources_analyzed": len(final_state.get("search_results_raw", [])),
            "logs": final_state.get("logs", []),
        }
        event_queue.put(("complete", result_data))

    except Exception as exc:
        tb = traceback.format_exc()
        event_queue.put(("error", {"message": str(exc), "traceback": tb}))
    finally:
        sys.stdout = original_stdout   # Restore stdout
        event_queue.put(None)          # Sentinel: stream finished


# ─── SSE Generator ───────────────────────────────────────────────────────────

async def _sse_generator(
    request: ResearchRequest,
) -> AsyncGenerator[str, None]:
    """
    Async generator that:
      1. Starts the blocking research pipeline in a ThreadPoolExecutor thread.
      2. Polls the shared queue and yields SSE events to the HTTP client.
      3. Terminates when the thread pushes the ``None`` sentinel.
    """
    event_queue: queue.Queue = queue.Queue()
    loop = asyncio.get_event_loop()

    # Kick off the blocking pipeline in a background thread
    thread = threading.Thread(
        target=_run_research_in_thread,
        args=(request, event_queue),
        daemon=True,
    )
    thread.start()

    # Yield a heartbeat immediately so the client knows the connection is live
    yield _build_event("progress", "Research pipeline initialising...", data={"topic": request.topic})

    prev_agent: Optional[str] = None

    while True:
        # Poll non-blocking every 50 ms to keep the async loop responsive
        try:
            item = await loop.run_in_executor(None, lambda: event_queue.get(timeout=0.05))
        except queue.Empty:
            # Send a keepalive comment so proxies don't drop the connection
            yield ": keepalive\n\n"
            continue

        if item is None:
            # Sentinel — pipeline finished
            yield _build_event("progress", "Pipeline execution complete.")
            break

        event_type, payload = item

        if event_type == "complete":
            yield _build_event(
                "complete",
                "Research report successfully generated.",
                data=payload,
            )

        elif event_type == "error":
            if isinstance(payload, dict):
                yield _build_event("error", payload.get("message", "Unknown error"), data=payload)
            else:
                yield _build_event("error", str(payload))
            break

        elif event_type == "agent_start":
            agent = _detect_agent(str(payload))
            if agent and agent != prev_agent:
                prev_agent = agent
                yield _build_event("agent_start", f"Agent active: {agent}", agent=agent)
            yield _build_event("progress", str(payload), agent=agent)

        else:
            # "progress" — raw stdout line from the orchestrator
            line = str(payload)
            agent = _detect_agent(line)
            if agent and agent != prev_agent:
                prev_agent = agent
                yield _build_event("agent_start", f"Agent active: {agent}", agent=agent)
            yield _build_event("progress", line, agent=agent or prev_agent)


# ─── Routes ───────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """Liveness check endpoint."""
    return HealthResponse(status="ok", version="1.0.0")


@app.get("/auth/config", tags=["Auth"])
async def auth_config():
    """Return Supabase configuration for client-side authentication."""
    return {
        "supabaseUrl": get_supabase_url(),
        "supabaseAnonKey": get_supabase_anon_key(),
    }


@app.get("/auth/me", tags=["Auth"])
async def auth_me(current_user: dict = Depends(get_current_user)):
    """Return the profile of the currently authenticated user."""
    return {"status": "authenticated", "user": current_user}


@app.get("/providers", response_model=ProvidersResponse, tags=["System"])
async def list_providers() -> ProvidersResponse:
    """Returns the list of supported LLM providers."""
    return ProvidersResponse(
        providers=["groq", "gemini", "mistral", "huggingface"],
        default=os.getenv("LLM_PROVIDER", "groq"),
    )


@app.get("/sessions", response_model=SessionsResponse, tags=["System"])
async def list_sessions(current_user: Optional[dict] = Depends(get_optional_user)) -> SessionsResponse:
    """Return past research sessions. When authenticated, strictly filters by user ID."""
    from session_manager import SessionManager

    user_id = current_user.get("user_id") if current_user else None
    sessions = SessionManager().get_all_sessions(user_id=user_id)
    return SessionsResponse(sessions=sessions, count=len(sessions))


@app.delete("/sessions/{session_id}", tags=["System"])
async def delete_session_endpoint(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a research session record owned by the authenticated user."""
    from session_manager import SessionManager
    deleted = SessionManager().delete_session(session_id, user_id=current_user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found or not owned by user.")
    return {"status": "deleted", "session_id": session_id}


@app.get("/reports/{filename:path}/share", tags=["Reports"])
async def get_report_share_link(
    filename: str,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    """
    Generate a read-only share token and link for a report.
    Accessible to the owner of the report or unauthenticated runs.
    """
    from session_manager import SessionManager
    sm = SessionManager()

    clean_filename = Path(filename).name
    file_path = (REPORTS_DIR / clean_filename).resolve()

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Report file not found.")

    if current_user:
        if not sm.user_owns_report(current_user["user_id"], clean_filename):
            raise HTTPException(status_code=403, detail="Access denied: You do not own this research report.")

    token = generate_share_token(clean_filename)
    return {
        "filename": clean_filename,
        "share_token": token,
        "share_url": f"/report.html?run={clean_filename}&share={token}",
    }


@app.get("/reports/{filename:path}", tags=["Reports"])
async def get_report_file(
    filename: str,
    share: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
    current_user: Optional[dict] = Depends(get_optional_user),
):
    """
    Serve a generated research report (.md, .docx, .pdf, .txt).
    Enforces user isolation, with secure read-only token bypass for shared links.
    """
    from session_manager import SessionManager
    sm = SessionManager()

    clean_filename = Path(filename).name
    file_path = (REPORTS_DIR / clean_filename).resolve()

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Report file not found.")

    # 1. Check share token for read-only access
    is_valid_share = False
    if share and verify_share_token(clean_filename, share):
        is_valid_share = True

    # 2. Check query param JWT token if provided
    if not current_user and token:
        try:
            from auth_validator import validate_jwt_token
            current_user = validate_jwt_token(token)
        except Exception:
            current_user = None

    if not is_valid_share:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required to view this report.")
        if not sm.user_owns_report(current_user["user_id"], clean_filename):
            raise HTTPException(status_code=403, detail="Access denied: You do not own this research report.")

    media_type = "text/markdown; charset=utf-8"
    if clean_filename.endswith(".pdf"):
        media_type = "application/pdf"
    elif clean_filename.endswith(".docx"):
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif clean_filename.endswith(".txt"):
        media_type = "text/plain; charset=utf-8"

    response_headers = {}
    if is_valid_share:
        response_headers["X-Read-Only"] = "true"

    return FileResponse(file_path, media_type=media_type, filename=clean_filename, headers=response_headers)


@app.post("/export", tags=["Reports"])
async def export_report(
    req: ExportRequest,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    """
    Export research report draft content to PDF, DOCX, Markdown, or TXT.
    Applies the selected citation style (APA, MLA, Harvard).
    Generates meaningful topic-based filenames and returns the downloadable file.
    """
    from report_exporter import ReportExporter

    raw_content = (req.content or "").strip()
    if not raw_content:
        raise HTTPException(status_code=400, detail="Cannot export an empty report draft.")

    fmt = (req.format or "pdf").lower().strip().lstrip(".")
    if fmt not in ("pdf", "docx", "md", "txt"):
        raise HTTPException(status_code=400, detail=f"Unsupported format '{req.format}'. Supported formats: pdf, docx, md, txt")

    # Generate meaningful filename from topic
    topic_str = (req.topic or "research_report").strip()
    clean_slug = re.sub(r"[^\w\s-]", "", topic_str).strip().lower()
    clean_slug = re.sub(r"[-\s]+", "_", clean_slug)[:60].strip("_")
    if not clean_slug:
        clean_slug = "research_report"

    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    ext_map = {"pdf": ".pdf", "docx": ".docx", "md": ".md", "txt": ".txt"}
    out_ext = ext_map.get(fmt, f".{fmt}")
    out_filename = f"quest_{clean_slug}_{timestamp_str}{out_ext}"

    export_dir = REPORTS_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    target_path = export_dir / out_filename

    try:
        exporter = ReportExporter()
        saved_path = exporter.export_single(
            markdown_text=raw_content,
            fmt=fmt,
            output_path=target_path,
            citation_style=req.citation_style or "apa",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to export {fmt.upper()}: {str(exc)}")

    media_map = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "md": "text/markdown; charset=utf-8",
        "txt": "text/plain; charset=utf-8",
    }
    media_type = media_map.get(fmt, "application/octet-stream")

    return FileResponse(
        saved_path,
        media_type=media_type,
        filename=out_filename,
        headers={"Content-Disposition": f'attachment; filename="{out_filename}"'},
    )


@app.post("/chat", response_model=ChatResponse, tags=["Research"])
async def chat(request: ChatRequest) -> ChatResponse:
    """Single-turn chat answer from the first available LLM provider.

    Falls through the provider chain on quota / rate-limit errors and returns
    HTTP 503 with a per-provider breakdown when every provider is exhausted.
    """
    loop = asyncio.get_event_loop()
    errors: list[str] = []
    for provider in _chat_provider_chain():
        try:
            reply = await loop.run_in_executor(None, _chat_via_provider, provider, request.message)
            if reply:
                return ChatResponse(reply=reply, provider=provider)
            errors.append(f"{provider}: empty reply")
        except Exception as exc:
            errors.append(f"{provider}: {str(exc)[:160]}")
    raise HTTPException(
        status_code=503,
        detail=(
            "All model providers are rate-limited or unavailable right now. "
            + " | ".join(errors)
        )[:500],
    )


@app.post(
    "/research",
    tags=["Research"],
    summary="Start a research job and stream live progress via SSE",
    response_description=(
        "A text/event-stream of SSE events. Each event is a JSON object with "
        "keys: event, message, agent (optional), data (optional)."
    ),
)
async def run_research(
    request: ResearchRequest,
    current_user: Optional[dict] = Depends(get_optional_user),
) -> StreamingResponse:
    """
    Start a multi-agent research pipeline and stream progress back as
    **Server-Sent Events (SSE)**.
    """
    if not request.topic.strip():
        raise HTTPException(status_code=422, detail="topic cannot be empty.")

    if current_user:
        request.user_id = current_user.get("user_id")

    return StreamingResponse(
        _sse_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable Nginx buffering
            "Connection": "keep-alive",
        },
    )


# ─── Dev entrypoint ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
