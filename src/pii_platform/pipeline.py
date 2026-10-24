"""Orchestrates upload -> extraction -> detection -> review -> redaction -> verification -> indexing.

A document is only REDACTED_VERIFIED if every stage succeeded. Any failure discards the output.
"""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

import pymupdf

from .config import Settings
from .detection import _raw_spans, detect_pii, find_value_occurrences, ner_available, normalize
from .extraction import extract_document
from .logging_setup import attach_file_handler, log_event
from .metadata.base import MetadataStore
from .metadata.sqlite_store import SQLiteStore
from .models import (
    AnalysisResult, CheckResult, Detection, PipelineError, RedactionOutcome, Status,
)
from .redaction import redact_pdf
from .storage.base import StorageBackend
from .storage.local import LocalStorage
from .validation import sanitize_display_name, validate_pdf_bytes
from .verification import all_blocking_passed, verify_redacted_pdf

_FILENAME_PII = {"EMAIL", "PHONE", "AADHAAR", "SSN", "PAN", "CREDIT_CARD"}


class Pipeline:
    def __init__(self, settings: Settings, storage: StorageBackend, store: MetadataStore):
        self.settings = settings
        self.storage = storage
        self.store = store
        self.cleanup_temp()

    # ---- housekeeping --------------------------------------------------
    def cleanup_temp(self, max_age_seconds: int = 3600) -> int:
        removed = 0
        tmp = self.settings.tmp_dir
        if tmp.is_dir():
            for f in tmp.iterdir():
                try:
                    if f.is_file() and time.time() - f.stat().st_mtime > max_age_seconds:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
        return removed

    def _set(self, doc_id: str, status: Status, detail: str = "", stage_updates: dict[str, str] | None = None, **fields: Any) -> None:
        stages = (self.store.get_document(doc_id) or {}).get("stages", {})
        if stage_updates:
            stages = {**stages, **stage_updates}
        self.store.update_document(doc_id, status=status.value, status_detail=detail, stages=stages, **fields)

    # ---- 1. upload + validation ---------------------------------------
    def ingest(self, filename: str, data: bytes) -> dict[str, Any]:
        doc_id = uuid.uuid4().hex
        name = sanitize_display_name(filename)
        # A filename can itself contain PII (e.g. "jane.doe@mail.com_ssn.pdf"); don't show it.
        raw_name = re.split(r"[\\/]", filename or "")[-1]
        if _raw_spans(raw_name, _FILENAME_PII, {}) or _raw_spans(name, _FILENAME_PII, {}):
            name = f"document-{doc_id[:8]}.pdf"
        try:
            result = validate_pdf_bytes(data, self.settings)
        except PipelineError as err:
            self.store.create_document({
                "id": doc_id, "display_name": name, "status": err.status.value,
                "status_detail": err.message, "size_bytes": len(data),
                "stages": {"validation": "failed"},
            })
            log_event(doc_id, "validation", "failed", size=len(data))
            return self.store.get_document(doc_id)  # type: ignore[return-value]

        self.storage.save_original(doc_id, data)
        self.store.create_document({
            "id": doc_id, "display_name": name, "status": Status.UPLOADED.value,
            "sha256": result.sha256, "size_bytes": result.size_bytes, "page_count": result.page_count,
            "stages": {"validation": "ok"},
        })
        log_event(doc_id, "validation", "ok", pages=result.page_count, size=result.size_bytes)
        return self.store.get_document(doc_id)  # type: ignore[return-value]

    # ---- 2+3. extraction and detection ---------------------------------
    def analyze(self, doc_id: str, categories: set[str] | None = None) -> AnalysisResult:
        record = self.store.get_document(doc_id)
        if record is None:
            raise PipelineError(Status.EXTRACTION_FAILED, "Document not found.")
        path = self.storage.original_path(doc_id)
        if not path.is_file():
            raise PipelineError(Status.EXTRACTION_FAILED, "The original file is no longer stored, so it cannot be re-analyzed.")

        t0 = time.time()
        try:
            doc = pymupdf.open(str(path))
        except Exception:
            self._set(doc_id, Status.EXTRACTION_FAILED, "The stored PDF could not be opened.", {"extraction": "failed"})
            raise PipelineError(Status.EXTRACTION_FAILED, "The stored PDF could not be opened.") from None
        try:
            try:
                extraction = extract_document(doc, self.settings)
            except PipelineError as err:
                self._set(doc_id, err.status, err.message, {"extraction": "failed"})
                log_event(doc_id, "extraction", "failed")
                raise
            except Exception as exc:
                msg = "Text extraction failed unexpectedly."
                self._set(doc_id, Status.EXTRACTION_FAILED, msg, {"extraction": "failed"})
                log_event(doc_id, "extraction", "failed", error=type(exc).__name__)
                raise PipelineError(Status.EXTRACTION_FAILED, msg) from None
        finally:
            doc.close()
        log_event(doc_id, "extraction", "ok", pages=len(extraction.pages), ocr_pages=len(extraction.ocr_pages),
                  ms=int((time.time() - t0) * 1000))

        try:
            detections = detect_pii(extraction.pages, self.settings, categories)
        except Exception as exc:
            msg = "PII detection failed unexpectedly."
            self._set(doc_id, Status.DETECTION_FAILED, msg, {"extraction": "ok", "detection": "failed"})
            log_event(doc_id, "detection", "failed", error=type(exc).__name__)
            raise PipelineError(Status.DETECTION_FAILED, msg) from None

        warnings = list(extraction.warnings)
        wanted = categories if categories is not None else set(self.settings.enabled_categories)
        if "PERSON" in wanted and not ner_available():
            warnings.append(
                "The spaCy language model is not installed, so name detection is limited to labelled names "
                "such as 'Name: Jane Doe'. Install en_core_web_sm for better coverage."
            )
        warnings.extend(self.settings.warnings)

        status = Status.AWAITING_REVIEW if detections else Status.NO_PII_FOUND
        detail = (
            f"{len(detections)} potential PII item(s) detected; review before redacting."
            if detections else
            "No PII was detected. Automatic detection is imperfect, so check the document yourself."
        )
        self._set(
            doc_id, status, detail, {"extraction": "ok", "detection": "ok", "redaction": "pending", "verification": "pending"},
            warnings=warnings, ocr_used=int(bool(extraction.ocr_pages)),
        )
        log_event(doc_id, "detection", "ok", detections=len(detections))
        return AnalysisResult(doc_id=doc_id, extraction=extraction, detections=detections, warnings=warnings)

    # ---- 4+5+6+7. redaction, verification, indexing ----------------------
    def redact(self, doc_id: str, analysis: AnalysisResult, selected_ids: set[str]) -> RedactionOutcome:
        record = self.store.get_document(doc_id)
        if record is None:
            raise PipelineError(Status.REDACTION_FAILED, "Document not found.")
        src = self.storage.original_path(doc_id)
        if not src.is_file():
            raise PipelineError(Status.REDACTION_FAILED, "The original file is no longer stored.")

        selected = [d for d in analysis.detections if d.id in selected_ids]
        # "Redact all occurrences": any selected value is also removed wherever else it appears.
        values = {normalize(d.value): d.category for d in selected}
        known = {(d.page, d.start, d.end) for d in selected}
        sweep = [d for d in find_value_occurrences(analysis.extraction.pages, values)
                 if (d.page, d.start, d.end) not in known]
        targets = selected + sweep

        by_category: dict[str, int] = {}
        for d in targets:
            by_category[d.category] = by_category.get(d.category, 0) + 1
        pages_affected = sorted({d.page for d in targets})

        temp = self.storage.new_temp_path()
        t0 = time.time()
        try:
            try:
                redact_pdf(src, temp, targets)
            except Exception as exc:
                log_event(doc_id, "redaction", "failed", error=type(exc).__name__)
                return self._fail(doc_id, Status.REDACTION_FAILED, "Redaction failed; no output was produced.",
                                  {"redaction": "failed", "verification": "skipped"})
            log_event(doc_id, "redaction", "ok", boxes=sum(len(d.rects) for d in targets), ms=int((time.time() - t0) * 1000))

            try:
                checks = verify_redacted_pdf(
                    temp, targets, expected_pages=record["page_count"] or len(analysis.extraction.pages),
                    ocr_pages=analysis.extraction.ocr_pages, settings=self.settings,
                )
            except Exception as exc:
                log_event(doc_id, "verification", "failed", error=type(exc).__name__)
                return self._fail(doc_id, Status.VERIFICATION_FAILED, "Verification could not be completed; output discarded.",
                                  {"redaction": "ok", "verification": "failed"})

            if not all_blocking_passed(checks):
                failing = "; ".join(c.detail for c in checks if c.blocking and not c.passed)
                log_event(doc_id, "verification", "failed", failed=sum(1 for c in checks if c.blocking and not c.passed))
                outcome = self._fail(doc_id, Status.VERIFICATION_FAILED,
                                     f"Verification failed, so the output was discarded. {failing}",
                                     {"redaction": "ok", "verification": "failed"})
                outcome.checks = checks
                return outcome

            # Verified: promote, index the redacted text, optionally drop the original.
            out_path = self.storage.promote_output(temp, doc_id)
            pages_text = self._extract_output_text(out_path)
            self.store.index_text(doc_id, pages_text)
            if self.settings.delete_original_after_redaction:
                self.storage.delete_original(doc_id)

            summary = {
                "redaction_count": len(targets),
                "by_category": by_category,
                "pages_affected": pages_affected,
                "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail, "blocking": c.blocking} for c in checks],
                "original_deleted": bool(self.settings.delete_original_after_redaction),
            }
            warnings = list(analysis.warnings)
            self._set(
                doc_id, Status.REDACTED_VERIFIED, f"{len(targets)} item(s) redacted and verified.",
                {"redaction": "ok", "verification": "ok"}, summary=summary, warnings=warnings,
            )
            log_event(doc_id, "verification", "ok", redactions=len(targets))
            return RedactionOutcome(
                doc_id=doc_id, status=Status.REDACTED_VERIFIED, message=f"{len(targets)} item(s) redacted and verified.",
                checks=checks, redaction_count=len(targets), by_category=by_category,
                pages_affected=pages_affected, warnings=warnings,
            )
        finally:
            Path(temp).unlink(missing_ok=True)  # no-op after a successful promote

    def _fail(self, doc_id: str, status: Status, message: str, stages: dict[str, str]) -> RedactionOutcome:
        self._set(doc_id, status, message, stages)
        return RedactionOutcome(doc_id=doc_id, status=status, message=message)

    @staticmethod
    def _extract_output_text(path: Path) -> list[str]:
        doc = pymupdf.open(str(path))
        try:
            return [p.get_text("text") for p in doc]
        finally:
            doc.close()

    # ---- other operations ----------------------------------------------
    def read_output(self, doc_id: str) -> bytes:
        record = self.store.get_document(doc_id)
        if not record or record["status"] != Status.REDACTED_VERIFIED.value:
            raise PipelineError(Status.REDACTION_FAILED, "No verified redacted output exists for this document.")
        return self.storage.read_output(doc_id)

    def delete(self, doc_id: str) -> None:
        self.storage.delete_all(doc_id)
        self.store.delete_document(doc_id)
        log_event(doc_id, "delete", "ok")


def build_pipeline(settings: Settings | None = None) -> Pipeline:
    """Local-only by default. Unknown/unsupported backends fall back to local with a warning."""
    settings = settings or Settings.from_env()
    if settings.backend != "local":
        settings.warnings.append(
            f"Backend '{settings.backend}' is not available in this build; using local processing instead."
        )
        settings.backend = "local"
    settings.ensure_dirs()
    attach_file_handler(settings.logs_dir)
    return Pipeline(settings, LocalStorage(settings), SQLiteStore(settings.db_path))
