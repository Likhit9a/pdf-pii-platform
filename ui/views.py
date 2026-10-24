"""Streamlit views. All processing is delegated to the Pipeline; views never touch PII storage."""
from __future__ import annotations

import json

import pandas as pd
import pymupdf
import streamlit as st

from pii_platform.config import DEFAULT_CATEGORIES, OPTIONAL_CATEGORIES
from pii_platform.detection import ner_available
from pii_platform.extraction import ocr_available
from pii_platform.models import PipelineError, Status, mask_value

STATUS_BADGE = {
    "REDACTED_VERIFIED": ":green-badge[Redacted & verified]",
    "AWAITING_REVIEW": ":orange-badge[Awaiting review]",
    "NO_PII_FOUND": ":blue-badge[No PII found]",
    "UPLOADED": ":gray-badge[Uploaded]",
}
STAGE_LABELS = [
    ("validation", "Validated"), ("extraction", "Text extracted"), ("detection", "PII detected"),
    ("redaction", "Redacted"), ("verification", "Verified"),
]


def badge(status: str) -> str:
    return STATUS_BADGE.get(status, f":red-badge[{status.replace('_', ' ').title()}]")


def stage_tracker(stages: dict[str, str]) -> None:
    cols = st.columns(len(STAGE_LABELS))
    for col, (key, label) in zip(cols, STAGE_LABELS):
        state = stages.get(key, "pending")
        icon = {"ok": "\u2705", "failed": "\u274c", "skipped": "\u23ed\ufe0f"}.get(state, "\u23f3")
        col.markdown(f"{icon} **{label}**")


