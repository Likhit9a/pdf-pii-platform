"""Plain data models shared across modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    UPLOADED = "UPLOADED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    DETECTION_FAILED = "DETECTION_FAILED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    NO_PII_FOUND = "NO_PII_FOUND"
    REDACTION_FAILED = "REDACTION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    REDACTED_VERIFIED = "REDACTED_VERIFIED"


# Stage keys tracked separately so "extracted" never implies "redacted".
STAGES = ("validation", "extraction", "detection", "redaction", "verification")


class PipelineError(Exception):
    """Raised for expected failures; message is safe to show to the user."""

    def __init__(self, status: Status, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    line_key: tuple  # words sharing a key sit on the same visual line


@dataclass
class PageContent:
    number: int  # 1-based
    words: list[Word]
    source: str  # "text" or "ocr"
    text: str = ""
    offsets: list[tuple[int, int]] = field(default_factory=list)  # per-word (start, end) in text


@dataclass
class Detection:
    category: str
    page: int
    start: int
    end: int
    method: str  # regex | ner | custom | sweep
    rects: list[tuple[float, float, float, float]]
    value: str = field(default="", repr=False)  # in-memory only; never persisted or logged

    @property
    def id(self) -> str:
        return f"{self.page}:{self.start}:{self.end}:{self.category}"


@dataclass
class ExtractionResult:
    pages: list[PageContent]
    warnings: list[str] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    unanalyzed_pages: list[int] = field(default_factory=list)


@dataclass
class AnalysisResult:
    doc_id: str
    extraction: ExtractionResult
    detections: list[Detection]
    warnings: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass
class RedactionOutcome:
    doc_id: str
    status: Status
    message: str
    checks: list[CheckResult] = field(default_factory=list)
    redaction_count: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    pages_affected: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def mask_value(value: str) -> str:
    """Safe preview: first character plus length hint only."""
    v = value.strip()
    if not v:
        return ""
    return v[0] + "\u2022" * min(len(v) - 1, 12)
