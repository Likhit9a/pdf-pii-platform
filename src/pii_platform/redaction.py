"""Genuine redaction: apply_redactions() removes the underlying content, then the file is
scrubbed and rewritten as a brand-new file (never an incremental save of the original)."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .models import Detection


def redact_pdf(src: Path, dst: Path, detections: list[Detection]) -> dict:
    """Write a redacted, sanitized copy of `src` to `dst`. `src` is never modified."""
    by_page: dict[int, list[Detection]] = {}
    for d in detections:
        by_page.setdefault(d.page, []).append(d)

    doc = pymupdf.open(str(src))
    try:
        applied = 0
        # Annotations (sticky notes, free text, stamps...) can carry PII that no text pass sees.
        for page in doc:
            annot = page.first_annot
            while annot:
                annot = page.delete_annot(annot)

        for page_number, dets in by_page.items():
            page = doc[page_number - 1]
            for d in dets:
                for r in d.rects:
                    rect = pymupdf.Rect(r)
                    if rect.is_empty or rect.is_infinite:
                        continue
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    applied += 1
            # Remove text AND blank image pixels under each box (not just draw over them).
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )

        # Strip everything that can carry PII outside the visible page content.
        doc.scrub(
            metadata=True, xml_metadata=True, attached_files=True, embedded_files=True,
            hidden_text=True, javascript=True, remove_links=True, reset_fields=True,
            reset_responses=True, thumbnails=True, clean_pages=True, redactions=True,
        )
        doc.set_metadata({})
        # Full rewrite: garbage=4 drops unreferenced objects so no old content stream survives.
        doc.save(str(dst), garbage=4, deflate=True, clean=True, incremental=False, encryption=pymupdf.PDF_ENCRYPT_NONE)
    finally:
        doc.close()
    return {"boxes_applied": applied}
