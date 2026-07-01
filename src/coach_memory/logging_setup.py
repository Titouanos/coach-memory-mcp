"""Structured JSON logging.

Emits one JSON object per log line on stdout so container log collectors
(Docker/journald/Loki) can parse fields without regexes. Any ``extra={...}``
passed to a log call is merged into the JSON payload.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sys

# Attributes present on every LogRecord — anything NOT in here that a caller
# attached via extra={...} gets serialized into the JSON payload. `message` and
# `asctime` are computed by formatters (not stock record attrs), so reserve them
# explicitly; the rest (including taskName on 3.12+) come from the record itself.
_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root handler (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn/starlette have their own handlers; route them through root.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(noisy)
        lg.handlers.clear()
        lg.propagate = True
