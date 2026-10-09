"""
Report Exporter
===============
Converts a Markdown research report into DOCX and PDF formats.

Usage::

    from report_exporter import ReportExporter

    exporter = ReportExporter()
    paths = exporter.export_all(
        markdown_text=full_document,
        base_path=Path("reports/report_20240101_120000_topic"),
    )
    # paths = {"md": Path(...), "docx": Path(...), "pdf": Path(...)}

Dependencies (see requirements.txt):
    python-docx   — DOCX generation
    markdown      — Markdown → HTML (for PDF pipeline)
    weasyprint    — HTML → PDF (primary)
    reportlab     — Plain-text PDF (fallback if WeasyPrint unavailable)
"""

import re
import sys
from pathlib import Path
from typing import Dict, Optional

from rich.console import Console
from rich.panel import Panel

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()


# ─── DOCX Export ─────────────────────────────────────────────────────────────


def _export_docx(markdown_text: str, output_path: Path) -> Path:
    """
    Convert *markdown_text* to a styled .docx file at *output_path*.

    Handles:
    - H1 / H2 / H3 headings
    - Bullet lists (lines starting with ``-`` or ``*``)
    - Numbered lists (lines starting with ``1.`` etc.)
    - Horizontal rules (``---``)
    - Code blocks (``` fenced blocks)
    - YAML frontmatter (rendered as a styled metadata table)
    - Regular paragraphs (blank-line delimited)
    """
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()

    # ── Page margins ──────────────────────────────────────────────
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1.2)
        section.right_margin = Inches(1.2)

    # ── Custom style helpers ──────────────────────────────────────
    def _add_heading(text: str, level: int) -> None:
        doc.add_heading(text.strip(), level=level)

    def _add_paragraph(text: str, style: str = "Normal") -> None:
        p = doc.add_paragraph(style=style)
        # Bold (**text**) and italic (*text*) inline rendering
        parts = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)", text)
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                run = p.add_run(part[2:-2])
                run.bold = True
            elif part.startswith("`") and part.endswith("`"):
                run = p.add_run(part[1:-1])
                run.font.name = "Courier New"
                run.font.size = Pt(9)
            elif part.startswith("*") and part.endswith("*"):
                run = p.add_run(part[1:-1])
                run.italic = True
            else:
                p.add_run(part)

    def _add_code_block(code: str) -> None:
        p = doc.add_paragraph(style="Normal")
        run = p.add_run(code)
        run.font.name = "Courier New"
        run.font.size = Pt(8.5)
        run.font.color.rgb = RGBColor(0x22, 0x22, 0x22)
        # Light grey background shading via XML
        pPr = p._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "F0F0F0")
        pPr.append(shd)

    def _add_horizontal_rule() -> None:
        doc.add_paragraph("─" * 60, style="Normal")

    def _render_frontmatter(fm_text: str) -> None:
        """Render YAML frontmatter as a small metadata table."""
        doc.add_heading("Document Metadata", level=2)
        table = doc.add_table(rows=0, cols=2)
        table.style = "Light Shading Accent 1"
        for line in fm_text.strip().splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                row = table.add_row()
                row.cells[0].text = key.strip()
                row.cells[1].text = val.strip().strip('"')
        doc.add_paragraph()

    # ── Parse and render ──────────────────────────────────────────
    lines = markdown_text.splitlines()
    i = 0
    in_code_block = False
    code_lines: list[str] = []
    frontmatter_lines: list[str] = []
    in_frontmatter = False
    frontmatter_done = False

    while i < len(lines):
        line = lines[i]

        # YAML frontmatter
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            i += 1
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
                frontmatter_done = True
                _render_frontmatter("\n".join(frontmatter_lines))
            else:
                frontmatter_lines.append(line)
            i += 1
            continue

        # Fenced code blocks
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lines = []
            else:
                _add_code_block("\n".join(code_lines))
                in_code_block = False
                code_lines = []
            i += 1
            continue
        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        # Headings
        if line.startswith("### "):
            _add_heading(line[4:], 3)
        elif line.startswith("## "):
            _add_heading(line[3:], 2)
        elif line.startswith("# "):
            _add_heading(line[2:], 1)

        # Horizontal rule
        elif re.match(r"^-{3,}$", line.strip()) or re.match(r"^={3,}$", line.strip()):
            _add_horizontal_rule()

        # Bullet list
        elif re.match(r"^[\-\*\+]\s+", line):
            _add_paragraph(re.sub(r"^[\-\*\+]\s+", "", line), style="List Bullet")

        # Numbered list
        elif re.match(r"^\d+\.\s+", line):
            _add_paragraph(re.sub(r"^\d+\.\s+", "", line), style="List Number")

        # Blockquote
        elif line.startswith("> "):
            _add_paragraph(line[2:], style="Normal")

        # Blank line — skip
        elif line.strip() == "":
            pass

        # Regular paragraph
        else:
            _add_paragraph(line)

        i += 1

    doc.save(str(output_path))
    return output_path


