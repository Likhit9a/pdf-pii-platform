import hashlib
import shutil

import pymupdf
import pytest

import pdf_factory as F
from pii_platform.models import PipelineError, Status


def run(pipe, data, select="all", name="t.pdf"):
    rec = pipe.ingest(name, data)
    assert rec["status"] == "UPLOADED", rec["status_detail"]
    analysis = pipe.analyze(rec["id"])
    if select == "all":
        ids = {d.id for d in analysis.detections}
    else:
        ids = {d.id for d in analysis.detections if d.category in select}
    return rec["id"], analysis, pipe.redact(rec["id"], analysis, ids)


def output_text(pipe, doc_id):
    doc = pymupdf.open(stream=pipe.read_output(doc_id), filetype="pdf")
    text = "\n".join(p.get_text("text") for p in doc)
    doc.close()
    return text


def test_text_pdf_redacted_and_verified(pipe):
    doc_id, _, out = run(pipe, F.simple_pii_pdf())
    assert out.status == Status.REDACTED_VERIFIED
    text = output_text(pipe, doc_id)
    for secret in (F.EMAIL, F.PHONE, F.NAME, F.SSN):
        assert secret not in text
    assert "Account summary" in text and "quarterly review" in text  # non-PII preserved


def test_multiple_pii_types_all_removed(pipe):
    doc_id, _, out = run(pipe, F.multi_pii_pdf())
    assert out.status == Status.REDACTED_VERIFIED
    text = output_text(pipe, doc_id)
    for secret in (F.EMAIL, F.PHONE, F.NAME, F.SSN, F.AADHAAR, F.PAN, F.CARD, "Baker Street"):
        assert secret not in text
    assert set(out.by_category) >= {"EMAIL", "PHONE", "PERSON", "SSN", "AADHAAR", "PAN", "CREDIT_CARD", "ADDRESS"}


def test_no_pii_document_reports_no_pii_found(pipe):
    rec = pipe.ingest("clean.pdf", F.no_pii_pdf())
    analysis = pipe.analyze(rec["id"])
    assert analysis.detections == []
    assert pipe.store.get_document(rec["id"])["status"] == "NO_PII_FOUND"


def test_redaction_removes_underlying_text_not_just_a_box(pipe):
    doc_id, _, _ = run(pipe, F.simple_pii_pdf())
    raw = pipe.read_output(doc_id)
    doc = pymupdf.open(stream=raw, filetype="pdf")
    # Every extraction mode must come back empty of the secret.
    for page in doc:
        for mode in ("text", "words", "blocks", "dict", "rawdict"):
            assert F.EMAIL not in str(page.get_text(mode))
    assert F.EMAIL.encode() not in raw


def test_value_repeated_across_pages_all_removed_even_if_only_one_selected(pipe):
    rec = pipe.ingest("r.pdf", F.repeated_value_pdf())
    analysis = pipe.analyze(rec["id"])
    first = [d for d in analysis.detections if d.page == 1]
    out = pipe.redact(rec["id"], analysis, {first[0].id})
    assert out.status == Status.REDACTED_VERIFIED
    text = output_text(pipe, rec["id"]).lower()
    assert "jane.doe@example.com" not in text
    assert out.pages_affected == [1, 2, 3]


def test_value_wrapped_across_lines_is_redacted(pipe):
    doc_id, _, out = run(pipe, F.wrapped_value_pdf())
    assert out.status == Status.REDACTED_VERIFIED
    text = output_text(pipe, doc_id)
    assert "415" not in text and "0132" not in text
    assert "Thank you." in text


def test_hidden_metadata_annotations_embedded_files_and_hidden_text_removed(pipe):
    doc_id, _, out = run(pipe, F.pdf_with_leaks())
    assert out.status == Status.REDACTED_VERIFIED
    raw = pipe.read_output(doc_id)
    doc = pymupdf.open(stream=raw, filetype="pdf")
    assert not any(v for k, v in doc.metadata.items() if k not in {"format", "encryption"})
    assert doc.embfile_count() == 0
    assert sum(len(list(p.annots())) for p in doc) == 0
    for secret in (F.EMAIL, F.PHONE, F.SSN, F.NAME):
        assert secret.encode() not in raw


