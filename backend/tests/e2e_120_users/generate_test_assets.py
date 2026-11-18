"""Generate realistic multi-page test PDFs with methodology, findings, and limitations."""

from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors


def generate_research_pdf(file_path: Path, title: str, sections: dict[str, str]) -> Path:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(file_path), pagesize=letter, rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=54)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#1E293B'),
        spaceAfter=14
    )
    h2_style = ParagraphStyle(
        'DocH2',
        parent=styles['Heading2'],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#0F172A'),
        spaceBefore=12,
        spaceAfter=6
    )
    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#334155'),
        spaceAfter=8
    )

    story = [
        Paragraph(title, title_style),
        Paragraph("<b>Authors:</b> Dr. Elena Vance, Dr. Marcus Thorne | <i>Research AI Testing Consortium</i>", body_style),
        Spacer(1, 12),
    ]

    for header, content in sections.items():
        story.append(Paragraph(header, h2_style))
        story.append(Paragraph(content, body_style))
        story.append(Spacer(1, 8))
        if "Methodology" in header or "Main Findings" in header:
            story.append(PageBreak())

    doc.build(story)
    return file_path


def build_default_test_papers(dest_dir: Path) -> tuple[Path, Path, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)

    paper_a = dest_dir / "paper_a_clinical_diagnostics.pdf"
    generate_research_pdf(
        paper_a,
        "Generative AI in Clinical Diagnostics: Methods, Validation, and Limitations",
        {
            "Executive Summary": (
                "This study investigates the deployment of multimodal transformer architectures in clinical pathology "
                "and radiology. Across 15,000 clinical cases, generative models demonstrated a 94.2% diagnostic concordance "
                "with senior board-certified pathologists while reducing diagnostic turnaround times by 23%."
            ),
            "Methodology": (
                "We performed a retrospective double-blind trial across three tertiary medical centers. Data collection "
                "encompassed 15,000 anonymized whole-slide biopsy scans and corresponding EHR records between 2021 and 2024. "
                "The primary evaluation metric was Cohen's kappa coefficient against multi-expert consensus diagnoses."
            ),
            "Main Findings": (
                "1. Multimodal generative models achieved 94.2% concordance with expert consensus.\n"
                "2. Turnaround time decreased by an average of 4.2 hours per biopsy evaluation.\n"
                "3. Rare pathology recognition remained a challenge, with error rates reaching 12.8% on uncommon malignancies."
            ),
            "Limitations and Challenges": (
                "The architecture exhibits occasional hallucination in atypical histopathological margins. High computational "
                "demands (minimum 4x A100 GPUs per concurrent batch) present cost barriers for community health systems."
            ),
            "Conclusion": (
                "Generative AI systems serve as highly reliable triage and second-opinion tools in diagnostic pipelines, provided "
                "mandatory human-in-the-loop validation is enforced for ambiguous findings."
            )
        }
    )

    paper_b = dest_dir / "paper_b_oncology_pathology.pdf"
    generate_research_pdf(
        paper_b,
        "Deep Learning for Oncology Pathology: Comparative Benchmark Study",
        {
            "Executive Summary": (
                "An empirical benchmark of deep convolutional and vision-transformer models applied to oncology tissue biopsies. "
                "While automated detection achieves 91.8% sensitivity on primary tumors, diagnostic divergence emerges on edge tissue "
                "samples and stained slide variations."
            ),
            "Methodology": (
                "A prospective multi-center validation on 12,000 whole-slide images across five diagnostic laboratories. "
                "Models tested include ResNet-50, Swin Transformer v2, and a domain-fine-tuned ViT-Huge architecture."
            ),
            "Main Findings": (
                "1. Vision transformers achieved 91.8% sensitivity and 89.4% specificity on primary tumor margin detection.\n"
                "2. Model performance suffered significant degradation (up to 18.5% loss in F1 score) across different laboratory staining protocols.\n"
                "3. Automated concordance contradicted manual pathologist assessments in 8.2% of borderline dysplasia cases."
            ),
            "Limitations and Challenges": (
                "Significant domain shift between staining batches severely undermines out-of-distribution generalization. "
                "Lack of standardized pre-processing pipelines remains the primary impediment to clinical adoption."
            ),
            "Conclusion": (
                "Automated oncology screening algorithms require domain-adaptation normalization layers prior to clinical deployment "
                "to overcome staining protocol variances."
            )
        }
    )

    corrupted_pdf = dest_dir / "corrupted_document.pdf"
    corrupted_pdf.write_bytes(b"%PDF-1.4 ... corrupted binary content ... not a valid pdf sequence ...")

    return paper_a, paper_b, corrupted_pdf


if __name__ == "__main__":
    p_a, p_b, p_c = build_default_test_papers(Path("backend/tests/e2e_120_users/fixtures"))
    print(f"Generated test PDFs:\n - {p_a}\n - {p_b}\n - {p_c}")
