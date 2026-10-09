import sys
from typing import Dict, Any, Optional

# Ensure UTF-8 stdout on Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel

from orchestrator import MultiAgentOrchestrator, display_orchestration_architecture

console = Console()


def run_research_pipeline(
    topic: str,
    provider: Optional[str] = None,
    max_revisions: int = 2,
    approval_threshold: int = 8,
    interactive: bool = True,
    output_dir: str = "reports",
) -> Dict[str, Any]:
    """
    Executes the complete Multi-Agent Research Pipeline powered by LangGraph.

    Args:
        topic: The user's research topic or prompt.
        provider: "gemini" or "huggingface" (None defaults to .env).
        max_revisions: Maximum autonomous self-correction cycles (default 2).
        approval_threshold: Target critic score out of 10 to approve publication (default 8).
        interactive: Whether to prompt for query intake confirmation (default True).
        output_dir: Directory where the generated Markdown reports will be stored (default 'reports').

    Returns:
        Dict representing the finalized research state.
    """
    orchestrator = MultiAgentOrchestrator(
        provider=provider or "gemini",
        max_revisions=max_revisions,
        approval_threshold=approval_threshold,
        output_dir=output_dir,
        interactive=interactive,
    )

    return orchestrator.run(topic)


if __name__ == "__main__":
    display_orchestration_architecture()
    topic = input("\n🔬 Enter a research topic: ").strip()
    if topic:
        run_research_pipeline(topic)