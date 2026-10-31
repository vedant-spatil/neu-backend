"""Structured log lines for the case workflow (plan Part 1B). One JSON line per graph node and per case run, on stdout.

Fields are ids, node names, statuses and durations, plus the exception class name for a failure. Payloads, names and
error messages are never logged, so nothing sensitive reaches CloudWatch. The admin observability page reads these
lines through CloudWatch Logs Insights (app.services.observability).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("neu.telemetry")
log.setLevel(logging.INFO)
if not log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_handler)
    log.propagate = False


def emit(event: str, **fields: Any) -> None:
    """Writes one JSON line: the event name followed by the given fields."""
    log.info(json.dumps({"event": event, **fields}, separators=(",", ":"), default=str))


@contextmanager
def timed(event: str, **fields: Any) -> Iterator[None]:
    """Times the block and emits one line when it ends. A failure records status=error and the exception class name
    only, then re-raises."""
    started = time.perf_counter()
    status, error_type = "ok", None
    try:
        yield
    except Exception as exc:
        status, error_type = "error", type(exc).__name__
        raise
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000)
        extra = {"error_type": error_type} if error_type else {}
        emit(event, status=status, duration_ms=duration_ms, **fields, **extra)
