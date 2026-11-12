"""
Research AI - Agent Definitions & Specialized Chains
=====================================================
Defines the specialized agent personas and reasoning chains for multi-agent research orchestration:
  1. Search Agent      - Multi-source query execution and retrieval
  2. Reader Agent      - Targeted deep web scraping and content extraction
  3. Analyst Chain     - Synthesis, fact-checking, and cross-source analysis
  4. Writer Chain      - Publication-grade structured report generation
  5. Critic Chain      - Multi-criteria academic review and scoring
  6. Refiner Chain     - Self-correction and iterative report revision
"""

import os
import time
import logging
from typing import Optional, List
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain.agents import create_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser

from tools import web_search, scrape_url, multi_source_search
from hf_provider import get_hf_llm, is_hf_available

load_dotenv()

from app.telemetry import TracedLLMCallbackHandler, get_metrics

logger = logging.getLogger(__name__)

# ─── Provider Availability ───────────────────────────────────────────────────

PROVIDER_ORDER = ["groq", "gemini", "mistral", "huggingface"]


def _provider_has_key(provider: str) -> bool:
    if provider == "gemini":
        return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    if provider == "groq":
        return bool(os.getenv("GROQ_API_KEY"))
    if provider == "mistral":
        return bool(os.getenv("MISTRAL_API_KEY"))
    if provider in ("huggingface", "hf"):
        return bool(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN"))
    return False


def _build_llm_for_provider(provider: str, temperature: float = 0):
    cb = [TracedLLMCallbackHandler(default_provider=provider)]
    if provider in ("huggingface", "hf"):
        model_key = os.getenv("HF_MODEL", "mistral")
        return get_hf_llm(model_key)
    elif provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            temperature=temperature,
            callbacks=cb,
        )
    elif provider == "mistral":
        from langchain_mistralai import ChatMistralAI
        return ChatMistralAI(
            model=os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
            temperature=temperature,
            callbacks=cb,
        )
    else:
        return ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            google_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            temperature=temperature,
            max_retries=2,
            timeout=30,
            callbacks=cb,
        )


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in (
        "429", "rate limit", "rate_limit", "quota", "resource_exhausted",
        "502", "503", "504", "overloaded", "temporarily unavailable",
        "timeout", "timed out", "connection", "socket",
    ))


# ─── LLM Configuration ──────────────────────────────────────────────────────


def get_llm(provider: Optional[str] = None, temperature: float = 0):
    """
    Get the configured LLM with retry and automatic provider fallback.

    Tries the requested provider first (with up to 2 retries for transient
    failures), then falls through other configured providers.
    """
    requested = (provider or os.getenv("LLM_PROVIDER", "groq")).lower().strip()

    chain = [requested] + [p for p in PROVIDER_ORDER if p != requested]
    chain = [p for p in chain if _provider_has_key(p)]
    if not chain:
        chain = ["gemini"]

    last_exc = None
    prev_provider = requested
    for prov in chain:
        if prov != prev_provider:
            get_metrics().record_llm_fallback(
                from_provider=prev_provider,
                to_provider=prov,
                reason="retryable_failure" if last_exc and _is_retryable(last_exc) else "provider_unavailable",
            )
        prev_provider = prov
        for attempt in range(3):
            try:
                llm = _build_llm_for_provider(prov, temperature)
                return llm
            except Exception as exc:
                last_exc = exc
                get_metrics().record_llm_usage(
                    provider=prov,
                    model=None,
                    duration_seconds=0.0,
                    status="failed",
                    error_code=type(exc).__name__,
                )
                if _is_retryable(exc) and attempt < 2:
                    wait = 2 ** attempt
                    logger.warning("Provider %s attempt %d failed (%s), retrying in %ds", prov, attempt + 1, exc, wait)
                    time.sleep(wait)
                    continue
                logger.warning("Provider %s failed: %s — trying next", prov, exc)
                break

    raise RuntimeError(f"All LLM providers failed. Last error: {last_exc}") from last_exc


# TD-RUNTIME-04 fix: removed module-level ``llm = get_llm()`` call.
# LLM is now resolved lazily inside build_* functions so ``import agent``
# succeeds without any API keys configured.
_llm = None


def _get_default_llm(temperature: float = 0):
    """Return the process-wide default LLM (lazy singleton)."""
    global _llm
    if _llm is None:
        _llm = get_llm(temperature=temperature)
    return _llm


