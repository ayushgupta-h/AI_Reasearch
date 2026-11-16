"""
backend/report_cleaner.py
=========================
Normalizes, cleans, and sanitizes research report Markdown.

Guarantees:
- Strips any YAML frontmatter (fenced or unfenced metadata blocks).
- Strips raw LLM / internal debug metadata (e.g. title: "...", generated_at: "...").
- Strips concatenated duplicate revisions and intermediate agent headers.
- Strips appended raw validation/audit ascii appendixes (Citation Validation Appendix,
  Source Credibility Assessment) while preserving real content.
- Preserves genuine report sections, headings, tables, markdown lists, and citations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# Regex patterns matching YAML frontmatter and unfenced metadata headers
_FRONTMATTER_FENCED = re.compile(r"^---\s*\n.*?\n---\s*\n?", re.DOTALL)
_FRONTMATTER_ANYWHERE = re.compile(r"\n---\s*\n.*?\n---\s*\n?", re.DOTALL)

# Loose metadata lines (e.g. title: "..." generated_at: "..." provider: "...")
_METADATA_LINE_PATTERN = re.compile(
    r"(?m)^(?:title|generated_at|provider|research_question|critic_score|revisions|revision_count|"
    r"max_revisions|sources_analyzed|sources_cited|citations_checked|citations_verified|"
    r"claims_verified|claims_unverified|claims_contradicted|contradictions_detected|"
    r"citation_pass_rate|source_credibility_evaluated):\s*.*$\n?",
    re.IGNORECASE,
)

# Appended internal debug appendices that should not be in the final user-facing report body
_DEBUG_APPENDIX_PATTERNS = [
    re.compile(r"\n#{1,3}\s*Citation Validation Appendix.*", re.DOTALL | re.IGNORECASE),
    re.compile(r"\nCitation Validation Appendix\s*\n.*", re.DOTALL | re.IGNORECASE),
    re.compile(r"\n#{1,3}\s*Source Credibility Assessment.*", re.DOTALL | re.IGNORECASE),
    re.compile(r"\nSource Credibility Assessment\s*\n.*", re.DOTALL | re.IGNORECASE),
]


def extract_frontmatter_dict(markdown: str) -> Tuple[Dict[str, Any], str]:
    """Extract metadata key-values from frontmatter if present, and return (metadata, clean_body)."""
    if not markdown:
        return {}, ""

    metadata: Dict[str, Any] = {}
    content = markdown

    # Check for fenced frontmatter at start
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if match:
        raw_meta, content = match.groups()
        for line in raw_meta.splitlines():
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    metadata[k] = v

    return metadata, content


def clean_report_markdown(markdown: str) -> str:
    """Normalize and strip all internal metadata, duplicate frontmatters, and debug appendices.

    Guarantees that the returned string is pure, clean Markdown ready for
    professional rendering.
    """
    if not markdown or not isinstance(markdown, str):
        return ""

    text = markdown.strip()

    # 1. Strip fenced YAML frontmatters
    text = _FRONTMATTER_FENCED.sub("", text)
    text = _FRONTMATTER_ANYWHERE.sub("\n\n", text)

    # 2. Strip any unfenced loose metadata headers (often created during multi-revision concatenations)
    text = _METADATA_LINE_PATTERN.sub("", text)

    # 3. Strip appended debug appendixes (the UI presents these in dedicated Sources & Evidence panels)
    for pattern in _DEBUG_APPENDIX_PATTERNS:
        text = pattern.sub("", text)

    # 4. Remove duplicate repetitive titles at the very start (e.g., # Title followed immediately by Title)
    lines = text.splitlines()
    cleaned_lines: List[str] = []
    seen_first_h1 = False
    first_h1_text = ""

    for line in lines:
        stripped = line.strip()
        # Detect if a plain text line repeats the first H1 title exactly
        if stripped.startswith("# "):
            h1_val = stripped.lstrip("# ").strip().lower()
            if not seen_first_h1:
                seen_first_h1 = True
                first_h1_text = h1_val
                cleaned_lines.append(line)
            elif h1_val == first_h1_text:
                continue  # duplicate top heading
            else:
                cleaned_lines.append(line)
        elif (
            seen_first_h1
            and len(cleaned_lines) < 6
            and stripped.lower() == first_h1_text
        ):
            # Duplicate unformatted title line right under H1
            continue
        else:
            cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # 5. Clean excessive blank lines (more than 2 consecutive newlines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return text
