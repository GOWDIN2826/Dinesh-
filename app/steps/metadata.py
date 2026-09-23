"""
metadata.py - diagram box "File Metadata Extraction (logging / audit)"

Collects facts about the file and saves them to the audit log.  In Batch 4 the
fraud check reuses them ("signs of tampering / edited document"):
  * PDF   -> creator / producer / created / modified dates
  * DOCX  -> author / last_modified_by / created / modified / revision
  * Image -> size + EXIF 'Software' tag (e.g. Photoshop)

If the file cannot even be opened (corrupted / password-protected) we stop with
INVALID_FILE_TYPE - it could never be read in the next steps anyway.
"""
import hashlib
from pathlib import Path

from schemas import ErrorCode, PipelineError

# PyMuPDF is imported as "pymupdf" in new versions and "fitz" in old ones
try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz


def _sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pdf_metadata(file_path: Path) -> dict:
    with fitz.open(file_path) as doc:
        if doc.needs_pass:
            raise PipelineError(
                ErrorCode.INVALID_FILE_TYPE,
                details="Password-protected PDF cannot be processed",
            )
        if doc.page_count == 0:
            raise PipelineError(
                ErrorCode.INVALID_FILE_TYPE, details="PDF has no pages"
            )
        info = doc.metadata or {}
        image_count = sum(len(page.get_images(full=True)) for page in doc)
        return {
            "page_count": doc.page_count,
            "image_count": image_count,
            "pdf_creator": info.get("creator"),
            "pdf_producer": info.get("producer"),
            "pdf_author": info.get("author"),
            "pdf_title": info.get("title"),
            "pdf_created": info.get("creationDate"),
            "pdf_modified": info.get("modDate"),
        }


def _docx_metadata(file_path: Path) -> dict:
    from docx import Document

    doc = Document(str(file_path))
    props = doc.core_properties

    def iso(value):
        return value.isoformat() if value else None

    return {
        "paragraph_count": len(doc.paragraphs),
        "image_count": len(doc.inline_shapes),
        "docx_author": props.author or None,
        "docx_last_modified_by": props.last_modified_by or None,
        "docx_created": iso(props.created),
        "docx_modified": iso(props.modified),
        "docx_revision": props.revision,
    }


def _image_metadata(file_path: Path) -> dict:
    from PIL import Image

    with Image.open(file_path) as img:
        img.load()  # forces a full read -> catches truncated / corrupt images
        exif = img.getexif()
        return {
            "image_width": img.width,
            "image_height": img.height,
            "image_format": img.format,
            "exif_software": exif.get(305),   # e.g. "Adobe Photoshop"
            "exif_datetime": exif.get(306),
        }


def extract_file_metadata(
    file_path: Path, original_filename: str, detected_type: str
) -> dict:
    """Returns a dict of metadata (saved to the audit log by the pipeline)."""
    meta = {
        "original_filename": original_filename,
        "detected_type": detected_type,
        "size_bytes": file_path.stat().st_size,
        "sha256": _sha256(file_path),
    }

    try:
        if detected_type == "pdf":
            meta.update(_pdf_metadata(file_path))
        elif detected_type == "docx":
            meta.update(_docx_metadata(file_path))
        elif detected_type in {"jpeg", "png", "gif", "bmp", "tiff", "webp"}:
            meta.update(_image_metadata(file_path))
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(
            ErrorCode.INVALID_FILE_TYPE,
            details=f"File is corrupted or cannot be opened ({type(exc).__name__})",
        ) from exc

    return meta