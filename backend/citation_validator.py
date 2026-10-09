"""
Citation Validator
==================
Validates every URL found in a research report by checking its HTTP
reachability, extracting the domain name, and tagging each citation
as ``verified`` (2xx/3xx) or ``broken`` (4xx/5xx/timeout/error).

Usage::

    from citation_validator import CitationValidator, CitationReport

    validator = CitationValidator()
    report   = validator.validate_report(markdown_text)

    # Rich console display
    validator.display_report(report)

    # Inject a citation-status appendix into the Markdown report
    new_md = validator.inject_appendix(markdown_text, report)

The validator runs all HTTP checks concurrently via
``concurrent.futures.ThreadPoolExecutor`` so validation of dozens of
URLs typically completes in a few seconds.
"""

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from urllib.parse import urlparse

import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Ensure UTF-8 stdout on Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()

# ─── Data Models ─────────────────────────────────────────────────────────────

# Regex that matches http/https URLs inside Markdown text.
# Handles bare URLs, Markdown links [text](url), and inline code.
_URL_PATTERN = re.compile(
    r"https?://"                        # scheme
    r"(?:[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)"  # body
    r"(?<![.,;:'\"\)\]\s])",            # don't capture trailing punctuation
    re.IGNORECASE,
)


@dataclass
class CitationResult:
    """Result for a single validated URL."""
    url: str
    domain: str
    status_code: Optional[int]          # HTTP status; None if connection failed
    status_label: str                   # "verified" | "broken" | "timeout" | "error"
    response_time_ms: Optional[float]   # Round-trip time in milliseconds
    error_message: Optional[str] = None # Set only when status_label != "verified"

    @property
    def is_live(self) -> bool:
        return self.status_label == "verified"


