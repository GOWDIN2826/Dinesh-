"""
file_validation.py - file format validation for documents, images, and text files
+ decision "Valid File Type?"

NO -> raise INVALID_FILE_TYPE   |   YES -> continue to File Metadata Extraction

Known binary formats are checked using their real content. Readable text files
are also accepted so they can continue through the normal Job Letter checks.
"""
import zipfile
from pathlib import Path
from typing import Optional

from schemas import ErrorCode, PipelineError

EXTENSION_TO_TYPE = {
    ".pdf": "pdf",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".gif": "gif",
    ".bmp": "bmp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".webp": "webp",
    ".docx": "docx",
}

TYPE_TO_MIME = {
    "pdf": "application/pdf",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "webp": "image/webp",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text": "text/plain",
    "unknown": "application/octet-stream",
}

IMAGE_SIGNATURES = {
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"BM": "bmp",
    b"II*\x00": "tiff",
    b"MM\x00*": "tiff",
}

TEXT_EXTENSIONS = {
    ".txt", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".md", ".rtf", ".log"
}

ACCEPTED_TEXT = "Accepted formats: documents, images, and readable text files"


def detect_file_type(file_path: Path) -> Optional[str]:
    """Look at the first bytes of the file. Returns pdf/jpeg/png/docx or None."""
    with file_path.open("rb") as f:
        head = f.read(2048)

    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    for signature, file_type in IMAGE_SIGNATURES.items():
        if head.startswith(signature):
            return file_type
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"PK\x03\x04") and zipfile.is_zipfile(file_path):
        try:
            with zipfile.ZipFile(file_path) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile:
            return None
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return "docx"
    return "text"


def validate_file_format(file_path: Path, original_filename: str) -> dict:
    """Returns {'detected_type', 'mime_type', 'extension'} or raises INVALID_FILE_TYPE."""
    extension = Path(original_filename).suffix.lower()

    # Known binary formats are identified from their content, not their name.
    detected = detect_file_type(file_path)
    if detected is None:
        detected = "unknown"

    # A known extension must agree with its detected content. Unknown extensions
    # are allowed when the content is a supported image or readable text.
    expected = EXTENSION_TO_TYPE.get(extension)
    if expected and expected != detected:
        raise PipelineError(
            ErrorCode.INVALID_FILE_TYPE,
            details=(
                f"File extension {extension} does not match the file content "
                f"({detected.upper()})"
            ),
        )

    return {
        "detected_type": detected,
        "mime_type": TYPE_TO_MIME[detected],
        "extension": extension,
    }