# ─── PDF Export ──────────────────────────────────────────────────────────────

_PDF_CSS = """
@page {
    margin: 2cm 2.5cm;
    @bottom-center { content: counter(page) " / " counter(pages); font-size: 9pt; color: #888; }
}
body {
    font-family: "Georgia", "Times New Roman", serif;
    font-size: 11pt;
    line-height: 1.7;
    color: #1a1a1a;
    max-width: 100%;
}
h1 { font-size: 22pt; color: #1a2f5e; border-bottom: 2px solid #1a2f5e; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 16pt; color: #1a2f5e; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 28px; }
h3 { font-size: 13pt; color: #2c4a7c; margin-top: 20px; }
h4 { font-size: 11pt; color: #444; }
p  { margin: 0.5em 0 0.9em 0; text-align: justify; }
ul, ol { margin: 0.4em 0 0.8em 1.8em; }
li { margin-bottom: 0.3em; }
pre, code {
    font-family: "Courier New", monospace;
    font-size: 9.5pt;
    background: #f4f4f4;
    border: 1px solid #ddd;
    border-radius: 4px;
}
pre  { padding: 10px 14px; overflow-x: auto; }
code { padding: 1px 4px; }
blockquote {
    border-left: 4px solid #1a2f5e;
    margin: 0.8em 0;
    padding: 0.4em 1em;
    background: #f7f9fc;
    color: #555;
}
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 10pt; }
th { background: #1a2f5e; color: white; padding: 8px 10px; text-align: left; }
td { padding: 6px 10px; border: 1px solid #ddd; }
tr:nth-child(even) td { background: #f7f8fa; }
hr { border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }
a  { color: #1a2f5e; }
"""


def _export_pdf_weasyprint(html_content: str, output_path: Path) -> Path:
    """Primary PDF path: HTML → PDF via WeasyPrint."""
    from weasyprint import HTML, CSS  # type: ignore
    HTML(string=html_content).write_pdf(
        str(output_path),
        stylesheets=[CSS(string=_PDF_CSS)],
    )
    return output_path


def _export_pdf_reportlab(markdown_text: str, output_path: Path) -> Path:
    """Fallback PDF path: plain Markdown → PDF via ReportLab."""
    from reportlab.lib.pagesizes import A4  # type: ignore
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (  # type: ignore
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Preformatted,
    )

    styles = getSampleStyleSheet()
    # Custom styles
    styles.add(ParagraphStyle(
        "H1Style", parent=styles["Heading1"], fontSize=20, textColor=colors.HexColor("#1a2f5e"),
    ))
    styles.add(ParagraphStyle(
        "H2Style", parent=styles["Heading2"], fontSize=15, textColor=colors.HexColor("#1a2f5e"),
    ))
    styles.add(ParagraphStyle(
        "H3Style", parent=styles["Heading3"], fontSize=12, textColor=colors.HexColor("#2c4a7c"),
    ))

    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm,
    )

    story = []
    in_code_block = False
    code_acc: list[str] = []
    in_frontmatter = False
    frontmatter_done = False

    for i, line in enumerate(markdown_text.splitlines()):
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
                frontmatter_done = True
            continue  # Skip YAML frontmatter in plain PDF

        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_acc = []
            else:
                story.append(Preformatted("\n".join(code_acc), styles["Code"]))
                story.append(Spacer(1, 0.2*cm))
                in_code_block = False
                code_acc = []
            continue
        if in_code_block:
            code_acc.append(line)
            continue

        if line.startswith("# "):
            story.append(Paragraph(line[2:], styles["H1Style"]))
            story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1a2f5e")))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], styles["H2Style"]))
        elif line.startswith("### "):
            story.append(Paragraph(line[4:], styles["H3Style"]))
        elif re.match(r"^-{3,}$", line.strip()):
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        elif re.match(r"^[\-\*]\s+", line):
            txt = "• " + re.sub(r"^[\-\*]\s+", "", line)
            story.append(Paragraph(txt, styles["Normal"]))
        elif line.strip() == "":
            story.append(Spacer(1, 0.25*cm))
        else:
            # Escape ReportLab special chars
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # Bold / italic inline
            safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
            safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)
            story.append(Paragraph(safe, styles["Normal"]))

    doc.build(story)
    return output_path


