# Secure PDF PII Detection, Redaction and Search Platform

Local-first web app: upload a PDF, detect personally identifiable information (PII), review it,
**genuinely redact** it, verify the result, and search the redacted documents.
Runs entirely on your computer. **Cost: 0. No AWS account or credentials needed.**

> Status: Stage 2 (local MVP). AWS adapters, final UI polish and the full README come in later stages.

## Run it (Windows)
1. Open this folder in VS Code (File > Open Folder).
2. Double-click `run.bat` (or in the VS Code terminal run `run.bat`).
   First run creates `.venv` and installs everything (a few minutes). The browser opens at http://localhost:8501.
3. Linux/macOS: `./run.sh`

Run the tests: `run.bat test`  (or `./run.sh test`).

Optional, for scanned PDFs: install Tesseract OCR (Windows installer from the UB Mannheim build) to the default
folder `C:\Program Files\Tesseract-OCR`. Without it, text PDFs work normally and scans are rejected with a clear message.

## How a document flows
Upload -> validate (header, size, pages, corruption, encryption) -> extract text (OCR for scans) ->
detect PII (regex + local spaCy model) -> **you review** -> redact -> verify -> download -> history/search.
A document is only `REDACTED_VERIFIED` if every stage passed; otherwise the output is deleted.

## What "genuine redaction" means here
1. PyMuPDF `apply_redactions()` removes the text (and blanks image pixels) under each box.
2. Metadata, XMP, annotations, embedded files, JavaScript, links and hidden text are stripped.
3. The file is rewritten from scratch (no incremental save), as a new file; the original is never modified.
4. The output is reopened and checked: no redacted value in extracted text, no metadata, annotations,
   embedded files or form fields. Scanned pages are re-OCR'd.

## Limits (be honest in your demo)
- Detection is automatic and imperfect: expect misses and false positives (that is why review exists).
- Text inside images on otherwise-text pages is not analyzed. Pure scans are handled by OCR, which can misread.
- Names: labelled names (`Name: ...`) plus a small local model; unusual names can be missed.
- Verification covers extractable text, metadata, annotations, embedded files and form fields, not every
  possible PDF structure.
- Password-protected PDFs are rejected. Rotated pages are redacted and verified.
- PyMuPDF is AGPL-licensed: fine for coursework, check the licence before commercial use.

## Privacy model
No document text or detected value is logged or stored in the database. Search indexes only text extracted
back out of the verified redacted PDF. The original upload is deleted after verified redaction (toggle in
*Detection settings*). Disk file names are random IDs; user file names are display-only and are replaced
if they contain PII. Streamlit telemetry is disabled in `.streamlit/config.toml`.

## Configuration
Copy `.env.example` to `.env` to change limits. Custom ID formats: create `data/custom_patterns.json`, e.g.
`{"EMPLOYEE_ID": "EMP-\\d{6}"}`.

## Layout
`app.py` (UI entry) | `ui/views.py` | `src/pii_platform/` (validation, extraction, detection, redaction,
verification, pipeline, `storage/`, `metadata/`) | `tests/` (synthetic PDFs only)
