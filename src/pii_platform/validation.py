"""Validate PDF content (not just the filename) before anything is stored."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import pymupdf

from .config import Settings
from .models import PipelineError, Status

pymupdf.TOOLS.mupdf_display_errors(False)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ \-]")


@dataclass
class ValidationResult:
    page_count: int
    size_bytes: int
    sha256: str


def sanitize_display_name(name: str) -> str:
    """Strip any path and unsafe characters. Used for display only, never as a disk path."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = _SAFE_NAME.sub("_", base).strip(" .")[:80]
    return base or "document.pdf"


def validate_pdf_bytes(data: bytes, settings: Settings) -> ValidationResult:
    size = len(data)
    if size == 0:
        raise PipelineError(Status.VALIDATION_FAILED, "The file is empty (0 bytes).")
    if size > settings.max_file_bytes:
        raise PipelineError(
            Status.VALIDATION_FAILED,
            f"The file is {size / 1_048_576:.1f} MB; the limit is {settings.max_file_mb} MB.",
        )
    if b"%PDF-" not in data[:1024]:
        raise PipelineError(Status.VALIDATION_FAILED, "This is not a PDF file (missing PDF header).")

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise PipelineError(Status.VALIDATION_FAILED, "The PDF is corrupt or unreadable.") from None

    try:
        if doc.is_encrypted or doc.needs_pass:
            raise PipelineError(
                Status.VALIDATION_FAILED,
                "Password-protected or encrypted PDFs are not supported. "
                "Remove the protection (with the owner's permission) and upload again.",
            )
        pages = doc.page_count
        if pages == 0:
            raise PipelineError(Status.VALIDATION_FAILED, "The PDF has no pages.")
        if pages > settings.max_pages:
            raise PipelineError(
                Status.VALIDATION_FAILED,
                f"The PDF has {pages} pages; the limit is {settings.max_pages}.",
            )
        has_content = False
        try:
            for page in doc:
                if page.get_text("text").strip() or page.get_images():
                    has_content = True
        except Exception:
            raise PipelineError(Status.VALIDATION_FAILED, "The PDF is corrupt: its pages cannot be read.") from None
        if not has_content:
            raise PipelineError(
                Status.VALIDATION_FAILED,
                "The PDF contains no extractable text or images, so there is nothing to process.",
            )
    finally:
        doc.close()

    return ValidationResult(page_count=pages, size_bytes=size, sha256=hashlib.sha256(data).hexdigest())
