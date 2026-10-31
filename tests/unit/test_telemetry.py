"""Telemetry lines: one JSON object per event, with ids and timings only, never a payload or an error message."""

import json
import logging

import pytest

from app.services.telemetry import emit, timed


def _last_json(caplog) -> dict:
    records = [r for r in caplog.records if r.name == "neu.telemetry"]
    assert records, "no telemetry line was written"
    return json.loads(records[-1].getMessage())


def test_emit_writes_one_json_object(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="neu.telemetry"):
        emit("node", node="triage", case_id=7, status="ok", duration_ms=12)
    line = _last_json(caplog)
    assert line == {"event": "node", "node": "triage", "case_id": 7, "status": "ok", "duration_ms": 12}


def test_timed_success_records_status_and_duration(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="neu.telemetry"), timed("node", node="typology", case_id=3):
        pass
    line = _last_json(caplog)
    assert line["status"] == "ok"
    assert line["node"] == "typology"
    assert isinstance(line["duration_ms"], int)
    assert "error_type" not in line


def test_timed_failure_records_class_only_and_reraises(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="neu.telemetry"), pytest.raises(ValueError), \
            timed("case_run", case_id=9, attempt=1, mode="start"):
        raise ValueError("secret detail: account 12345 name Jane Doe")
    line = _last_json(caplog)
    assert line["status"] == "error"
    assert line["error_type"] == "ValueError"
    serialized = json.dumps(line)
    assert "secret detail" not in serialized
    assert "Jane Doe" not in serialized
