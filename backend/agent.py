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


# ─── LLM Configuration ──────────────────────────────────────────────────────


def get_llm(provider: Optional[str] = None, temperature: float = 0):
    """
    Get the configured LLM based on provider choice.

    Args:
        provider: "gemini", "mistral", "groq" or "huggingface". If None, reads
                  LLM_PROVIDER from .env (defaults to "gemini").
        temperature: Model temperature (default 0 for deterministic outputs).
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).lower().strip()

    if provider in ("huggingface", "hf"):
        model_key = os.getenv("HF_MODEL", "mistral")
        return get_hf_llm(model_key)
    elif provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            temperature=temperature,
        )
    elif provider == "mistral":
        from langchain_mistralai import ChatMistralAI
        return ChatMistralAI(
            model=os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
            temperature=temperature,
        )
    else:
        return ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            google_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            temperature=temperature,
            max_retries=0,
            timeout=15,
        )


# Default LLM
llm = get_llm()


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
        model=model or llm,
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
        model=model or llm,
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
    return ANALYST_PROMPT | (model or llm) | StrOutputParser()


analyst_chain = build_analyst_chain()


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
    return WRITER_PROMPT | (model or llm) | StrOutputParser()


writer_chain = build_writer_chain()


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
        | (model or llm)
        | CRITIC_JSON_PARSER
    )


critic_chain = build_critic_chain()


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
    return CRITIC_TEXT_PROMPT | (model or llm) | StrOutputParser()


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
    return REFINER_PROMPT | (model or llm) | StrOutputParser()


refiner_chain = build_refiner_chain()
