"""
Research Query Intake System
=============================
Transforms a raw user topic into a structured, validated research plan:
  1. Validates the query (clarity, actionability)
  2. Decomposes it into targeted sub-queries
  3. Classifies research type, scope, and time-sensitivity
  4. Identifies focus areas and recommended source types
  5. Presents the plan to the user for confirmation
"""

from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from typing import Optional
import os
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

console = Console()


# ─── Pydantic Models ────────────────────────────────────────────────────────


class QueryValidation(BaseModel):
    """Result of validating a research query."""
    is_valid: bool = Field(
        description="Whether the query is clear and actionable for research"
    )
    clarity_score: int = Field(
        description="How clear the query is, from 1 (very vague) to 10 (crystal clear)"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Specific issues found with the query (empty if none)"
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Concrete suggestions to make the query better"
    )


class ResearchRequest(BaseModel):
    """Fully structured research request after LLM-powered analysis."""
    original_query: str = Field(
        description="The original user query, unchanged"
    )
    refined_query: str = Field(
        description="A clearer, more specific and searchable version of the query"
    )
    research_type: str = Field(
        description=(
            "One of: exploratory, comparative, fact_check, "
            "technical_deep_dive, trend_analysis, opinion_analysis"
        )
    )
    sub_queries: list[str] = Field(
        description="3-5 specific, targeted sub-queries for comprehensive search coverage"
    )
    focus_areas: list[str] = Field(
        description="Key angles, dimensions, or themes to investigate"
    )
    scope: str = Field(
        description="One of: broad, focused, deep_dive"
    )
    time_sensitivity: str = Field(
        description=(
            "One of: high (needs the very latest data), "
            "medium (recent is preferred), low (evergreen / historical topic)"
        )
    )
    suggested_source_types: list[str] = Field(
        description=(
            "Most relevant source types from: academic, news, "
            "technical_docs, government, industry_reports, blogs"
        )
    )


# ─── Prompts ─────────────────────────────────────────────────────────────────


VALIDATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You are a research query validator. Your job is to evaluate whether "
        "a user's research query is clear, specific, and actionable.\n\n"
        "A valid query should:\n"
        "- Have a clear topic or subject\n"
        "- Be specific enough to research (not just a single word with no context)\n"
        "- Be a topic that can be researched through web sources\n\n"
        "A query with clarity_score >= 6 should be marked as is_valid=true.\n\n"
        "Respond in this exact JSON format:\n"
        "{format_instructions}"
    )),
    ("human", "Research query: {query}")
])


DECOMPOSITION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You are an expert research strategist. Given a research topic, "
        "analyze it and create a comprehensive research plan.\n\n"
        "Your job is to:\n"
        "1. Refine the query — make it more specific and searchable while "
        "preserving the user's intent\n"
        "2. Classify the research type (exploratory, comparative, fact_check, "
        "technical_deep_dive, trend_analysis, or opinion_analysis)\n"
        "3. Generate 3-5 targeted sub-queries that together cover the topic "
        "from multiple angles for comprehensive results\n"
        "4. Identify 2-4 key focus areas or dimensions to investigate\n"
        "5. Determine the scope (broad, focused, deep_dive) based on the "
        "specificity of the query\n"
        "6. Assess time sensitivity (high, medium, low) based on whether "
        "the topic requires recent data\n"
        "7. Suggest what types of sources would be most valuable (academic, "
        "news, technical_docs, government, industry_reports, blogs)\n\n"
        "IMPORTANT: Each sub-query should search a different angle or aspect "
        "of the topic. Avoid redundant sub-queries.\n\n"
        "Respond in this exact JSON format:\n"
        "{format_instructions}"
    )),
    ("human", "Research topic: {query}")
])


# ─── Query Intake System ────────────────────────────────────────────────────


