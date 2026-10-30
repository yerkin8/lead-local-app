"""Masking for anything that leaves the module as text (logs, events, UI errors)."""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"(?<![\w])\+?\d[\d\s()\-]{8,18}\d(?![\w])"), "phone"),  # phones
    (re.compile(r"(?<![\w])\d{6,}(?![\w])"), "number"),                    # api_id, user ids
    (re.compile(r"\b[0-9a-fA-F]{32}\b"), "hash"),                       # api_hash
    (re.compile(r"\b1[A-Za-z0-9_\-+/=]{60,}"), "session"),              # Telethon string sessions
    (re.compile(r"\b[A-Za-z0-9_\-+/=]{80,}"), "blob"),
]


def mask_digits(value: str | int | None) -> str:
    """12345678 -> 12••••. Used for UI and reports."""
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"{text[:2]}••••" if text else "—"


def scrub(text: object) -> str:
    result = str(text)
    for pattern, _label in _PATTERNS:
        result = pattern.sub(lambda m: (m.group(0)[:2] + "••••"), result)
    return result


class ScrubFilter(logging.Filter):
    PREFIXES = ("LeadEqualMoney.mailing", "telethon")

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith(self.PREFIXES):
            return True
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = scrub(message)
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            # Keep the type, drop library messages that may echo request contents.
            record.exc_text = f"{type(record.exc_info[1]).__name__}: {scrub(record.exc_info[1])}"
            record.exc_info = None
        return True


def get_logger() -> logging.Logger:
    logger = logging.getLogger("LeadEqualMoney.mailing")
    if not any(isinstance(item, ScrubFilter) for item in logger.filters):
        logger.addFilter(ScrubFilter())
    # Library records (telethon.*) reach the app log through the root handlers;
    # logger filters are not inherited by child loggers, handler filters are.
    logging.getLogger("telethon").setLevel(logging.WARNING)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(item, ScrubFilter) for item in handler.filters):
            handler.addFilter(ScrubFilter())
    return logger
