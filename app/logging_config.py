"""Production logging with consistent formatting and secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any, TextIO

from app.config import environment_value


LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
DEFAULT_LOG_LEVEL = "INFO"

SENSITIVE_PATTERNS = (
    re.compile(
        r"(?i)\b[A-Z0-9_]*(?:APP_SECRET|API_KEY|MODEL_KEY|TABLE_ID)\b"
        r"\s*[:=]\s*[\"']?[^\s,}\"']+[\"']?"
    ),
    re.compile(r"(?i)\bAuthorization\s*[:=]\s*Bearer\s+\S+"),
    re.compile(r"(?i)\btenant_access_token\b\s*[:=]\s*\S+"),
)


def redact_sensitive_data(value: Any) -> str:
    text = str(value or "")
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[敏感配置已隐藏]", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_data(super().format(record))


def _log_level(value: str | None) -> int:
    normalized = str(value or DEFAULT_LOG_LEVEL).strip().upper()
    selected = getattr(logging, normalized, None)
    return selected if isinstance(selected, int) else logging.INFO


def configure_logging(
    level: str | None = None,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure the process root logger once with a safe formatter."""
    selected_level = level or environment_value("LOG_LEVEL", DEFAULT_LOG_LEVEL)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(_log_level(selected_level))
    logging.captureWarnings(True)
    return root_logger
