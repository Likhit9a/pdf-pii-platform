import pdf_factory as F
from pii_platform.models import Status


def redact_all(pipe, data, name="doc.pdf"):
    rec = pipe.ingest(name, data)
    analysis = pipe.analyze(rec["id"])
    out = pipe.redact(rec["id"], analysis, {d.id for d in analysis.detections})
    assert out.status == Status.REDACTED_VERIFIED
    return rec["id"]


def test_search_finds_safe_text(pipe):
    doc_id = redact_all(pipe, F.simple_pii_pdf())
    hits = pipe.store.search("quarterly review")
    assert [h["id"] for h in hits] == [doc_id]
    assert hits[0]["page"] == 1


def test_search_never_returns_original_pii(pipe):
    redact_all(pipe, F.multi_pii_pdf())
    for secret in ("jane", "doe", "example.com", "4111", "415", "123-45-6789", "ABCDE1234F", "Baker"):
        for h in pipe.store.search(secret):
            blob = (h["snippet"] + h["display_name"]).lower()
            assert secret.lower() not in blob


def test_database_file_contains_no_original_pii(pipe, settings):
    redact_all(pipe, F.multi_pii_pdf())
    redact_all(pipe, F.repeated_value_pdf())
    raw = settings.db_path.read_bytes()
    for secret in (F.EMAIL, F.PHONE, F.NAME, F.SSN, F.AADHAAR, F.PAN, F.CARD, "Baker Street"):
        assert secret.encode() not in raw


def test_logs_contain_no_original_pii(pipe, settings):
    redact_all(pipe, F.multi_pii_pdf())
    log = (settings.logs_dir / "app.log").read_text(encoding="utf-8")
    assert "stage=detection" in log  # logging works...
    for secret in (F.EMAIL, F.PHONE, F.NAME, F.SSN, F.AADHAAR, F.PAN, "Baker"):
        assert secret not in log  # ...but never records content


def test_unverified_documents_are_not_searchable(pipe):
    rec = pipe.ingest("u.pdf", F.simple_pii_pdf())
    pipe.analyze(rec["id"])
    assert pipe.store.search("quarterly") == []


def test_history_stores_safe_metadata_only(pipe):
    doc_id = redact_all(pipe, F.multi_pii_pdf(), name="my file.pdf")
    rec = pipe.store.get_document(doc_id)
    assert rec["display_name"] == "my file.pdf"
    assert rec["created_at"] and rec["sha256"] and rec["page_count"] == 1
    assert rec["summary"]["by_category"]["EMAIL"] == 1
    assert F.EMAIL not in str(rec)


def test_delete_removes_record_files_and_index(pipe, settings):
    doc_id = redact_all(pipe, F.simple_pii_pdf())
    assert pipe.store.search("quarterly")
    pipe.delete(doc_id)
    assert pipe.store.get_document(doc_id) is None
    assert pipe.store.search("quarterly") == []
    assert not pipe.storage.output_path(doc_id).exists()
    assert not pipe.storage.original_path(doc_id).exists()


def test_dashboard_stats(pipe):
    redact_all(pipe, F.simple_pii_pdf())
    pipe.ingest("bad.pdf", b"not a pdf")
    s = pipe.store.stats()
    assert s["total"] == 2 and s["verified"] == 1 and s["failed"] == 1 and s["redactions"] >= 4
