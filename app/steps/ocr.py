"""
ocr.py - diagram boxes "Image Preprocessing & Data Extraction" -> "OCR" -> "OCR Success?"

Used when text cannot be read directly (scanned PDF, photo/scan of a letter,
Word file that only contains a picture) and to read images inside a PDF.

Steps for every image:
  1. Preprocessing  : white background -> grayscale -> enlarge -> remove noise ->
                      straighten (deskew) -> even out lighting
                      (we deliberately do NOT force pure black & white: Tesseract's own
                      binarizer reads digits more accurately - tested)
  2. OCR            : Tesseract reads the cleaned image
  3. OCR Success?   : enough text AND average confidence >= OCR_MIN_CONFIDENCE (.env)

Raises OcrError when OCR cannot produce usable text (extraction.py turns that
into the OCR_FAILED error from the diagram).
"""
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

from app.config import settings

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

logger = logging.getLogger("job_letter.ocr")

MAX_OCR_PAGES = 5        # a job letter is 1-2 pages; protects the server from huge files
MAX_EMBEDDED_IMAGES = 10
RENDER_DPI = 250         # resolution used to turn PDF pages into images
MIN_IMAGE_SIDE = 100     # ignore tiny embedded pictures (icons, bullets)
MIN_OCR_CHARS = 20       # fewer readable characters than this = OCR failed


class OcrError(Exception):
    """OCR could not produce usable text."""


@dataclass
class OcrResult:
    text: str
    confidence: float      # average word confidence, 0-100
    images_processed: int


# ----------------------------------------------------------------------------
# 1. Image preprocessing
# ----------------------------------------------------------------------------
def _configure_tesseract() -> None:
    cmd = (settings.tesseract_cmd or "").strip()
    if cmd and Path(cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = cmd
    # otherwise pytesseract looks for "tesseract" on the system PATH


def _to_gray_array(img: Image.Image) -> np.ndarray:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        white = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(white, img)   # transparent -> white (not black)
    rgb = img.convert("RGB")
    return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2GRAY)


def _flatten_lighting(gray: np.ndarray) -> np.ndarray:
    """Divide by the blurred background -> removes shadows / uneven light (phone photos)."""
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=25)
    return cv2.divide(gray, background, scale=255)


