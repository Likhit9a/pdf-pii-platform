"""Application settings. Everything defaults to local-only, zero-cloud operation."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CATEGORIES = (
    "EMAIL", "PHONE", "PERSON", "ADDRESS",
    "AADHAAR", "PAN", "SSN", "CREDIT_CARD",
)
OPTIONAL_CATEGORIES = ("LOCATION",)  # noisy, off by default

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader (no extra dependency). Does not override real env vars."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path
    max_file_mb: int = 20
    max_pages: int = 50
    enabled_categories: tuple[str, ...] = DEFAULT_CATEGORIES
    ocr_enabled: bool = True
    ocr_dpi: int = 300
    tesseract_cmd: str | None = None
    allow_unanalyzed_pages: bool = False
    delete_original_after_redaction: bool = True
    backend: str = "local"
    custom_patterns: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # ---- derived paths -------------------------------------------------
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "platform.db"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for d in (self.uploads_dir, self.outputs_dir, self.tmp_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- construction --------------------------------------------------
    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        _load_dotenv(PROJECT_ROOT / ".env")
        env = os.environ
        data_dir = Path(overrides.pop("data_dir", None) or env.get("PII_DATA_DIR") or PROJECT_ROOT / "data")

        cats_env = env.get("PII_CATEGORIES")
        cats = tuple(c.strip().upper() for c in cats_env.split(",") if c.strip()) if cats_env else DEFAULT_CATEGORIES

        tess = env.get("TESSERACT_CMD") or None
        if tess is None:
            win_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
            if win_default.is_file():
                tess = str(win_default)

        s = cls(
            data_dir=data_dir,
            max_file_mb=int(env.get("PII_MAX_FILE_MB", 20)),
            max_pages=int(env.get("PII_MAX_PAGES", 50)),
            enabled_categories=cats,
            ocr_enabled=_bool(env.get("PII_OCR_ENABLED"), True),
            ocr_dpi=int(env.get("PII_OCR_DPI", 300)),
            tesseract_cmd=tess,
            allow_unanalyzed_pages=_bool(env.get("PII_ALLOW_UNANALYZED_PAGES"), False),
            delete_original_after_redaction=_bool(env.get("PII_DELETE_ORIGINAL"), True),
            backend=(env.get("PII_BACKEND") or "local").strip().lower(),
        )
        for k, v in overrides.items():
            if not hasattr(s, k):
                raise TypeError(f"Unknown setting: {k}")
            setattr(s, k, v)
        s.custom_patterns = s._load_custom_patterns() if not s.custom_patterns else s.custom_patterns
        return s

    def _load_custom_patterns(self) -> dict[str, str]:
        """Optional: <data_dir>/custom_patterns.json -> {"EMPLOYEE_ID": "EMP-\\d{6}"}"""
        path = self.data_dir / "custom_patterns.json"
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.warnings.append("custom_patterns.json is not valid JSON and was ignored.")
            return {}
        good: dict[str, str] = {}
        for name, pattern in raw.items():
            key = re.sub(r"[^A-Z0-9_]", "_", str(name).upper())
            try:
                re.compile(pattern)
            except re.error:
                self.warnings.append(f"Custom pattern '{name}' is not a valid regex and was ignored.")
                continue
            good[key] = pattern
        return good
