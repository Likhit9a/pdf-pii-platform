"""Logging that records IDs, stages, counts and timings only - never document content or PII."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "pii_platform"


def get_logger(log_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "app.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


def attach_file_handler(log_dir: Path) -> None:
    """Add the file handler if the logger was created before the data dir was known."""
    logger = get_logger()
    target = (log_dir / "app.log").resolve()
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler) and Path(h.baseFilename).resolve() == target:
            return
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(target, maxBytes=512_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)


def log_event(doc_id: str, stage: str, outcome: str, **counts: int | float | str) -> None:
    """Structured, content-free event. Values must be numbers or fixed codes only."""
    extra = " ".join(f"{k}={v}" for k, v in counts.items())
    get_logger().info("doc=%s stage=%s outcome=%s %s", doc_id[:8], stage, outcome, extra)