@dataclass
class CitationReport:
    """Aggregated validation results for a full report."""
    total: int = 0
    verified: int = 0
    broken: int = 0
    results: List[CitationResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return (self.verified / self.total * 100) if self.total else 0.0

    def by_domain(self) -> Dict[str, List[CitationResult]]:
        """Group results by domain name."""
        grouped: Dict[str, List[CitationResult]] = {}
        for r in self.results:
            grouped.setdefault(r.domain, []).append(r)
        return grouped


# ─── Core Validator ───────────────────────────────────────────────────────────


class CitationValidator:
    """
    Extracts and validates every URL found in a Markdown research report.

    Parameters
    ----------
    timeout : float
        Per-URL HTTP request timeout in seconds (default 8 s).
    max_workers : int
        Thread-pool size for concurrent HEAD/GET requests (default 12).
    user_agent : str
        User-Agent header sent with every request so servers don't block us.
    """

    _DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; ResearchAI-CitationBot/1.0; "
            "+https://github.com/your-org/research-ai)"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }

    def __init__(
        self,
        timeout: float = 8.0,
        max_workers: int = 12,
        user_agent: Optional[str] = None,
    ) -> None:
        self.timeout = timeout
        self.max_workers = max_workers
        if user_agent:
            self._DEFAULT_HEADERS["User-Agent"] = user_agent

    # ── URL extraction ────────────────────────────────────────────

    @staticmethod
    def extract_urls(text: str) -> List[str]:
        """Return a deduplicated, order-preserving list of URLs found in *text*."""
        raw = _URL_PATTERN.findall(text)
        seen: set = set()
        unique: List[str] = []
        for url in raw:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique

    @staticmethod
    def extract_domain(url: str) -> str:
        """Return the netloc (domain + optional port) of *url*."""
        try:
            return urlparse(url).netloc or url
        except Exception:
            return url

    # ── Single-URL check ─────────────────────────────────────────

    def _check_url(self, url: str) -> CitationResult:
        """
        Perform an HTTP HEAD (falling back to GET) request against *url*.

        Returns a :class:`CitationResult` with the validation outcome.
        """
        domain = self.extract_domain(url)
        start = time.perf_counter()
        try:
            resp = requests.head(
                url,
                headers=self._DEFAULT_HEADERS,
                timeout=self.timeout,
                allow_redirects=True,
            )
            # Some servers reject HEAD; retry with GET when we get 405 / 403.
            if resp.status_code in (403, 405):
                resp = requests.get(
                    url,
                    headers=self._DEFAULT_HEADERS,
                    timeout=self.timeout,
                    stream=True,          # don't download the body
                    allow_redirects=True,
                )

            elapsed_ms = (time.perf_counter() - start) * 1000
            code = resp.status_code

            if 200 <= code < 400:
                label = "verified"
                err = None
            else:
                label = "broken"
                err = f"HTTP {code}"

            return CitationResult(
                url=url,
                domain=domain,
                status_code=code,
                status_label=label,
                response_time_ms=round(elapsed_ms, 1),
                error_message=err,
            )

        except requests.exceptions.Timeout:
            return CitationResult(
                url=url,
                domain=domain,
                status_code=None,
                status_label="timeout",
                response_time_ms=round((time.perf_counter() - start) * 1000, 1),
                error_message=f"Timed out after {self.timeout}s",
            )
        except requests.exceptions.ConnectionError as exc:
            return CitationResult(
                url=url,
                domain=domain,
                status_code=None,
                status_label="broken",
                response_time_ms=round((time.perf_counter() - start) * 1000, 1),
                error_message=f"Connection error: {exc}",
            )
        except Exception as exc:
            return CitationResult(
                url=url,
                domain=domain,
                status_code=None,
                status_label="error",
                response_time_ms=round((time.perf_counter() - start) * 1000, 1),
                error_message=str(exc),
            )

    # ── Batch validation ─────────────────────────────────────────

    def validate_urls(self, urls: List[str]) -> CitationReport:
        """
        Validate a list of *urls* concurrently.

        Returns a fully populated :class:`CitationReport`.
        """
        if not urls:
            return CitationReport()

        results: List[CitationResult] = []

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(urls))) as pool:
            future_map = {pool.submit(self._check_url, url): url for url in urls}
            for future in as_completed(future_map):
                try:
                    results.append(future.result())
                except Exception as exc:
                    url = future_map[future]
                    results.append(CitationResult(
                        url=url,
                        domain=self.extract_domain(url),
                        status_code=None,
                        status_label="error",
                        response_time_ms=None,
                        error_message=str(exc),
                    ))

        verified = sum(1 for r in results if r.is_live)
        return CitationReport(
            total=len(results),
            verified=verified,
            broken=len(results) - verified,
            results=sorted(results, key=lambda r: (not r.is_live, r.domain)),
        )

    def validate_report(self, markdown_text: str) -> CitationReport:
        """
        Extract every URL from *markdown_text* and validate them all.

        This is the primary entry point — pass the raw Markdown report string.
        """
        urls = self.extract_urls(markdown_text)

        if not urls:
            console.print("  [dim yellow]⚠ No URLs found in report — skipping citation validation.[/dim yellow]")
            return CitationReport()

        console.print(
            f"  [cyan]🔗 Citation Validator: checking {len(urls)} unique URL(s) concurrently...[/cyan]"
        )
        return self.validate_urls(urls)

    # ── Rich display ─────────────────────────────────────────────

    def display_report(self, report: CitationReport) -> None:
        """Print a Rich table summarising the citation validation results."""
        if report.total == 0:
            console.print(Panel("No citations to validate.", style="dim"))
            return

        table = Table(
            title=f"🔗 Citation Validation Report  ({report.verified}/{report.total} live  |  {report.pass_rate:.0f}% pass rate)",
            show_lines=True,
        )
        table.add_column("#", style="dim", width=3)
        table.add_column("Domain", style="bold cyan", no_wrap=True)
        table.add_column("Status", width=10)
        table.add_column("HTTP", width=6)
        table.add_column("ms", width=7)
        table.add_column("URL", style="dim", overflow="fold")

        for i, r in enumerate(report.results, 1):
            if r.is_live:
                status_str = "[bold green]✅ Live[/bold green]"
            elif r.status_label == "timeout":
                status_str = "[yellow]⏱ Timeout[/yellow]"
            else:
                status_str = "[bold red]❌ Broken[/bold red]"

            http_str = str(r.status_code) if r.status_code else "—"
            ms_str = f"{r.response_time_ms:.0f}" if r.response_time_ms else "—"
            table.add_row(str(i), r.domain, status_str, http_str, ms_str, r.url)

        console.print(Panel(table, style="blue"))

        # Summary bar
        broken_list = [r for r in report.results if not r.is_live]
        if broken_list:
            console.print(
                f"  [bold red]⚠ {report.broken} broken/unreachable citation(s):[/bold red]"
            )
            for r in broken_list:
                console.print(
                    f"    [red]• {r.url}[/red]  [dim]({r.error_message})[/dim]"
                )

    # ── Markdown appendix injector ────────────────────────────────

    @staticmethod
    def inject_appendix(markdown_text: str, report: CitationReport) -> str:
        """
        Append a *Citation Validation Appendix* section to *markdown_text*.

        Returns the augmented Markdown string. The original content is unchanged;
        the appendix is always added at the very end.
        """
        if report.total == 0:
            return markdown_text

        lines = [
            "\n\n---\n",
            "## 📋 Citation Validation Appendix\n",
            f"> Auto-generated by ResearchAI Citation Validator  \n",
            f"> **{report.verified}/{report.total} sources live** ({report.pass_rate:.0f}% pass rate)\n\n",
            "| # | Domain | Status | HTTP | URL |\n",
            "|---|--------|--------|------|-----|\n",
        ]

        for i, r in enumerate(report.results, 1):
            if r.is_live:
                status = "✅ Live"
            elif r.status_label == "timeout":
                status = "⏱ Timeout"
            else:
                status = "❌ Broken"

            http = str(r.status_code) if r.status_code else "—"
            # Shorten URL for table readability
            short_url = r.url if len(r.url) <= 80 else r.url[:77] + "..."
            lines.append(f"| {i} | `{r.domain}` | {status} | {http} | {short_url} |\n")

        return markdown_text + "".join(lines)


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    _SAMPLE = """
    # Sample Report

    Sources:
    - https://arxiv.org/abs/2303.08774
    - https://en.wikipedia.org/wiki/Large_language_model
    - https://huggingface.co/papers
    - https://this-url-does-not-exist-xyz-999.com/broken
    - https://openai.com/research/gpt-4
    """

    validator = CitationValidator()
    report = validator.validate_report(_SAMPLE)
    validator.display_report(report)
    augmented = validator.inject_appendix(_SAMPLE, report)
    print("\n--- Augmented Markdown (tail) ---")
    print(augmented.split("---")[-1])