def test_invisible_text_layer_is_removed(pipe):
    rec = pipe.ingest("h.pdf", F.hidden_text_pdf())
    analysis = pipe.analyze(rec["id"])
    out = pipe.redact(rec["id"], analysis, {d.id for d in analysis.detections})
    assert out.status == Status.REDACTED_VERIFIED
    assert F.EMAIL not in output_text(pipe, rec["id"])


def test_original_file_never_modified(settings):
    settings.delete_original_after_redaction = False
    from pii_platform.pipeline import build_pipeline

    pipe = build_pipeline(settings)
    data = F.simple_pii_pdf()
    rec = pipe.ingest("o.pdf", data)
    analysis = pipe.analyze(rec["id"])
    pipe.redact(rec["id"], analysis, {d.id for d in analysis.detections})
    stored = pipe.storage.original_path(rec["id"]).read_bytes()
    assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(data).hexdigest()
    assert F.EMAIL in "".join(p.get_text() for p in pymupdf.open(stream=stored, filetype="pdf"))


def test_original_deleted_after_success_by_default(pipe):
    doc_id, _, out = run(pipe, F.simple_pii_pdf())
    assert out.status == Status.REDACTED_VERIFIED
    assert not pipe.storage.original_path(doc_id).exists()
    assert pipe.storage.output_path(doc_id).exists()


def test_deselected_detection_is_left_alone(pipe):
    doc_id, _, out = run(pipe, F.simple_pii_pdf(), select={"EMAIL"})
    assert out.status == Status.REDACTED_VERIFIED
    text = output_text(pipe, doc_id)
    assert F.EMAIL not in text and F.PHONE in text  # reviewer chose to keep the phone number


def test_verification_failure_fails_closed(pipe, monkeypatch):
    """If redaction silently does nothing, verification must catch it and discard the output."""
    from pii_platform import pipeline as pl

    def fake_redact(src, dst, detections):
        shutil.copy(src, dst)
        return {"boxes_applied": 0}

    monkeypatch.setattr(pl, "redact_pdf", fake_redact)
    doc_id, _, out = run(pipe, F.simple_pii_pdf())
    assert out.status == Status.VERIFICATION_FAILED
    assert any(c.name == "text_not_recoverable" and not c.passed for c in out.checks)
    assert not pipe.storage.output_path(doc_id).exists()
    rec = pipe.store.get_document(doc_id)
    assert rec["status"] == "VERIFICATION_FAILED" and rec["stages"]["verification"] == "failed"
    with pytest.raises(PipelineError):
        pipe.read_output(doc_id)
    assert pipe.store.search("quarterly") == []  # never indexed


def test_redaction_exception_is_reported_not_swallowed(pipe, monkeypatch):
    from pii_platform import pipeline as pl

    monkeypatch.setattr(pl, "redact_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    doc_id, _, out = run(pipe, F.simple_pii_pdf())
    assert out.status == Status.REDACTION_FAILED
    assert pipe.store.get_document(doc_id)["status"] == "REDACTION_FAILED"
    assert not pipe.storage.output_path(doc_id).exists()


def test_rotated_page_is_redacted(pipe):
    doc_id, _, out = run(pipe, F.rotated_pdf())
    assert out.status == Status.REDACTED_VERIFIED
    assert F.EMAIL not in output_text(pipe, doc_id)


def test_zero_selected_detections_still_produces_clean_copy(pipe):
    rec = pipe.ingest("z.pdf", F.simple_pii_pdf())
    analysis = pipe.analyze(rec["id"])
    out = pipe.redact(rec["id"], analysis, set())
    assert out.status == Status.REDACTED_VERIFIED and out.redaction_count == 0
