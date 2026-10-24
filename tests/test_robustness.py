import os
import socket
import sys
import time

import pymupdf
import pytest

import pdf_factory as F
from pii_platform.config import Settings
from pii_platform.extraction import ocr_available
from pii_platform.models import PipelineError, Status
from pii_platform.pipeline import build_pipeline

needs_ocr = pytest.mark.skipif(not ocr_available(Settings.from_env()), reason="Tesseract not installed")


# ---- temp files ---------------------------------------------------------
def test_temp_dir_empty_after_success_and_failure(pipe, settings, monkeypatch):
    rec = pipe.ingest("a.pdf", F.simple_pii_pdf())
    analysis = pipe.analyze(rec["id"])
    pipe.redact(rec["id"], analysis, {d.id for d in analysis.detections})
    assert list(settings.tmp_dir.iterdir()) == []

    from pii_platform import pipeline as pl
    monkeypatch.setattr(pl, "redact_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    rec2 = pipe.ingest("b.pdf", F.simple_pii_pdf())
    a2 = pipe.analyze(rec2["id"])
    pipe.redact(rec2["id"], a2, {d.id for d in a2.detections})
    assert list(settings.tmp_dir.iterdir()) == []


def test_stale_temp_files_are_cleaned(pipe, settings):
    stale = pipe.storage.new_temp_path()
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = pipe.storage.new_temp_path()
    assert pipe.cleanup_temp() == 1
    assert not stale.exists() and fresh.exists()


# ---- error handling -------------------------------------------------------
def test_unexpected_extraction_error_gives_safe_status(pipe, monkeypatch):
    from pii_platform import pipeline as pl

    monkeypatch.setattr(pl, "extract_document", lambda *a, **k: (_ for _ in ()).throw(ValueError("secret " + F.EMAIL)))
    rec = pipe.ingest("e.pdf", F.simple_pii_pdf())
    with pytest.raises(PipelineError) as e:
        pipe.analyze(rec["id"])
    assert e.value.status == Status.EXTRACTION_FAILED
    assert F.EMAIL not in e.value.message  # exception text is never surfaced
    stored = pipe.store.get_document(rec["id"])
    assert stored["status"] == "EXTRACTION_FAILED" and F.EMAIL not in str(stored)


def test_unexpected_detection_error_gives_detection_failed(pipe, monkeypatch):
    from pii_platform import pipeline as pl

    monkeypatch.setattr(pl, "detect_pii", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    rec = pipe.ingest("d.pdf", F.simple_pii_pdf())
    with pytest.raises(PipelineError) as e:
        pipe.analyze(rec["id"])
    assert e.value.status == Status.DETECTION_FAILED
    assert pipe.store.get_document(rec["id"])["stages"]["extraction"] == "ok"


def test_path_traversal_ids_rejected(pipe):
    for bad in ("../../etc/passwd", "..\\x", "abc", ""):
        with pytest.raises(ValueError):
            pipe.storage.original_path(bad)


def test_missing_original_reports_clearly(pipe):
    rec = pipe.ingest("m.pdf", F.simple_pii_pdf())
    pipe.storage.delete_original(rec["id"])
    with pytest.raises(PipelineError):
        pipe.analyze(rec["id"])


# ---- AWS unavailable / offline -------------------------------------------
def test_aws_backend_request_falls_back_to_local(tmp_path):
    s = Settings.from_env(data_dir=tmp_path / "d", backend="aws")
    pipe = build_pipeline(s)
    assert s.backend == "local"
    assert any("not available" in w for w in s.warnings)
    rec = pipe.ingest("a.pdf", F.simple_pii_pdf())
    assert rec["status"] == "UPLOADED"


def test_local_mode_never_imports_cloud_sdks(pipe):
    rec = pipe.ingest("a.pdf", F.simple_pii_pdf())
    a = pipe.analyze(rec["id"])
    pipe.redact(rec["id"], a, {d.id for d in a.detections})
    assert "boto3" not in sys.modules and "botocore" not in sys.modules


def test_full_pipeline_works_with_no_credentials_and_no_network(pipe, monkeypatch):
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    def blocked(*a, **k):
        raise OSError("network disabled for test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    rec = pipe.ingest("offline.pdf", F.multi_pii_pdf())
    a = pipe.analyze(rec["id"])
    out = pipe.redact(rec["id"], a, {d.id for d in a.detections})
    assert out.status == Status.REDACTED_VERIFIED
    assert pipe.store.search("Application")


# ---- scanned PDFs and OCR -----------------------------------------------------
def test_scan_without_ocr_fails_with_actionable_message(settings):
    settings.ocr_enabled = False
    pipe = build_pipeline(settings)
    rec = pipe.ingest("scan.pdf", F.scanned_pdf(["Contact: jane.doe@example.com"]))
    assert rec["status"] == "UPLOADED"
    with pytest.raises(PipelineError) as e:
        pipe.analyze(rec["id"])
    assert e.value.status == Status.EXTRACTION_FAILED and "OCR" in e.value.message
    assert pipe.store.get_document(rec["id"])["status"] == "EXTRACTION_FAILED"


@needs_ocr
def test_scanned_pdf_ocr_detect_redact_verify(pipe):
    rec = pipe.ingest("scan.pdf", F.scanned_pdf([f"Email: {F.EMAIL}", f"Phone: {F.PHONE}"]))
    a = pipe.analyze(rec["id"])
    assert a.extraction.ocr_pages == [1]
    assert any("OCR" in w for w in a.warnings)  # limitation is surfaced to the user
    cats = {d.category for d in a.detections}
    assert "EMAIL" in cats
    out = pipe.redact(rec["id"], a, {d.id for d in a.detections})
    assert out.status == Status.REDACTED_VERIFIED
    assert any(c.name == "ocr_recheck" and c.passed for c in out.checks)
    # The pixels are gone: OCR of the output finds no email.
    from pii_platform.extraction import _ocr_words
    doc = pymupdf.open(stream=pipe.read_output(rec["id"]), filetype="pdf")
    words = " ".join(w.text for w in _ocr_words(doc[0], pipe.settings))
    assert "example.com" not in words