class QueryIntakeSystem:
    """
    Handles the complete research query intake pipeline:
      1. Validates the user's query for clarity and actionability
      2. Decomposes it into sub-queries for multi-angle search
      3. Classifies research type, scope, and focus areas
      4. Returns a structured ResearchRequest ready for the pipeline
    """

    def __init__(self, provider: Optional[str] = None):
        # Honor the pipeline's provider choice so a fallback run never hits a
        # hardwired Gemini quota when another provider is available.
        from agent import get_llm
        self.llm = get_llm(provider=provider, temperature=0.2)

        # Parsers
        self.validation_parser = JsonOutputParser(
            pydantic_object=QueryValidation
        )
        self.decomposition_parser = JsonOutputParser(
            pydantic_object=ResearchRequest
        )

        # Chains
        self.validation_chain = (
            VALIDATION_PROMPT.partial(
                format_instructions=self.validation_parser.get_format_instructions()
            )
            | self.llm
            | self.validation_parser
        )

        self.decomposition_chain = (
            DECOMPOSITION_PROMPT.partial(
                format_instructions=self.decomposition_parser.get_format_instructions()
            )
            | self.llm
            | self.decomposition_parser
        )

    def validate_query(self, query: str) -> QueryValidation:
        """Validate whether a query is clear and researchable."""
        result = self.validation_chain.invoke({"query": query})
        return QueryValidation(**result)

    def decompose_query(self, query: str) -> ResearchRequest:
        """Decompose a research topic into a structured research plan."""
        result = self.decomposition_chain.invoke({"query": query})
        return ResearchRequest(**result)

    def process(self, query: str) -> Optional[ResearchRequest]:
        """
        Full intake pipeline:
          1. Validate the query
          2. If invalid, offer the user a chance to revise
          3. Decompose into structured research request
          4. Display the research plan
          5. Let the user confirm or revise
        Returns a ResearchRequest, or None if the user cancels.
        """
        query = query.strip()
        if not query:
            console.print("[bold red]❌ Empty query. Please enter a research topic.[/bold red]")
            return None

        # ── Step 1: Validate ─────────────────────────────────────
        console.print(Panel(
            "🔍 Validating your research query...",
            style="cyan"
        ))
        validation = self.validate_query(query)
        self._display_validation(validation)

        if not validation.is_valid:
            console.print(Panel(
                "[bold red]⚠️  Query needs improvement before proceeding.[/bold red]\n\n"
                + "[bold]Issues:[/bold]\n"
                + "\n".join(f"  • {issue}" for issue in validation.issues)
                + "\n\n[bold]Suggestions:[/bold]\n"
                + "\n".join(f"  💡 {s}" for s in validation.suggestions),
                title="Query Validation",
                style="red"
            ))
            choice = input(
                "\nWould you like to (r)evise your query or (c)ontinue anyway? [r/c]: "
            ).strip().lower()
            if choice == "r":
                new_query = input("Enter revised query: ").strip()
                return self.process(new_query)
            elif choice != "c":
                console.print("[yellow]Research cancelled.[/yellow]")
                return None

        # ── Step 2: Decompose ────────────────────────────────────
        console.print(Panel(
            "🧠 Analyzing and decomposing your research topic...",
            style="cyan"
        ))
        research_request = self.decompose_query(query)

        # ── Step 3: Display plan ─────────────────────────────────
        self._display_research_plan(research_request)

        # ── Step 4: User confirmation ────────────────────────────
        choice = input(
            "\n✅ Proceed with this research plan? (y)es / (r)evise query / (n)o: "
        ).strip().lower()
        if choice == "r":
            new_query = input("Enter revised query: ").strip()
            return self.process(new_query)
        elif choice not in ("y", "yes", ""):
            console.print("[yellow]Research cancelled.[/yellow]")
            return None

        console.print("[bold green]✅ Research plan confirmed. Starting pipeline...[/bold green]\n")
        return research_request

    # ── Display Helpers ──────────────────────────────────────────

    def _display_validation(self, validation: QueryValidation):
        """Show the validation result."""
        status = "[bold green]✅ VALID[/bold green]" if validation.is_valid \
            else "[bold red]❌ NEEDS IMPROVEMENT[/bold red]"
        score_color = "green" if validation.clarity_score >= 7 \
            else "yellow" if validation.clarity_score >= 5 else "red"

        console.print(Panel(
            f"Status: {status}    "
            f"Clarity: [{score_color}]{validation.clarity_score}/10[/{score_color}]",
            title="Query Validation Result",
            style="blue"
        ))

    def _display_research_plan(self, request: ResearchRequest):
        """Display the structured research plan in rich formatted panels."""

        # ── Overview table ───────────────────────────────────────
        info_table = Table(show_header=False, box=None, padding=(0, 2))
        info_table.add_column("Key", style="bold cyan", width=22)
        info_table.add_column("Value", style="white")
        info_table.add_row(
            "📋 Research Type",
            request.research_type.replace("_", " ").title()
        )
        info_table.add_row(
            "🎯 Scope",
            request.scope.replace("_", " ").title()
        )
        info_table.add_row(
            "⏱️  Time Sensitivity",
            request.time_sensitivity.title()
        )
        console.print(Panel(info_table, title="📊 Research Plan Overview", style="green"))

        # ── Refined query ────────────────────────────────────────
        console.print(Panel(
            f"[bold white]{request.refined_query}[/bold white]",
            title="🔎 Refined Query",
            style="blue"
        ))

        # ── Sub-queries ──────────────────────────────────────────
        sub_q_text = "\n".join(
            f"  [bold cyan]{i}.[/bold cyan] {sq}"
            for i, sq in enumerate(request.sub_queries, 1)
        )
        console.print(Panel(sub_q_text, title="📝 Sub-Queries for Search", style="yellow"))

        # ── Focus areas ──────────────────────────────────────────
        focus_text = "\n".join(f"  • {fa}" for fa in request.focus_areas)
        console.print(Panel(focus_text, title="🎯 Focus Areas", style="magenta"))

        # ── Suggested sources ────────────────────────────────────
        source_text = "  " + ", ".join(
            s.replace("_", " ").title() for s in request.suggested_source_types
        )
        console.print(Panel(source_text, title="📚 Recommended Source Types", style="cyan"))
