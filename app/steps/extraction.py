"""
extraction.py - the whole text-reading part of the diagram:

  Can text be extracted directly?
     YES -> (PDF only) Images present in PDF?
               NO  -> Extract Raw Data
               YES -> OCR flow (read the pictures too) -> Extract Raw Data
     NO  -> OCR flow: Image Preprocessing -> OCR -> OCR Success?
               YES -> Extract Raw Data
               NO  -> raise OCR_FAILED

Every decision is written into `decisions` so it appears in the audit log.

One practical detail: when a PDF already has good text and only contains a logo,
OCR of the picture is a bonus.  If it finds nothing (or OCR is not installed) we
keep the good text instead of failing the whole letter.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from schemas import ErrorCode, PipelineError
from app.steps.ocr import OcrError, ocr_file, ocr_pdf_embedded_images

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

MIN_TEXT_CHARS = 50        # fewer readable characters than this = "no direct text"
MAX_TEXT_CHARS = 30000     # a job letter is short; protects LLM cost


@dataclass
class ExtractionResult:
    text: str
    method: str                              # direct / ocr / direct+ocr
    ocr_confidence: Optional[float] = None
    truncated: bool = False
    decisions: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Readers ("PDF / DOCX / Image Readers")
# ----------------------------------------------------------------------------
def _pdf_text(file_path: Path) -> str:
    with fitz.open(file_path) as doc:
        return "\n\n".join(page.get_text("text", sort=True) for page in doc)


def _table_lines(table) -> list[str]:
    lines = []
    for row in table.rows:
        cells = dict.fromkeys(c.text.strip() for c in row.cells if c.text.strip())
        if cells:
            lines.append(" | ".join(cells))
    return lines


def _docx_text(file_path: Path) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(file_path))
    parts: list[str] = []

    def add_header_footer(part) -> None:
        if part.is_linked_to_previous:
            return
        for p in part.paragraphs:
            if p.text.strip():
                parts.append(p.text.strip())
        for t in part.tables:
            parts.extend(_table_lines(t))

    # the employer LETTERHEAD is usually stored in the page header
    for section in doc.sections:
        add_header_footer(section.header)

    for child in doc.element.body.iterchildren():   # keeps paragraphs/tables in order
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, doc).text.strip()
            if text:
                parts.append(text)
        elif tag == "tbl":
            parts.extend(_table_lines(Table(child, doc)))

    for section in doc.sections:
        add_header_footer(section.footer)

    return "\n".join(parts)


def read_direct_text(file_path: Path, detected_type: str) -> str:
    if detected_type == "pdf":
        return _pdf_text(file_path)
    if detected_type == "docx":
        return _docx_text(file_path)
    if detected_type in {"text", "unknown"}:
        return file_path.read_bytes().decode("utf-8", errors="replace")
    return ""      # JPEG / PNG have no text layer


def _has_enough_text(text: str) -> bool:
    return len(re.sub(r"\s+", "", text)) >= MIN_TEXT_CHARS


def _clean(text: str) -> tuple[str, bool]:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS], True
    return text, False


# ----------------------------------------------------------------------------
# The decision flow
# ----------------------------------------------------------------------------
def extract_raw_text(
    file_path: Path, detected_type: str, image_count: int = 0
) -> ExtractionResult:
    """`image_count` comes from File Metadata Extraction (pictures inside the PDF)."""
    decisions: list[str] = []

    direct = read_direct_text(file_path, detected_type)
    can_direct = _has_enough_text(direct)
    decisions.append(f"Can text be extracted directly? {'YES' if can_direct else 'NO'}")

    if detected_type in {"text", "unknown"}:
        text, truncated = _clean(direct)
        return ExtractionResult(text, "direct", None, truncated, decisions)

    # ---------------- YES branch ----------------
    if can_direct:
        if detected_type != "pdf":
            text, truncated = _clean(direct)
            return ExtractionResult(text, "direct", None, truncated, decisions)

        has_images = image_count > 0
        decisions.append(f"Images present in PDF? {'YES' if has_images else 'NO'}")
        if not has_images:
            text, truncated = _clean(direct)
            return ExtractionResult(text, "direct", None, truncated, decisions)

        # Images present -> OCR flow on the pictures, added to the direct text
        try:
            ocr = ocr_pdf_embedded_images(file_path)
        except OcrError as exc:
            decisions.append(f"OCR of PDF images skipped ({exc}) - using the PDF text layer")
            text, truncated = _clean(direct)
            return ExtractionResult(text, "direct", None, truncated, decisions)

        if ocr.text.strip():
            decisions.append(
                f"OCR read text from {ocr.images_processed} image(s) "
                f"(confidence {ocr.confidence:.0f}%)"
            )
            text, truncated = _clean(direct + "\n\n[TEXT FOUND IN IMAGES]\n" + ocr.text)
            return ExtractionResult(text, "direct+ocr", ocr.confidence, truncated, decisions)

        decisions.append("Images contain no readable text (e.g. a logo) - using the PDF text layer")
        text, truncated = _clean(direct)
        return ExtractionResult(text, "direct", None, truncated, decisions)

    # ---------------- NO branch: OCR flow ----------------
    try:
        ocr = ocr_file(file_path, detected_type)
    except OcrError as exc:
        decisions.append(f"OCR Success? NO - {exc}")
        raise PipelineError(ErrorCode.OCR_FAILED, details=f"OCR failed: {exc}") from exc

    decisions.append(f"OCR Success? YES (confidence {ocr.confidence:.0f}%)")
    text, truncated = _clean(ocr.text)
    return ExtractionResult(text, "ocr", ocr.confidence, truncated, decisions)