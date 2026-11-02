"""Structured application logs: how the system behaved (timings, errors, retries, Claude calls).

Not the audit log. audit_events records business actions for auditors; these logs are for engineers.

    log = get_logger(__name__)
    with log_context(case_id=case.id):
        log.info("claude.call", model=..., duration_ms=...)

Every line carries the bound context (correlation_id, tenant_id, case_id, ...). Output goes to stderr and to a
rotating JSON-lines file (LOG_FILE, default logs/finrecon.log). Log IDs and counts, never transaction contents,
customer names or prompt text; secret-looking fields are masked regardless.
"""

import json
import logging
import logging.handlers
import re
import sys
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

ROOT_LOGGER = "finrecon"
MAX_FILE_BYTES = 10 * 1024 * 1024
BACKUP_FILES = 5
NOISY_LIBRARIES = ("httpx", "httpcore", "anthropic", "urllib3")

_context: ContextVar[dict[str, Any]] = ContextVar("finrecon_log_context", default={})

# Whole key segments only: "access_token" and "api_key" are secrets, "input_tokens" is a count.
_SECRET_KEY = re.compile(r"(^|[_-])(api[_-]?key|password|passwd|secret|token|authorization|credentials?)$",
                         re.IGNORECASE)
_SECRET_VALUE = re.compile(r"(sk-ant-[A-Za-z0-9_\-]+|Bearer\s+[A-Za-z0-9._\-]+)")
_URL_PASSWORD = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")
MASK = "***"


# --- context ---


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields to every log line inside the block (works across function calls, not across threads)."""
    token = _context.set({**_context.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


def correlation_id() -> str | None:
    value = _context.get().get("correlation_id")
    return str(value) if value is not None else None


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


# --- logger API ---


class EventLogger:
    """log.info("event.name", key=value, ...) -> one structured line."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, fields)

    def exception(self, event: str, **fields: Any) -> None:
        """Logs at ERROR with the current exception's traceback."""
        self._log(logging.ERROR, event, fields, exc_info=True)

    def _log(self, level: int, event: str, fields: dict[str, Any], exc_info: bool = False) -> None:
        if self._logger.isEnabledFor(level):
            self._logger.log(level, event, extra={"fields": fields}, exc_info=exc_info, stacklevel=3)


def get_logger(name: str) -> EventLogger:
    short = name.removeprefix("finrecon_ai.")
    return EventLogger(logging.getLogger(f"{ROOT_LOGGER}.{short}"))


# --- formatting ---


def _record_fields(record: logging.LogRecord) -> dict[str, Any]:
    return redact({**_context.get(), **getattr(record, "fields", {}), **_trace_ids()})


def _trace_ids() -> dict[str, str]:
    """The OpenTelemetry trace and span the line was written in, if telemetry is on (telemetry.py)."""
    from opentelemetry import trace

    span = trace.get_current_span().get_span_context()
    return {"trace_id": format(span.trace_id, "032x"), "span_id": format(span.span_id, "016x")} \
        if span.is_valid else {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            **_record_fields(record),
        }
        if record.exc_info:
            line["error"] = {"type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                             "message": _scrub(str(record.exc_info[1])),
                             "stack": _scrub("".join(traceback.format_exception(*record.exc_info)))}
        return json.dumps(line, default=_json_default, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Readable single line for local terminals."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        fields = " ".join(f"{k}={_json_default(v) if not isinstance(v, str | int | float | bool) else v}"
                          for k, v in _record_fields(record).items())
        text = f"{ts} {record.levelname:<7} {record.name.removeprefix(ROOT_LOGGER + '.'):<22} " \
               f"{record.getMessage()}  {fields}".rstrip()
        if record.exc_info:
            text += "\n" + _scrub("".join(traceback.format_exception(*record.exc_info)))
        return text


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SECRET_KEY.search(key):
        return MASK
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _scrub(value)
    return value


def _scrub(text: str) -> str:
    return _URL_PASSWORD.sub(rf"\g<1>{MASK}@", _SECRET_VALUE.sub(MASK, text))


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal | uuid.UUID | Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return repr(value)


# --- setup ---


def configure_logging(level: str = "INFO", console_format: str = "console", log_file: str | None = None) -> None:
    """Idempotent. The terminal stream uses console_format ("console" or "json"); the file is always JSON lines."""
    root = logging.getLogger(ROOT_LOGGER)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level.upper())
    root.propagate = False

    # stderr keeps the CLI's own stdout output (JSON summaries, reports) clean for piping; Docker captures both.
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(ConsoleFormatter() if console_format == "console" else JsonFormatter())
    root.addHandler(stream)

    if log_file:
        path = Path(log_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_FILE_BYTES,
                                                                backupCount=BACKUP_FILES, encoding="utf-8")
        except OSError as exc:  # e.g. a mounted folder the process may not write: keep running, log to stderr
            get_logger(__name__).warning("log.file_unavailable", path=str(path), error=type(exc).__name__)
        else:
            file_handler.setFormatter(JsonFormatter())
            root.addHandler(file_handler)

    for name in NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
