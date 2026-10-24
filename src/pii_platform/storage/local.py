"""Local filesystem storage. Disk names are generated UUIDs only; user filenames never touch paths."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from ..config import Settings
from .base import StorageBackend

_DOC_ID = re.compile(r"^[0-9a-f]{32}$")


class LocalStorage(StorageBackend):
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()

    # ---- path safety ---------------------------------------------------
    def _check(self, doc_id: str) -> str:
        if not _DOC_ID.match(doc_id or ""):
            raise ValueError("Invalid document identifier.")
        return doc_id

    def _inside(self, base: Path, name: str) -> Path:
        p = (base / name).resolve()
        if base.resolve() not in p.parents:
            raise ValueError("Path escapes the data directory.")
        return p

    def original_path(self, doc_id: str) -> Path:
        return self._inside(self.settings.uploads_dir, f"{self._check(doc_id)}.pdf")

    def output_path(self, doc_id: str) -> Path:
        return self._inside(self.settings.outputs_dir, f"{self._check(doc_id)}.redacted.pdf")

    # ---- operations ----------------------------------------------------
    def save_original(self, doc_id: str, data: bytes) -> Path:
        path = self.original_path(doc_id)
        path.write_bytes(data)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path

    def new_temp_path(self, suffix: str = ".pdf") -> Path:
        self.settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=suffix, dir=self.settings.tmp_dir)
        os.close(fd)
        return Path(name)

    def promote_output(self, temp_path: Path, doc_id: str) -> Path:
        dest = self.output_path(doc_id)
        os.replace(temp_path, dest)
        return dest

    def delete_original(self, doc_id: str) -> None:
        self.original_path(doc_id).unlink(missing_ok=True)

    def delete_all(self, doc_id: str) -> None:
        self.original_path(doc_id).unlink(missing_ok=True)
        self.output_path(doc_id).unlink(missing_ok=True)

    def read_output(self, doc_id: str) -> bytes:
        return self.output_path(doc_id).read_bytes()
