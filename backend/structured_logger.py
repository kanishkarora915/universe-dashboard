"""
Structured JSON logging for production observability.

Why: print statements are unstructured — hard to filter, alert on, or
correlate in log aggregators. Production systems use JSON logs that
Render/Datadog/CloudWatch can parse.

Usage:
    from structured_logger import log

    log.info("trade_opened", trade_id=127, idx="NIFTY", strike=24350,
             entry_price=185.9, source="reversal_zone")
    log.warn("ws_stale", last_tick_age_sec=67, idx="BANKNIFTY")
    log.error("db_write_failed", error=str(e), trade_id=127)

Output (one JSON object per line, parseable):
    {"ts":"2026-05-08T15:30:00.123+05:30","level":"INFO","event":"trade_opened","trade_id":127,...}

Render/Vercel/CloudWatch logs can grep by event:
    grep '"event":"trade_opened"'   → all trade entries
    grep '"level":"ERROR"'          → all errors
    grep '"event":"ws_stale"'       → all websocket staleness alerts

Existing print() statements continue working — this is additive.
Use log.* for NEW code; gradually migrate critical paths.
"""

import json
import sys
import time
from datetime import datetime
from typing import Any, Dict
import pytz

IST = pytz.timezone("Asia/Kolkata")


def _ts_iso() -> str:
    """ISO 8601 timestamp with IST timezone (matches our convention)."""
    return datetime.now(IST).isoformat(timespec="milliseconds")


def _emit(level: str, event: str, **fields: Any) -> None:
    """Emit one JSON line to stdout. Render/log aggregators pick this up.

    Fields are merged into the JSON object. Reserved keys (ts, level, event)
    are always present and not overridable.
    """
    obj: Dict[str, Any] = {
        "ts": _ts_iso(),
        "level": level,
        "event": event,
    }
    # Merge user-supplied fields, but don't allow override of reserved keys
    for k, v in fields.items():
        if k not in obj:
            # Coerce non-JSON-serializable values to str
            try:
                json.dumps(v)
                obj[k] = v
            except (TypeError, ValueError):
                obj[k] = str(v)

    try:
        # Single line, no pretty-printing — log aggregators expect this
        line = json.dumps(obj, ensure_ascii=False, default=str)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception as e:
        # Logger must NEVER crash the application — fall back to plain print
        sys.stderr.write(f"[STRUCTLOG-FAIL] {e}: {obj}\n")


class _Logger:
    """Logger interface — exposes info/warn/error/debug methods."""

    @staticmethod
    def info(event: str, **fields: Any) -> None:
        _emit("INFO", event, **fields)

    @staticmethod
    def warn(event: str, **fields: Any) -> None:
        _emit("WARN", event, **fields)

    @staticmethod
    def error(event: str, **fields: Any) -> None:
        _emit("ERROR", event, **fields)

    @staticmethod
    def debug(event: str, **fields: Any) -> None:
        # Debug logs only in dev (controlled via env var)
        import os
        if os.getenv("DEBUG_LOGS", "false").lower() == "true":
            _emit("DEBUG", event, **fields)


# Module-level singleton — `from structured_logger import log`
log = _Logger()


# ── Performance timer helper ──────────────────────────────────────────────

class timed:
    """Context manager + decorator for timing code blocks.

    Usage 1 (context):
        with timed("compute_chain", idx="NIFTY"):
            chain = compute_chain("NIFTY")

    Usage 2 (decorator):
        @timed("compute_chain")
        def compute_chain(idx):
            ...

    Emits log line with duration_ms after block completes.
    Useful for tracking p99 latencies of hot paths.
    """

    def __init__(self, event: str, **fields: Any):
        self.event = event
        self.fields = fields
        self.start = 0.0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = round((time.time() - self.start) * 1000, 2)
        if exc_type is not None:
            log.error(
                self.event + "_failed",
                duration_ms=duration_ms,
                error=str(exc_val),
                error_type=exc_type.__name__ if exc_type else None,
                **self.fields,
            )
        else:
            log.info(
                self.event + "_done",
                duration_ms=duration_ms,
                **self.fields,
            )
        return False  # don't suppress exceptions

    def __call__(self, fn):
        """Decorator support."""
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            with timed(self.event, **self.fields):
                return fn(*args, **kwargs)

        return wrapper
