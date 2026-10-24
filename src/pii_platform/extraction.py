"""Word-level text extraction (with positions) and optional local OCR via Tesseract."""
from __future__ import annotations

import io

import pymupdf

from .config import Settings
from .models import ExtractionResult, PageContent, PipelineError, Status, Word

MIN_TEXT_CHARS = 5  # below this a page is treated as "no text layer"


def build_page_text(words: list[Word]) -> tuple[str, list[tuple[int, int]]]:
    """Join words with spaces within a line and newlines between lines; return per-word offsets."""
    parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    pos = 0
    prev_key = None
    for w in words:
        if prev_key is not None:
            sep = " " if w.line_key == prev_key else "\n"
            parts.append(sep)
            pos += 1
        offsets.append((pos, pos + len(w.text)))
        parts.append(w.text)
        pos += len(w.text)
        prev_key = w.line_key
    return "".join(parts), offsets


def ocr_available(settings: Settings) -> bool:
    if not settings.ocr_enabled:
        return False
    try:
        import pytesseract

        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _ocr_words(page: pymupdf.Page, settings: Settings) -> list[Word]:
    import pytesseract
    from PIL import Image

    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    pix = page.get_pixmap(dpi=settings.ocr_dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(img, lang="eng", output_type=pytesseract.Output.DICT)
    scale = 72.0 / settings.ocr_dpi
    words: list[Word] = []
    for i, raw in enumerate(data["text"]):
        text = (raw or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not text or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        rect = pymupdf.Rect(x * scale, y * scale, (x + w) * scale, (y + h) * scale) * page.derotation_matrix
        rect.normalize()
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        words.append(Word(rect.x0, rect.y0, rect.x1, rect.y1, text, key))
    return words


def extract_document(doc: pymupdf.Document, settings: Settings) -> ExtractionResult:
    pages: list[PageContent] = []
    warnings: list[str] = []
    ocr_pages: list[int] = []
    unanalyzed: list[int] = []
    ocr_ok = ocr_available(settings)
    pages_with_images_and_text: list[int] = []

    for index, page in enumerate(doc):
        number = index + 1
        raw = page.get_text("words")
        words = [Word(w[0], w[1], w[2], w[3], w[4], (w[5], w[6])) for w in raw if w[4].strip()]
        text_len = sum(len(w.text) for w in words)
        has_images = bool(page.get_images())
        source = "text"

        if text_len < MIN_TEXT_CHARS and has_images:
            if ocr_ok:
                words = _ocr_words(page, settings)
                source = "ocr"
                ocr_pages.append(number)
            else:
                unanalyzed.append(number)
                words = []
        elif has_images:
            pages_with_images_and_text.append(number)

        content = PageContent(number=number, words=words, source=source)
        content.text, content.offsets = build_page_text(words)
        pages.append(content)

    if ocr_pages:
        warnings.append(
            f"OCR was used on page(s) {_fmt(ocr_pages)}. OCR can misread characters, so detection on "
            "those pages is less reliable; review carefully."
        )
    if unanalyzed:
        warnings.append(
            f"Page(s) {_fmt(unanalyzed)} look scanned but OCR is unavailable (Tesseract not found or disabled)."
        )
    if pages_with_images_and_text:
        warnings.append(
            f"Page(s) {_fmt(pages_with_images_and_text)} contain images in addition to text. "
            "Text inside those images is not analyzed or redacted."
        )

    if unanalyzed and not settings.allow_unanalyzed_pages:
        raise PipelineError(
            Status.EXTRACTION_FAILED,
            f"Page(s) {_fmt(unanalyzed)} appear to be scans and OCR is not available, so they cannot be "
            "checked for PII. Install Tesseract OCR (see README) or enable OCR, then try again.",
        )
    if not any(p.words for p in pages):
        raise PipelineError(Status.EXTRACTION_FAILED, "No text could be extracted from this document.")

    return ExtractionResult(pages=pages, warnings=warnings, ocr_pages=ocr_pages, unanalyzed_pages=unanalyzed)


def _fmt(nums: list[int]) -> str:
    return ", ".join(str(n) for n in nums)
