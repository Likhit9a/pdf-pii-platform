"""PII detection using configurable regexes plus an optional local spaCy NER model.

Detected values live in memory only (Detection.value); they are never persisted or logged.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .config import Settings
from .models import Detection, PageContent

# Higher priority wins when two detections overlap.
PRIORITY = [
    "CREDIT_CARD", "AADHAAR", "SSN", "PAN", "EMAIL", "PHONE",
    "ADDRESS", "PERSON", "LOCATION",
]

_REGEX: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "PHONE": re.compile(r"(?<![\w.])\+?\d[\d \t().\-]{8,17}\d(?![\w])"),
    # A number broken by exactly one line break (e.g. "+1 415\n555 0132").
    "PHONE_WRAPPED": re.compile(r"(?<![\w.])\+?\d[\d \t().\-]{2,16}\n[\d \t().\-]{2,16}\d(?![\w])"),
    "AADHAAR": re.compile(r"(?<![\d\-])[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?![\d\-])"),
    "SSN": re.compile(r"(?<![\d\-])\d{3}-\d{2}-\d{4}(?![\d\-])"),
    "PAN": re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])"),
    "CREDIT_CARD": re.compile(r"(?<![\d\-])(?:\d[ \-]?){12,18}\d(?![\d\-])"),
    "ADDRESS": re.compile(
        r"\b\d{1,5}[A-Za-z]?(?:[-/]\d+)?,?[ \t]+(?:[A-Z][\w.'\-]*[ \t]+){1,4}"
        r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Lane|Ln\.?|Boulevard|Blvd\.?|Drive|Dr\.?)"
        r"(?:,[ \t]*[A-Z][A-Za-z]+(?:[ \t][A-Z][A-Za-z]+)?){0,2}"
        r"(?:,?[ \t]*[A-Z]{2})?(?:[ \t]*\d{5,6}(?:-\d{4})?)?"
    ),
}

# "Name: Jane Doe" style labels; works even when spaCy is unavailable.
_LABELLED_NAME = re.compile(
    r"(?:Name|Patient|Employee|Customer|Client|Applicant|Student|Signed by|Contact)[ \t]*:[ \t]*"
    r"((?:[A-Z][a-zA-Z'\-]+\.?)(?:[ \t]+[A-Z][a-zA-Z'\-]+\.?){1,3})"
)


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


@lru_cache(maxsize=1)
def _load_nlp():
    try:
        import spacy

        return spacy.load(
            "en_core_web_sm",
            disable=["parser", "lemmatizer", "attribute_ruler", "tagger", "senter"],
        )
    except Exception:
        return None


def ner_available() -> bool:
    return _load_nlp() is not None


def _raw_spans(text: str, categories: set[str], custom: dict[str, str]) -> list[tuple[str, int, int, str]]:
    """Return (category, start, end, method) candidates for one page of text."""
    spans: list[tuple[str, int, int, str]] = []

    for cat in ("EMAIL", "AADHAAR", "SSN", "PAN", "ADDRESS"):
        if cat in categories:
            for m in _REGEX[cat].finditer(text):
                spans.append((cat, m.start(), m.end(), "regex"))

    if "CREDIT_CARD" in categories:
        for m in _REGEX["CREDIT_CARD"].finditer(text):
            digits = re.sub(r"\D", "", m.group())
            if 13 <= len(digits) <= 19 and luhn_ok(digits):
                spans.append(("CREDIT_CARD", m.start(), m.end(), "regex"))

    if "PHONE" in categories:
        for key in ("PHONE", "PHONE_WRAPPED"):
            for m in _REGEX[key].finditer(text):
                digits = re.sub(r"\D", "", m.group())
                if 10 <= len(digits) <= 13:
                    spans.append(("PHONE", m.start(), m.end(), "regex"))

    if "PERSON" in categories:
        for m in _LABELLED_NAME.finditer(text):
            spans.append(("PERSON", m.start(1), m.end(1), "regex"))
        nlp = _load_nlp()
        if nlp is not None and text.strip():
            for ent in nlp(text).ents:
                if ent.label_ != "PERSON":
                    continue
                # NER can run across line breaks and swallow neighbouring labels; split per line.
                offset = ent.start_char
                for segment in ent.text.split("\n"):
                    if len(segment.split()) >= 2:
                        lead = len(segment) - len(segment.lstrip())
                        spans.append(("PERSON", offset + lead, offset + len(segment.rstrip()), "ner"))
                    offset += len(segment) + 1

    if "LOCATION" in categories:
        nlp = _load_nlp()
        if nlp is not None and text.strip():
            for ent in nlp(text).ents:
                if ent.label_ in {"GPE", "LOC", "FAC"}:
                    spans.append(("LOCATION", ent.start_char, ent.end_char, "ner"))

    for name, pattern in custom.items():
        if name in categories:
            for m in re.finditer(pattern, text):
                if m.end() > m.start():
                    spans.append((name, m.start(), m.end(), "custom"))
    return spans


def _resolve_overlaps(spans: list[tuple[str, int, int, str]], custom_names: list[str]) -> list[tuple[str, int, int, str]]:
    order = {c: i for i, c in enumerate(custom_names + PRIORITY)}
    ranked = sorted(spans, key=lambda s: (order.get(s[0], 99), -(s[2] - s[1]), s[1]))
    chosen: list[tuple[str, int, int, str]] = []
    for s in ranked:
        if all(s[2] <= c[1] or s[1] >= c[2] for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: s[1])


def _rects_for_span(page: PageContent, start: int, end: int) -> list[tuple[float, float, float, float]]:
    """Union word boxes per visual line so one detection yields one rect per line."""
    rects: list[list[float]] = []
    last_key = None
    for word, (ws, we) in zip(page.words, page.offsets):
        if we <= start or ws >= end:
            continue
        if rects and word.line_key == last_key:
            r = rects[-1]
            r[0], r[1] = min(r[0], word.x0), min(r[1], word.y0)
            r[2], r[3] = max(r[2], word.x1), max(r[3], word.y1)
        else:
            rects.append([word.x0, word.y0, word.x1, word.y1])
        last_key = word.line_key
    return [tuple(r) for r in rects]  # type: ignore[misc]


def detect_pii(pages: list[PageContent], settings: Settings, categories: set[str] | None = None) -> list[Detection]:
    cats = set(categories if categories is not None else settings.enabled_categories)
    custom = {k: v for k, v in settings.custom_patterns.items()}
    cats |= set(custom) & (set(categories) if categories is not None else set(custom))
    out: list[Detection] = []
    for page in pages:
        if not page.text.strip():
            continue
        spans = _resolve_overlaps(_raw_spans(page.text, cats, custom), list(custom))
        for cat, s, e, method in spans:
            rects = _rects_for_span(page, s, e)
            if rects:
                out.append(Detection(cat, page.number, s, e, method, rects, value=page.text[s:e]))
    return out


# ---- value sweep: redact every occurrence of an already-selected value -----------------

def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def find_value_occurrences(pages: list[PageContent], values: dict[str, str]) -> list[Detection]:
    """Find all occurrences (case/whitespace-insensitive, across line breaks) of selected values.

    `values` maps normalized value -> category. Over-redaction at word boundaries is accepted.
    """
    found: list[Detection] = []
    for page in pages:
        if not page.words:
            continue
        stream_parts: list[str] = []
        owner: list[int] = []  # char index in stream -> word index
        for wi, w in enumerate(page.words):
            token = normalize(w.text)
            stream_parts.append(token)
            owner.extend([wi] * len(token))
        stream = "".join(stream_parts)
        for norm, cat in values.items():
            if len(norm) < 4:
                continue
            idx = stream.find(norm)
            while idx != -1:
                first, last = owner[idx], owner[idx + len(norm) - 1]
                ws, we = page.offsets[first][0], page.offsets[last][1]
                rects = _rects_for_span(page, ws, we)
                if rects:
                    found.append(Detection(cat, page.number, ws, we, "sweep", rects, value=page.text[ws:we]))
                idx = stream.find(norm, idx + 1)
    return found
