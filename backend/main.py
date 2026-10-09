"""
Research AI - Main CLI Entry Point
====================================
Command-line interface for the Multi-Agent Research Orchestrator.

Usage:
  python main.py
  python main.py --topic "Future of Quantum Computing"
  python main.py --topic "Graph Neural Networks" --provider gemini --max-revisions 2
  python main.py --arch
  python main.py --history
  python main.py --history --limit 10
"""

import sys
import os
import argparse

# Ensure UTF-8 stdout on Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from orchestrator import MultiAgentOrchestrator, display_orchestration_architecture
from session_manager import SessionManager, PipelineTimer

console = Console()


def print_banner():
    banner_text = (
        "[bold cyan]╔════════════════════════════════════════════════════════════════════╗[/bold cyan]\n"
        "[bold cyan]║[/bold cyan]           [bold white]🧠 RESEARCH AI — MULTI-AGENT ORCHESTRATION[/bold white]               [bold cyan]║[/bold cyan]\n"
        "[bold cyan]║[/bold cyan]  [dim]Autonomous Multi-Agent Intelligence, Cross-Source Verification &[/dim]  [bold cyan]║[/bold cyan]\n"
        "[bold cyan]║[/bold cyan]  [dim]Self-Correcting Publication-Grade Research Reporting (LangGraph)[/dim]   [bold cyan]║[/bold cyan]\n"
        "[bold cyan]╚════════════════════════════════════════════════════════════════════╝[/bold cyan]"
    )
    console.print(banner_text)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Autonomous Multi-Agent AI Research System powered by LangGraph"
    )
    parser.add_argument(
        "-t", "--topic",
        type=str,
        default=None,
        help="Research topic or question to investigate",
    )
    parser.add_argument(
        "-p", "--provider",
        type=str,
        choices=["gemini", "huggingface", "hf"],
        default="gemini",
        help="LLM provider to use (default: gemini)",
    )
    parser.add_argument(
        "-r", "--max-revisions",
        type=int,
        default=2,
        help="Maximum self-correction revision cycles by Critic & Refiner (default: 2)",
    )
    parser.add_argument(
        "-s", "--threshold",
        type=int,
        default=8,
        help="Critic score threshold (1-10) required for publication approval (default: 8)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        default="reports",
        help="Directory to save generated Markdown reports (default: reports)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run autonomously without interactive query intake confirmations",
    )
    parser.add_argument(
        "--arch",
        action="store_true",
        help="Display the multi-agent architecture overview and exit",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Display past research sessions from the local database and exit",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit --history output to the N most recent sessions",
    )
    return parser.parse_args()


def main():
    print_banner()
    args = parse_args()

    if args.arch:
        display_orchestration_architecture()
        return

    # ── History mode: show past sessions and exit ──────────────────────
    if args.history:
        sm = SessionManager()
        sm.display_history(limit=args.limit)
        return

    topic = args.topic
    if not topic:
        console.print("\n[bold yellow]💡 Enter your research topic or question below:[/bold yellow]")
        topic = Prompt.ask("[bold green]🔬 Research Topic[/bold green]").strip()

    if not topic:
        console.print("[bold red]❌ No topic provided. Exiting.[/bold red]")
        sys.exit(1)

    interactive = not args.non_interactive

    orchestrator = MultiAgentOrchestrator(
        provider=args.provider,
        max_revisions=args.max_revisions,
        approval_threshold=args.threshold,
        output_dir=args.output_dir,
        interactive=interactive,
    )

    # ── Run pipeline with timer ──────────────────────────────────
    with PipelineTimer() as timer:
        result = orchestrator.run(topic)

    # ── Persist session ────────────────────────────────────────
    sm = SessionManager()
    session_id = sm.save_session(result, duration_seconds=timer.elapsed)

    if result.get("status") == "completed":
        console.print(Panel(
            f"🎉 [bold green]Research Pipeline Completed Successfully![/bold green]\n"
            f"Saved Report : [cyan]{result.get('saved_report_path')}[/cyan]\n"
            f"Critic Score : [bold]{result.get('critic_score')}/10[/bold]\n"
            f"Duration     : [dim]{timer.elapsed:.1f}s[/dim]\n"
            f"Session ID   : [dim]{session_id[:16]}…[/dim]\n\n"
            f"[dim]View all sessions with:[/dim] [yellow]python main.py --history[/yellow]",
            style="bold green"
        ))


if __name__ == "__main__":
    main()