def _estimate_skew(gray: np.ndarray, max_angle: float = 10.0, step: float = 0.5) -> float:
    """
    Projection-profile method: rotate a small copy through candidate angles and keep
    the angle where the text lines are sharpest (biggest row-to-row contrast).
    More reliable than measuring a bounding box on noisy scans.
    """
    h, w = gray.shape
    scale = 900 / max(h, w)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    small = _flatten_lighting(small)
    _, bw = cv2.threshold(cv2.bitwise_not(small), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))   # drop speckle
    sh, sw = bw.shape
    centre = (sw / 2, sh / 2)

    def sharpness(angle: float) -> float:
        matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
        rotated = cv2.warpAffine(bw, matrix, (sw, sh), flags=cv2.INTER_NEAREST)
        return float(np.var(rotated.sum(axis=1, dtype=np.float64)))

    coarse = np.arange(-max_angle, max_angle + step, step)
    best = float(coarse[int(np.argmax([sharpness(a) for a in coarse]))])
    fine = np.arange(best - step, best + step + 0.01, 0.1)              # refine
    return float(fine[int(np.argmax([sharpness(a) for a in fine]))])


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Straighten a tilted scan (corrects up to +/-10 degrees)."""
    angle = _estimate_skew(gray)
    if abs(angle) < 0.3:
        return gray
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def preprocess_image(img: Image.Image) -> np.ndarray:
    """Turn any picture into a clean grayscale image for Tesseract."""
    gray = _to_gray_array(img)

    # enlarge small images (Tesseract likes ~300 DPI text)
    longest = max(gray.shape)
    if longest < 1600:
        scale = min(3.0, 1600 / longest)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.medianBlur(gray, 3)     # remove speckle noise
    gray = _deskew(gray)               # straighten
    return _flatten_lighting(gray)     # even out shadows / uneven light


# ----------------------------------------------------------------------------
# 2. OCR on one image
# ----------------------------------------------------------------------------
def ocr_image(img: Image.Image) -> tuple[str, float]:
    """Returns (text, average_confidence 0-100)."""
    _configure_tesseract()
    processed = preprocess_image(img)
    try:
        data = pytesseract.image_to_data(
            processed,
            lang=settings.ocr_lang,
            config="--oem 3 --psm 3",
            output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError(
            "OCR engine (Tesseract) not found - install it or fix TESSERACT_CMD in .env"
        ) from exc
    except pytesseract.TesseractError as exc:
        raise OcrError(f"OCR engine error: {str(exc)[:150]}") from exc

    lines: list[str] = []
    confidences: list[float] = []
    current: list[str] = []
    last_key = None
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        conf = float(data["conf"][i])
        if conf >= 0:
            confidences.append(conf)
        block_par = (data["block_num"][i], data["par_num"][i])
        line_key = block_par + (data["line_num"][i],)
        if line_key != last_key:
            if current:
                lines.append(" ".join(current))
            if last_key is not None and block_par != last_key[:2]:
                lines.append("")            # blank line between paragraphs
            current, last_key = [], line_key
        current.append(word)
    if current:
        lines.append(" ".join(current))

    average = sum(confidences) / len(confidences) if confidences else 0.0
    return "\n".join(lines), average


def _combine(results: list[tuple[str, float]]) -> OcrResult:
    texts = [t for t, _ in results if t.strip()]
    confs = [c for t, c in results if t.strip()]
    return OcrResult(
        text="\n\n".join(texts),
        confidence=sum(confs) / len(confs) if confs else 0.0,
        images_processed=len(results),
    )


# ----------------------------------------------------------------------------
# Where the images come from
# ----------------------------------------------------------------------------
def _render_pdf_pages(file_path: Path) -> list[Image.Image]:
    images = []
    with fitz.open(file_path) as doc:
        for index in range(min(doc.page_count, MAX_OCR_PAGES)):
            pix = doc[index].get_pixmap(dpi=RENDER_DPI)
            images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    return images


def _pdf_embedded_images(file_path: Path) -> list[Image.Image]:
    images, seen = [], set()
    with fitz.open(file_path) as doc:
        for page in doc:
            for item in page.get_images(full=True):
                xref = item[0]
                if xref in seen or len(images) >= MAX_EMBEDDED_IMAGES:
                    continue
                seen.add(xref)
                try:
                    info = doc.extract_image(xref)
                    img = Image.open(io.BytesIO(info["image"]))
                    if min(img.size) >= MIN_IMAGE_SIDE:
                        images.append(img)
                except Exception:  # unusual image formats are simply skipped
                    continue
    return images


def _docx_embedded_images(file_path: Path) -> list[Image.Image]:
    images = []
    with zipfile.ZipFile(file_path) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/") and name.lower().endswith((".png", ".jpg", ".jpeg")):
                if len(images) >= MAX_EMBEDDED_IMAGES:
                    break
                try:
                    img = Image.open(io.BytesIO(zf.read(name)))
                    if min(img.size) >= MIN_IMAGE_SIDE:
                        images.append(img)
                except Exception:
                    continue
    return images


# ----------------------------------------------------------------------------
# 3. Public functions used by extraction.py
# ----------------------------------------------------------------------------
def ocr_file(file_path: Path, detected_type: str) -> OcrResult:
    """
    FULL OCR - the document has no readable text layer.
    Applies the "OCR Success?" decision: raises OcrError when it did not work.
    """
    if detected_type == "pdf":
        images = _render_pdf_pages(file_path)
    elif detected_type == "docx":
        images = _docx_embedded_images(file_path)
    else:  # jpeg / png
        images = [Image.open(file_path)]

    if not images:
        raise OcrError("No images found to read with OCR")

    result = _combine([ocr_image(img) for img in images])

    readable_chars = len(re.sub(r"\s+", "", result.text))
    if readable_chars < MIN_OCR_CHARS:
        raise OcrError("No readable text found in the document image")
    if result.confidence < settings.ocr_min_confidence:
        raise OcrError(
            f"OCR confidence too low ({result.confidence:.0f}% - minimum {settings.ocr_min_confidence}%)"
        )
    return result


def ocr_pdf_embedded_images(file_path: Path) -> OcrResult:
    """
    SUPPLEMENTARY OCR - the PDF already has real text, but also contains pictures
    (logo, stamp, signature).  Reads any text inside those pictures.
    An empty result is fine (a logo often has no text).
    """
    images = _pdf_embedded_images(file_path)
    if not images:
        return OcrResult(text="", confidence=0.0, images_processed=0)
    return _combine([ocr_image(img) for img in images])