# ─── Pydantic Models for Structured Agent Outputs ───────────────────────────


class CriticEvaluation(BaseModel):
    """Structured evaluation returned by the Critic Agent."""
    score: int = Field(
        description="Overall quality score from 1 (poor/incomplete) to 10 (exceptional)",
        ge=1,
        le=10,
    )
    passed: bool = Field(
        description="Whether the report meets publication quality standards (score >= 8)"
    )
    strengths: List[str] = Field(
        default_factory=list,
        description="Key strengths, well-researched points, or insightful findings"
    )
    areas_for_improvement: List[str] = Field(
        default_factory=list,
        description="Specific weaknesses, missing context, data gaps, or stylistic flaws"
    )
    actionable_revisions: List[str] = Field(
        default_factory=list,
        description="Concrete, actionable recommendations for the refiner agent to fix"
    )
    verdict: str = Field(
        description="One-to-two sentence final summary verdict"
    )


# ─── Agent 1: Search Agent ───────────────────────────────────────────────────


def build_search_agent(model=None):
    """Builds a tool-calling search agent with multi-source retrieval tools."""
    return create_agent(
        model=model or _get_default_llm(),
        tools=[multi_source_search, web_search],
        system_prompt=(
            "You are a master research search agent. Your objective is to formulate "
            "effective search queries and use multi_source_search to retrieve broad, "
            "diverse, and authoritative findings across academic, news, and web sources."
        )
    )


# ─── Agent 2: Reader / Content Extraction Agent ──────────────────────────────


def build_reader_agent(model=None):
    """Builds a tool-calling reader agent for scraping deep page content."""
    return create_agent(
        model=model or _get_default_llm(),
        tools=[scrape_url],
        system_prompt=(
            "You are an expert content extraction agent. Your job is to scrape selected "
            "URLs, extract crucial facts, statistical figures, technical mechanisms, "
            "and quotes, and filter out irrelevant noise."
        )
    )


# ─── Agent 3: Analyst Chain (Synthesizer & Fact-Checker) ────────────────────


ANALYST_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You are an elite research analyst and intelligence synthesizer. "
        "Your task is to analyze gathered research across diverse sources, "
        "verify factual consistency, identify consensus vs disagreement, "
        "and extract a rigorous analytical briefing."
    )),
    ("human", """Perform a deep analytical synthesis on the research gathered below for the topic: "{topic}".

Research Context:
- Research Type: {research_type}
- Focus Areas: {focus_areas}

Search Results & Raw Snippets:
{search_results}

Deep Scraped Content:
{scraped_content}

Synthesize your analysis into the following clear sections:
1. Executive Takeaways & Consensus: High-level core insights supported by the sources.
2. Thematic Breakdown: Detailed analysis mapped to the requested focus areas.
3. Quantitative & Factual Evidence: Key statistics, dates, figures, and empirical findings.
4. Diverging Perspectives & Knowledge Gaps: Conflicting viewpoints, uncertainties, or under-researched aspects.
5. Key Authoritative Sources: Most credible primary URLs and citations identified.

Provide a comprehensive, objective, and dense analytical synthesis.""")
])


def build_analyst_chain(model=None):
    """Builds an analysis chain for synthesizing multi-source findings."""
    return ANALYST_PROMPT | (model or _get_default_llm()) | StrOutputParser()


# TD-RUNTIME-04 fix: analyst_chain is now only built on demand via orchestrator.


# ─── Agent 4: Writer Chain (Comprehensive Report Generator) ─────────────────