def _docs_frame(docs: list[dict]) -> pd.DataFrame:
    rows = []
    for d in docs:
        s = d.get("summary") or {}
        rows.append({
            "Document": d["display_name"], "Status": d["status"].replace("_", " ").title(),
            "Pages": d.get("page_count"), "Redactions": s.get("redaction_count"),
            "Created (UTC)": d["created_at"].replace("T", " ")[:19], "ID": d["id"][:8],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- dashboard
def dashboard(pipe) -> None:
    st.header("Dashboard")
    s = pipe.store.stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Documents", s["total"])
    c2.metric("Verified redactions", s["verified"])
    c3.metric("Failed / rejected", s["failed"])
    c4.metric("Items redacted", s["redactions"])

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Redactions by category")
        if s["by_category"]:
            st.bar_chart(pd.Series(s["by_category"], name="items"))
        else:
            st.caption("Nothing redacted yet. Open **Process document** to start.")
    with right:
        st.subheader("System status")
        st.markdown(f"- Mode: **local only** (no cloud calls)\n"
                    f"- OCR for scans: {'**available**' if ocr_available(pipe.settings) else '**not available**'}\n"
                    f"- Name detection model: {'**available**' if ner_available() else '**not installed** (labelled names only)'}\n"
                    f"- Limits: {pipe.settings.max_file_mb} MB, {pipe.settings.max_pages} pages")
    st.subheader("Recent documents")
    docs = pipe.store.list_documents(limit=8)
    if docs:
        st.dataframe(_docs_frame(docs), hide_index=True, width="stretch")
    else:
        st.caption("No documents yet.")


# ---------------------------------------------------------------- process
def _render_outcome(pipe, outcome) -> None:
    if outcome.status == Status.REDACTED_VERIFIED:
        st.success(f"{outcome.message} Pages affected: {', '.join(map(str, outcome.pages_affected)) or 'none'}.")
    else:
        st.error(outcome.message)
    if outcome.checks:
        with st.expander("Verification checks", expanded=outcome.status != Status.REDACTED_VERIFIED):
            for c in outcome.checks:
                mark = "\u2705" if c.passed else ("\u274c" if c.blocking else "\u26a0\ufe0f")
                st.markdown(f"{mark} **{c.name.replace('_', ' ')}** - {c.detail}")
    if outcome.by_category:
        st.caption("Redacted: " + ", ".join(f"{k} \u00d7 {v}" for k, v in sorted(outcome.by_category.items())))


def _download_buttons(pipe, doc_id: str, name: str) -> None:
    rec = pipe.store.get_document(doc_id)
    if not rec or rec["status"] != Status.REDACTED_VERIFIED.value:
        return
    base = name.rsplit(".", 1)[0]
    c1, c2 = st.columns(2)
    c1.download_button("\u2b07\ufe0f Download redacted PDF", pipe.read_output(doc_id),
                       file_name=f"{base}.redacted.pdf", mime="application/pdf", key=f"dl-pdf-{doc_id}",
                       type="primary", width="stretch")
    report = {"document_id": doc_id, "file": name, "status": rec["status"], **(rec.get("summary") or {})}
    c2.download_button("\u2b07\ufe0f Download redaction summary (no PII)", json.dumps(report, indent=2),
                       file_name=f"{base}.summary.json", mime="application/json", key=f"dl-json-{doc_id}",
                       width="stretch")


def _preview_image(pipe, doc_id: str, page_no: int, dets) -> bytes | None:
    path = pipe.storage.original_path(doc_id)
    if not path.is_file():
        return None
    doc = pymupdf.open(str(path))
    try:
        page = doc[page_no - 1]
        for d in dets:
            for r in d.rects:
                page.draw_rect(pymupdf.Rect(r), color=(1, 0, 0), width=1.2)
        return page.get_pixmap(dpi=90).tobytes("png")
    finally:
        doc.close()


def process(pipe) -> None:
    st.header("Process a document")
    st.caption("Upload \u2192 extract \u2192 detect \u2192 review \u2192 redact \u2192 verify. Everything runs on this computer.")

    with st.expander("Detection settings"):
        all_cats = list(DEFAULT_CATEGORIES) + list(OPTIONAL_CATEGORIES) + sorted(pipe.settings.custom_patterns)
        chosen = st.multiselect("Categories to detect", all_cats, default=list(pipe.settings.enabled_categories) +
                                sorted(pipe.settings.custom_patterns))
        st.checkbox("Delete the original upload after a verified redaction",
                    value=pipe.settings.delete_original_after_redaction, key="del_orig",
                    help="Recommended: avoids keeping an unredacted copy on disk.")
        pipe.settings.delete_original_after_redaction = st.session_state.get("del_orig", True)

    upload = st.file_uploader("PDF file", type=["pdf"], accept_multiple_files=False)
    if upload and st.button("Upload & analyze", type="primary"):
        st.session_state.pop("analysis", None)
        with st.spinner("Validating..."):
            rec = pipe.ingest(upload.name, upload.getvalue())
        st.session_state["doc_id"] = rec["id"]
        if rec["status"] != Status.UPLOADED.value:
            st.error(rec["status_detail"])
        else:
            try:
                with st.spinner("Extracting text and detecting PII..."):
                    st.session_state["analysis"] = pipe.analyze(rec["id"], set(chosen))
            except PipelineError as err:
                st.error(err.message)

    doc_id = st.session_state.get("doc_id")
    rec = pipe.store.get_document(doc_id) if doc_id else None
    if not rec:
        return

    st.divider()
    st.subheader(rec["display_name"])
    st.markdown(badge(rec["status"]))
    stage_tracker(rec["stages"])
    if rec["status"] in {"VALIDATION_FAILED", "EXTRACTION_FAILED", "DETECTION_FAILED"}:
        st.error(rec["status_detail"])
        return

    analysis = st.session_state.get("analysis")
    if analysis is not None and analysis.doc_id == doc_id and rec["status"] in {"AWAITING_REVIEW", "NO_PII_FOUND"}:
        for w in analysis.warnings:
            st.warning(w)
        if rec["status"] == "NO_PII_FOUND":
            st.info(rec["status_detail"])
            if st.button("Create a sanitized copy anyway (metadata, annotations, hidden content removed)"):
                st.session_state["outcome"] = pipe.redact(doc_id, analysis, set())
                st.rerun()
        else:
            st.markdown("#### Review detections")
            reveal = st.checkbox("Reveal detected values (shows sensitive data on screen)", value=False)
            df = pd.DataFrame([{
                "Redact": True, "Category": d.category, "Page": d.page, "Method": d.method,
                "Value": d.value if reveal else mask_value(d.value), "id": d.id,
            } for d in analysis.detections])
            edited = st.data_editor(
                df, hide_index=True, width="stretch", key=f"review-{doc_id}-{reveal}",
                disabled=["Category", "Page", "Method", "Value"], column_config={"id": None},
            )
            selected_ids = set(edited.loc[edited["Redact"], "id"])
            st.caption(f"{len(selected_ids)} of {len(df)} selected. Every other occurrence of a selected value "
                       "is redacted too.")
            if reveal:
                with st.expander("Page preview with detections outlined in red"):
                    pages = sorted({d.page for d in analysis.detections})
                    pno = st.selectbox("Page", pages)
                    img = _preview_image(pipe, doc_id, pno, [d for d in analysis.detections if d.page == pno])
                    if img:
                        st.image(img)
            if st.button("Redact selected items", type="primary", disabled=not selected_ids):
                with st.spinner("Redacting and verifying..."):
                    st.session_state["outcome"] = pipe.redact(doc_id, analysis, selected_ids)
                st.rerun()

    outcome = st.session_state.get("outcome")
    if outcome is not None and outcome.doc_id == doc_id:
        st.markdown("#### Result")
        _render_outcome(pipe, outcome)
        _download_buttons(pipe, doc_id, rec["display_name"])


# ---------------------------------------------------------------- history
def history(pipe) -> None:
    st.header("Document history")
    status_filter = st.selectbox("Filter by status", ["All"] + [s.value for s in Status])
    docs = pipe.store.list_documents(None if status_filter == "All" else status_filter, limit=500)
    if not docs:
        st.info("No documents match.")
        return
    st.dataframe(_docs_frame(docs), hide_index=True, width="stretch")

    st.subheader("Details")
    labels = {f"{d['display_name']} ({d['id'][:8]})": d["id"] for d in docs}
    pick = st.selectbox("Select a document", list(labels))
    rec = pipe.store.get_document(labels[pick])
    st.markdown(badge(rec["status"]))
    stage_tracker(rec["stages"])
    st.write(rec["status_detail"])
    for w in rec.get("warnings", []):
        st.warning(w)
    s = rec.get("summary") or {}
    if s.get("checks"):
        with st.expander("Verification checks"):
            for c in s["checks"]:
                mark = "\u2705" if c["passed"] else "\u274c"
                label = c["name"].replace("_", " ")
                st.markdown(f"{mark} **{label}** - {c['detail']}")
    _download_buttons(pipe, rec["id"], rec["display_name"])

    st.markdown("##### Delete")
    confirm = st.checkbox("I understand this permanently deletes the record and its files", key=f"cf-{rec['id']}")
    if st.button("Delete this document", disabled=not confirm):
        pipe.delete(rec["id"])
        st.session_state.pop("doc_id", None)
        st.rerun()


# ---------------------------------------------------------------- search
def search(pipe) -> None:
    st.header("Search")
    st.caption("Searches only the redacted text of verified documents, plus file names.")
    q = st.text_input("Search term", placeholder="e.g. quarterly review")
    if not q:
        return
    hits = pipe.store.search(q)
    if not hits:
        st.info("No matching documents.")
        return
    st.write(f"{len(hits)} result(s)")
    for h in hits:
        with st.container(border=True):
            st.markdown(f"**{h['display_name']}**  \n`{h['id'][:8]}` \u00b7 {h['created_at'][:19].replace('T', ' ')} UTC"
                        + (f" \u00b7 page {h['page']}" if h["page"] else " \u00b7 file name match"))
            if h["snippet"]:
                st.markdown(f"> {h['snippet']}")


# ---------------------------------------------------------------- about
def about(pipe) -> None:
    st.header("About & limitations")
    st.markdown("""
**How redaction works.** Detected text is physically removed from the PDF content (not covered by a box),
metadata, annotations, embedded files and hidden text are stripped, the file is rewritten from scratch, and then
re-opened to confirm none of the redacted values can still be extracted. If any check fails the output is
discarded.

**What it cannot guarantee**
- Detection is automatic and imperfect: it can miss PII and flag harmless text. Review before redacting.
- Text inside images is not analyzed unless the page is a pure scan handled by OCR; OCR can misread characters.
- Names are detected from labels (e.g. `Name: ...`) and a small local language model, so unusual names can be missed.
- Verification covers extractable text, metadata, annotations, embedded files and form fields, not every possible
  hidden structure in a PDF.

**Privacy.** No document content or detected value is logged or stored in the database. Search indexes only the
text extracted back out of the verified redacted PDF. By default the original upload is deleted after a verified
redaction. The app makes no network calls, and Streamlit's usage telemetry is switched off in `.streamlit/config.toml`.
""")
