"""Report export tests for Markdown, TXT, DOCX and PDF."""

from __future__ import annotations

from pathlib import Path

import pytest

from report_exporter import ReportExporter

MARKDOWN = """# Findings

Artificial intelligence improves diagnostic accuracy.

## Details

- Point one
- Point two

Sources: https://example.com/study
"""


class TestSingleFormatExports:
    def test_markdown_export_writes_styled_copy(self, tmp_path: Path) -> None:
        target = ReportExporter().export_single(MARKDOWN, "md", tmp_path / "report.md")
        assert target.suffix == ".md"
        assert target.is_file()
        text = target.read_text(encoding="utf-8")
        assert "Findings" in text
        assert "diagnostic accuracy" in text

    def test_txt_export_contains_plain_content(self, tmp_path: Path) -> None:
        target = ReportExporter().export_single(MARKDOWN, "txt", tmp_path / "report.txt")
        assert target.suffix == ".txt"
        text = target.read_text(encoding="utf-8")
        # Headings are upper-cased and underlined, not left as markdown.
        assert "FINDINGS" in text.upper()
        assert "diagnostic accuracy" in text
        assert "#" not in text

    def test_docx_export_is_a_valid_zip_package(self, tmp_path: Path) -> None:
        target = ReportExporter().export_single(MARKDOWN, "docx", tmp_path / "report.docx")
        assert target.suffix == ".docx"
        assert target.read_bytes()[:2] == b"PK"

    def test_pdf_export_starts_with_pdf_header(self, tmp_path: Path) -> None:
        target = ReportExporter().export_single(MARKDOWN, "pdf", tmp_path / "report.pdf")
        assert target.suffix == ".pdf"
        assert target.read_bytes()[:4] == b"%PDF"

    def test_extension_is_forced_from_the_format(self, tmp_path: Path) -> None:
        target = ReportExporter().export_single(MARKDOWN, "txt", tmp_path / "wrong.md")
        assert target.suffix == ".txt"
        assert target.is_file()

    def test_unsupported_format_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            ReportExporter().export_single(MARKDOWN, "xlsx", tmp_path / "report.xlsx")


class TestExportAll:
    def test_export_all_produces_every_format(self, tmp_path: Path) -> None:
        paths = ReportExporter().export_all(MARKDOWN, tmp_path / "quest_report")
        assert set(paths) == {"md", "txt", "docx", "pdf"}
        for fmt, path in paths.items():
            assert path is not None, f"{fmt} export failed"
            assert Path(path).is_file()
            assert Path(path).stat().st_size > 0

    def test_skip_flags_leave_optional_formats_empty(self, tmp_path: Path) -> None:
        paths = ReportExporter(skip_pdf=True, skip_docx=True).export_all(
            MARKDOWN, tmp_path / "quest_report"
        )
        assert paths["md"] and paths["txt"]
        assert paths["docx"] is None
        assert paths["pdf"] is None

    def test_export_all_survives_citation_style_selection(self, tmp_path: Path) -> None:
        paths = ReportExporter(skip_pdf=True, skip_docx=True).export_all(
            MARKDOWN, tmp_path / "styled", citation_style="mla"
        )
        assert paths["md"] is not None
        assert "diagnostic accuracy" in Path(paths["md"]).read_text(encoding="utf-8")
