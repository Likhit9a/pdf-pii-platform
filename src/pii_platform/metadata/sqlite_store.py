"""SQLite metadata store with FTS5 search over REDACTED text only.

The database never receives original text or detected values: only counts, categories, and the
text extracted back out of the verified redacted PDF.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import MetadataStore

_JSON_FIELDS = {"stages", "summary", "warnings"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER,
    page_count INTEGER,
    status TEXT NOT NULL,
    status_detail TEXT DEFAULT '',
    stages TEXT DEFAULT '{}',
    summary TEXT DEFAULT '{}',
    warnings TEXT DEFAULT '[]',
    ocr_used INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fts_query(q: str) -> str:
    tokens = re.findall(r"\w+", q, flags=re.UNICODE)
    return " ".join(f'"{t}"*' for t in tokens)


class SQLiteStore(MetadataStore):
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            try:
                c.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5("
                    "content, doc_id UNINDEXED, page UNINDEXED, tokenize='unicode61')"
                )
                self.fts = True
            except sqlite3.OperationalError:
                c.execute("CREATE TABLE IF NOT EXISTS doc_text (doc_id TEXT, page INTEGER, content TEXT)")
                self.fts = False

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- documents -----------------------------------------------------
    def create_document(self, record: dict[str, Any]) -> None:
        rec = {
            "sha256": None, "size_bytes": None, "page_count": None, "status_detail": "",
            "stages": {}, "summary": {}, "warnings": [], "ocr_used": 0, **record,
        }
        rec["created_at"] = rec["updated_at"] = _now()
        for f in _JSON_FIELDS:
            rec[f] = json.dumps(rec[f])
        cols = ",".join(rec)
        marks = ",".join("?" for _ in rec)
        with self._conn() as c:
            c.execute(f"INSERT INTO documents ({cols}) VALUES ({marks})", list(rec.values()))

    def update_document(self, doc_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = _now()
        for f in _JSON_FIELDS & fields.keys():
            fields[f] = json.dumps(fields[f])
        sets = ",".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE documents SET {sets} WHERE id=?", [*fields.values(), doc_id])

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        for f in _JSON_FIELDS:
            d[f] = json.loads(d[f]) if d.get(f) else ({} if f != "warnings" else [])
        return d

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            return self._row(c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())

    def list_documents(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM documents", []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [self._row(r) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def delete_document(self, doc_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            if self.fts:
                c.execute("DELETE FROM doc_fts WHERE doc_id=?", (doc_id,))
            else:
                c.execute("DELETE FROM doc_text WHERE doc_id=?", (doc_id,))

    # ---- search --------------------------------------------------------
    def index_text(self, doc_id: str, pages: list[str]) -> None:
        with self._conn() as c:
            table = "doc_fts" if self.fts else "doc_text"
            c.execute(f"DELETE FROM {table} WHERE doc_id=?", (doc_id,))
            for number, text in enumerate(pages, start=1):
                if text.strip():
                    c.execute(f"INSERT INTO {table} (content, doc_id, page) VALUES (?,?,?)", (text, doc_id, number))

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        hits: list[dict[str, Any]] = []
        with self._conn() as c:
            if self.fts:
                fq = _fts_query(q)
                if fq:
                    rows = c.execute(
                        "SELECT d.id, d.display_name, d.created_at, f.page, "
                        "snippet(doc_fts, 0, '[', ']', ' ... ', 12) AS snippet "
                        "FROM doc_fts f JOIN documents d ON d.id = f.doc_id "
                        "WHERE doc_fts MATCH ? AND d.status='REDACTED_VERIFIED' ORDER BY rank LIMIT ?",
                        (fq, limit),
                    ).fetchall()
                    hits.extend({**dict(r), "match": "content"} for r in rows)
            else:
                like = f"%{q}%"
                rows = c.execute(
                    "SELECT d.id, d.display_name, d.created_at, t.page, substr(t.content,1,160) AS snippet "
                    "FROM doc_text t JOIN documents d ON d.id=t.doc_id "
                    "WHERE t.content LIKE ? AND d.status='REDACTED_VERIFIED' LIMIT ?",
                    (like, limit),
                ).fetchall()
                hits.extend({**dict(r), "match": "content"} for r in rows)

            seen = {h["id"] for h in hits}
            rows = c.execute(
                "SELECT id, display_name, created_at FROM documents "
                "WHERE display_name LIKE ? AND status='REDACTED_VERIFIED' LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
            for r in rows:
                if r["id"] not in seen:
                    hits.append({**dict(r), "page": None, "snippet": "", "match": "filename"})
        return hits

    # ---- dashboard -----------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._conn() as c:
            by_status = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) AS n FROM documents GROUP BY status")}
            rows = c.execute("SELECT summary FROM documents WHERE status='REDACTED_VERIFIED'").fetchall()
        by_category: dict[str, int] = {}
        total = 0
        for r in rows:
            s = json.loads(r["summary"] or "{}")
            total += int(s.get("redaction_count", 0))
            for k, v in (s.get("by_category") or {}).items():
                by_category[k] = by_category.get(k, 0) + int(v)
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "verified": by_status.get("REDACTED_VERIFIED", 0),
            "failed": sum(v for k, v in by_status.items() if k.endswith("_FAILED")),
            "redactions": total,
            "by_category": by_category,
        }