WRITER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You are an expert research author and investigative writer. "
        "You craft publication-ready, deeply informative, and logically organized research reports. "
        "Always ground your claims in the provided research data and cite sources clearly."
    )),
    ("human", """Draft a comprehensive, publication-grade research report on the topic below.

Topic: {topic}
Research Type: {research_type}
Focus Areas: {focus_areas}

Source Breakdown:
{source_summary}

Synthesized Analyst Briefing:
{analysis}

Primary Scraped & Search Materials:
{research}

Report Structure Requirements:
# [Clear, Engaging Report Title]

## 1. Executive Summary
- High-level overview of the topic, current state, and critical takeaways.

## 2. Research Scope & Methodology
- Context on research angles explored, source types consulted, and analytical approach.

## 3. Deep-Dive Analysis
(Create detailed subsections covering each key focus area and thematic angle with rich explanations, data points, and technical/factual depth.)

## 4. Comparative Perspectives & Divergences
- Contrasting viewpoints, market/academic debates, and trade-offs.

## 5. Strategic Implications & Future Outlook
- What this means for practitioners, researchers, or industry; next developments to watch.

## 6. Limitations & Open Questions
- Unresolved questions, data limitations, or emerging uncertainties.

## 7. Sources & References
- Complete markdown list of all referenced URLs with descriptive titles and source categories.

Formatting Guidelines:
- Use clear Markdown formatting with headers, bullet points, callouts, and tables where appropriate.
- Maintain an authoritative, objective, and analytical tone.
- Do not use generic filler words; maximize information density and clarity.""")
])


def build_writer_chain(model=None):
    """Builds a writer chain for drafting comprehensive reports."""
    return WRITER_PROMPT | (model or _get_default_llm()) | StrOutputParser()


# TD-RUNTIME-04 fix: writer_chain is now built on demand via orchestrator.


# ─── Agent 5: Critic Chain (Reviewer & Quality Auditor) ──────────────────────


CRITIC_JSON_PARSER = JsonOutputParser(pydantic_object=CriticEvaluation)

CRITIC_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You are a strict academic reviewer and quality assurance critic for research reports. "
        "Evaluate reports rigorously on factual grounding, depth, structural clarity, "
        "citation completeness, and balance. "
        "A score of 8 or above is required for publication approval.\n\n"
        "Output MUST be in valid JSON matching this schema:\n"
        "{format_instructions}"
    )),
    ("human", """Review and critically audit the research report below on the topic: "{topic}".

Original Focus Areas: {focus_areas}

Drafted Research Report:
{report}

Evaluate carefully:
- Does it comprehensively address all focus areas with empirical depth?
- Are claims substantiated without hallucinations or generic fluff?
- Is there a complete Sources section with working URLs?
- Is the structure, formatting, and readability excellent?

Provide your structured review now.""")
])


def build_critic_chain(model=None):
    """Builds a structured critic chain returning a CriticEvaluation."""
    return (
        CRITIC_PROMPT.partial(
            format_instructions=CRITIC_JSON_PARSER.get_format_instructions()
        )
        | (model or _get_default_llm())
        | CRITIC_JSON_PARSER
    )


# TD-RUNTIME-04 fix: critic_chain is now built on demand via orchestrator.


# Backward-compatible text critic chain if needed
CRITIC_TEXT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a sharp and constructive research critic. Be honest and specific."),
    ("human", """Review the research report below and evaluate it strictly.

Report:
{report}

Respond in this exact format:

Score: X/10

Strengths:
- ...
- ...

Areas to improve:
- ...
- ...

One line verdict:
...""")
])


def build_critic_text_chain(model=None):
    return CRITIC_TEXT_PROMPT | (model or _get_default_llm()) | StrOutputParser()


# ─── Agent 6: Refiner Chain (Self-Correction & Revision) ─────────────────────


REFINER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You are an expert research editor and refiner. Your mission is to take a drafted research report "
        "and elevate it to publication perfection by systematically addressing every critique, "
        "expanding thin sections, verifying citations, and polishing prose."
    )),
    ("human", """You are revising the research report for topic: "{topic}".

Previous Report Draft:
{report}

Critic Review & Score ({critic_score}/10):
- Strengths: {strengths}
- Areas for Improvement: {areas_for_improvement}
- Actionable Revisions Required: {actionable_revisions}
- Verdict: {verdict}

Supporting Research Context:
{research}

Instructions for Revision:
1. Address EVERY point in 'Actionable Revisions Required' directly and thoroughly.
2. Preserve and enhance the report's strong sections while fixing weaknesses.
3. Ensure all figures, statistics, and claims are supported and clearly cited.
4. Output the COMPLETE, fully polished, final revised report in Markdown (do not summarize or truncate).""")
])


def build_refiner_chain(model=None):
    """Builds a refiner chain for revising reports based on critic feedback."""
    return REFINER_PROMPT | (model or _get_default_llm()) | StrOutputParser()


# TD-RUNTIME-04 fix: refiner_chain is now built on demand via orchestrator.