def _markdown_to_html(markdown_text: str) -> str:
    """Convert Markdown to a full HTML document string."""
    import markdown as md  # type: ignore
    body = md.markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "toc", "nl2br"],
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Research Report</title></head>
<body>{body}</body>
</html>"""


def _export_pdf(markdown_text: str, output_path: Path) -> tuple[Path, str]:
    """
    Export PDF, trying WeasyPrint first, falling back to ReportLab.

    Returns (path, engine_used).
    """
    try:
        html = _markdown_to_html(markdown_text)
        _export_pdf_weasyprint(html, output_path)
        return output_path, "weasyprint"
    except ImportError:
        pass
    except Exception as wp_err:
        console.print(f"  [dim yellow]⚠ WeasyPrint PDF failed ({wp_err}), trying ReportLab...[/dim yellow]")

    try:
        _export_pdf_reportlab(markdown_text, output_path)
        return output_path, "reportlab"
    except ImportError:
        raise RuntimeError(
            "Neither weasyprint nor reportlab is installed. "
            "Run: pip install weasyprint  OR  pip install reportlab"
        )


# ─── Main Exporter Class ─────────────────────────────────────────────────────


class ReportExporter:
    """
    Multi-format report exporter: Markdown → DOCX + PDF.

    Parameters
    ----------
    skip_pdf  : bool  — Set True to skip PDF generation (useful in CI/test).
    skip_docx : bool  — Set True to skip DOCX generation.
    """

    def __init__(self, skip_pdf: bool = False, skip_docx: bool = False) -> None:
        self.skip_pdf = skip_pdf
        self.skip_docx = skip_docx

    def export_all(
        self,
        markdown_text: str,
        base_path: Path,
    ) -> Dict[str, Optional[Path]]:
        """
        Save the report in all supported formats.

        Parameters
        ----------
        markdown_text : str   — Full Markdown report content (incl. frontmatter).
        base_path     : Path  — Base path WITHOUT extension.
                                e.g. ``Path("reports/report_20240101_topic")``

        Returns
        -------
        Dict mapping format name to resolved Path (or None if skipped/failed).
        """
        paths: Dict[str, Optional[Path]] = {}

        # ── DOCX ─────────────────────────────────────────────────
        if not self.skip_docx:
            docx_path = base_path.with_suffix(".docx")
            try:
                console.print(f"  [dim]📝 Generating DOCX: {docx_path.name}...[/dim]")
                _export_docx(markdown_text, docx_path)
                paths["docx"] = docx_path.resolve()
                console.print(f"  [green]✅ DOCX saved → {docx_path.name}[/green]")
            except ImportError:
                console.print("  [yellow]⚠ python-docx not installed — DOCX export skipped.[/yellow]")
                paths["docx"] = None
            except Exception as exc:
                console.print(f"  [dim red]⚠ DOCX export failed: {exc}[/dim red]")
                paths["docx"] = None
        else:
            paths["docx"] = None

        # ── PDF ──────────────────────────────────────────────────
        if not self.skip_pdf:
            pdf_path = base_path.with_suffix(".pdf")
            try:
                console.print(f"  [dim]📄 Generating PDF: {pdf_path.name}...[/dim]")
                saved_path, engine = _export_pdf(markdown_text, pdf_path)
                paths["pdf"] = saved_path.resolve()
                console.print(f"  [green]✅ PDF saved → {pdf_path.name} [dim](via {engine})[/dim][/green]")
            except RuntimeError as exc:
                console.print(f"  [yellow]⚠ PDF export skipped: {exc}[/yellow]")
                paths["pdf"] = None
            except Exception as exc:
                console.print(f"  [dim red]⚠ PDF export failed: {exc}[/dim red]")
                paths["pdf"] = None
        else:
            paths["pdf"] = None

        return paths


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    _SAMPLE_MD = """---
title: "Test Research Report"
generated_at: "2024-01-01T12:00:00"
critic_score: 9/10
---

# AI in Healthcare: A Research Overview

## Executive Summary

This report explores the application of **artificial intelligence** in modern healthcare.

## Key Findings

- Large language models (LLMs) are being deployed in clinical settings
- Diagnostic accuracy improved by *up to 34%* in radiology tasks
- Regulatory frameworks lag behind technological development

## Technical Architecture

```python
model = load_model("medbert-large")
predictions = model.predict(patient_data)
```

## Conclusion

AI is rapidly transforming healthcare delivery across the globe.
"""

    exporter = ReportExporter()
    base = Path("reports") / "test_export_sample"
    base.parent.mkdir(exist_ok=True)
    result = exporter.export_all(_SAMPLE_MD, base)
    for fmt, path in result.items():
        status = str(path) if path else "SKIPPED"
        print(f"  {fmt.upper()}: {status}")
