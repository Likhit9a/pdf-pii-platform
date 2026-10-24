import pytest

import pdf_factory as F
from pii_platform.models import PipelineError, Status
from pii_platform.validation import sanitize_display_name, validate_pdf_bytes


def test_valid_pdf_accepted(settings):
    r = validate_pdf_bytes(F.simple_pii_pdf(), settings)
    assert r.page_count == 1 and len(r.sha256) == 64


def test_corrupt_pdf_rejected(settings):
    data = F.simple_pii_pdf()
    corrupt = data[:200] + b"\x00garbage\x00" * 40  # keeps the header, destroys the structure
    with pytest.raises(PipelineError) as e:
        validate_pdf_bytes(corrupt, settings)
    assert e.value.status == Status.VALIDATION_FAILED


def test_not_a_pdf_rejected_even_if_named_pdf(settings, pipe):
    rec = pipe.ingest("report.pdf", b"just some plain text, definitely not a PDF")
    assert rec["status"] == "VALIDATION_FAILED"
    assert "not a PDF" in rec["status_detail"]


def test_empty_file_rejected(pipe):
    rec = pipe.ingest("empty.pdf", b"")
    assert rec["status"] == "VALIDATION_FAILED" and "empty" in rec["status_detail"].lower()


def test_blank_document_rejected(pipe):
    rec = pipe.ingest("blank.pdf", F.blank_pdf())
    assert rec["status"] == "VALIDATION_FAILED"
    assert "nothing to process" in rec["status_detail"]


def test_password_protected_rejected(pipe):
    rec = pipe.ingest("locked.pdf", F.encrypted_pdf())
    assert rec["status"] == "VALIDATION_FAILED"
    assert "password" in rec["status_detail"].lower()


def test_oversized_file_rejected(settings, pipe):
    settings.max_file_mb = 1
    data = F.simple_pii_pdf() + b"0" * (2 * 1024 * 1024)
    rec = pipe.ingest("big.pdf", data)
    assert rec["status"] == "VALIDATION_FAILED" and "limit" in rec["status_detail"]


def test_page_limit_enforced(settings, pipe):
    settings.max_pages = 3
    rec = pipe.ingest("long.pdf", F.many_pages_pdf(5))
    assert rec["status"] == "VALIDATION_FAILED" and "5 pages" in rec["status_detail"]


def test_rejected_upload_stores_no_file(settings, pipe):
    rec = pipe.ingest("x.pdf", b"nope")
    assert list(settings.uploads_dir.iterdir()) == []
    assert rec["stages"]["validation"] == "failed"


def test_filename_sanitized_and_traversal_blocked():
    assert sanitize_display_name("../../etc/passwd") == "passwd"
    assert sanitize_display_name("C:\\Users\\me\\a<b>.pdf") == "a_b_.pdf"
    assert sanitize_display_name("") == "document.pdf"


def test_filename_containing_pii_is_replaced(pipe):
    rec = pipe.ingest("jane.doe@example.com_statement.pdf", F.no_pii_pdf())
    assert "example.com" not in rec["display_name"]
    assert rec["display_name"].startswith("document-")
