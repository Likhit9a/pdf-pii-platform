"""Verify a redacted PDF. Any blocking check that fails means the output must be discarded."""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from .config import Settings
from .detection import normalize
from .extraction import _ocr_words, ocr_available
from .models import CheckResult, Detection

_DIGIT_CATEGORIES = {"PHONE", "AADHAAR", "SSN", "CREDIT_CARD"}
MIN_VERIFY_LEN = 4


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def verify_redacted_pdf(
    out_path: Path,
    detections: list[Detection],
    expected_pages: int,
    ocr_pages: list[int],
    settings: Settings,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        doc = pymupdf.open(str(out_path))
    except Exception:
        return [CheckResult("output_opens", False, "The redacted file could not be reopened.")]

    try:
        checks.append(CheckResult("output_opens", True, f"Reopened; {doc.page_count} page(s)."))
        checks.append(CheckResult(
            "page_count", doc.page_count == expected_pages,
            f"Output has {doc.page_count} page(s); expected {expected_pages}.",
        ))

        # 1. Redacted strings must not be recoverable by ordinary text extraction.
        targets: dict[str, str] = {}
        digit_targets: dict[str, str] = {}
        skipped = 0
        for d in detections:
            n = normalize(d.value)
            if len(n) < MIN_VERIFY_LEN:
                skipped += 1
                continue
            targets[n] = d.category
            if d.category in _DIGIT_CATEGORIES and len(_digits(d.value)) >= 6:
                digit_targets[_digits(d.value)] = d.category

        leaks: list[tuple[int, str]] = []
        page_texts: list[str] = []
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            page_texts.append(text)
            norm_text = normalize(text)
            digit_text = _digits(text)
            for n, cat in targets.items():
                if n in norm_text:
                    leaks.append((i, cat))
            for dg, cat in digit_targets.items():
                if dg in digit_text:
                    leaks.append((i, cat))
        if leaks:
            detail = "Redacted value(s) still extractable: " + ", ".join(
                f"{c} on page {p}" for p, c in sorted(set(leaks))
            )
        else:
            detail = f"None of {len(targets)} redacted value(s) found in extracted text."
        if skipped:
            detail += f" ({skipped} very short value(s) could not be checked.)"
        checks.append(CheckResult("text_not_recoverable", not leaks, detail))

        # 2. Metadata must be empty.
        meta = {k: v for k, v in (doc.metadata or {}).items() if k not in {"format", "encryption"} and v}
        xml_present = bool(doc.xref_xml_metadata())
        checks.append(CheckResult(
            "metadata_clean", not meta and not xml_present,
            "Document info and XMP metadata are empty." if not meta and not xml_present
            else f"Metadata still present (fields: {', '.join(sorted(meta)) or 'xmp'}).",
        ))

        # 3. No annotations, embedded files or form widgets.
        annots = sum(len(list(p.annots() or [])) for p in doc)
        checks.append(CheckResult("annotations_clean", annots == 0, f"{annots} annotation(s) remain."))
        emb = doc.embfile_count()
        checks.append(CheckResult("no_embedded_files", emb == 0, f"{emb} embedded file(s) remain."))
        widgets = sum(len(list(p.widgets() or [])) for p in doc)
        checks.append(CheckResult("no_form_fields", widgets == 0, f"{widgets} form field(s) remain."))

        # 4. OCR'd (scanned) pages: re-OCR the output and confirm nothing readable remains.
        if ocr_pages:
            if not ocr_available(settings):
                checks.append(CheckResult("ocr_recheck", False, "OCR unavailable for re-check of scanned pages."))
            else:
                ocr_leaks = 0
                for number in ocr_pages:
                    words = _ocr_words(doc[number - 1], settings)
                    joined = normalize(" ".join(w.text for w in words))
                    ocr_leaks += sum(1 for n in targets if n in joined)
                checks.append(CheckResult(
                    "ocr_recheck", ocr_leaks == 0,
                    "Re-running OCR on scanned pages found no redacted values."
                    if ocr_leaks == 0 else f"OCR still reads {ocr_leaks} redacted value(s).",
                ))

        # 5. Informational: images we cannot analyze.
        image_pages = [i for i, p in enumerate(doc, start=1) if p.get_images() and i not in ocr_pages]
        if image_pages:
            checks.append(CheckResult(
                "images_present", True,
                "Embedded images remain on page(s) "
                + ", ".join(map(str, image_pages))
                + ". Text inside images was not analyzed.",
                blocking=False,
            ))
    finally:
        doc.close()
    return checks


def all_blocking_passed(checks: list[CheckResult]) -> bool:
    return all(c.passed for c in checks if c.blocking)
