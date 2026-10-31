"""Observability summary: query-row parsing, the shaping of results, the not-configured state, polling and the cache."""

import pytest

from app.services import observability as obs


def _cells(**values: str) -> list[dict[str, str]]:
    return [{"field": k, "value": v} for k, v in values.items()]


def test_rows_to_dicts_reads_each_cell() -> None:
    rows = obs.rows_to_dicts([_cells(node="triage", count="4.0"), _cells(node="report_writer", count="2.0")])
    assert rows == [{"node": "triage", "count": "4.0"}, {"node": "report_writer", "count": "2.0"}]


def test_build_summary_shapes_each_panel() -> None:
    results = {
        "runs": [{"runs": "10.0", "p95": "8200.0"}],
        "case_errors": [{"errors": "1.0"}],
        "nodes": [{"node": "triage", "count": "10.0", "p95": "120.0"}, {"node": "screening", "count": "9.0", "p95": "300.0"}],
        "node_errors": [{"node": "screening", "errors": "1.0"}],
        "error_types": [{"error_type": "ReadTimeout", "count": "1.0"}],
        "recent": [{"@timestamp": "2026-10-31 10:00:00.000", "case_id": "7.0", "attempt": "0.0",
                    "status": "ok", "duration_ms": "5000.0"}],
    }
    out = obs.build_summary(results, 24)
    assert out["enabled"] is True
    assert out["case_runs"] == 10
    assert out["errors"] == 1
    assert out["p95_ms"] == 8200.0
    assert out["nodes"][1] == {"node": "screening", "count": 9, "p95_ms": 300.0, "errors": 1}
    assert out["nodes"][0]["errors"] == 0
    assert out["error_types"] == [{"error_type": "ReadTimeout", "count": 1}]
    assert out["recent"][0]["case_id"] == 7
    assert out["recent"][0]["duration_ms"] == 5000.0


def test_build_summary_handles_empty_results() -> None:
    out = obs.build_summary({}, 168)
    assert out["case_runs"] == 0
    assert out["errors"] == 0
    assert out["p95_ms"] is None
    assert out["nodes"] == []
    assert out["recent"] == []


class _Settings:
    observability_log_group = None
    observability_region = None
    bedrock_region = "ap-south-1"


def test_summary_is_disabled_without_a_log_group(monkeypatch) -> None:
    monkeypatch.setattr(obs, "get_settings", lambda: _Settings())
    assert obs.summary(24) == {"enabled": False}


class _FakeLogs:
    """Stands in for the CloudWatch Logs client: every query completes on the first poll."""

    def __init__(self) -> None:
        self.started: list[str] = []

    def start_query(self, **kwargs):
        self.started.append(kwargs["queryString"])
        return {"queryId": f"q{len(self.started)}"}

    def get_query_results(self, queryId: str):
        return {"status": "Complete", "results": [_cells(runs="1.0", p95="10.0")]}


def test_run_queries_starts_every_panel_and_polls_to_complete() -> None:
    client = _FakeLogs()
    done = obs._run_queries(client, "/neu/api", 0, 10)
    assert set(done) == set(obs.QUERIES)
    assert len(client.started) == len(obs.QUERIES)


class _FailingLogs(_FakeLogs):
    def get_query_results(self, queryId: str):
        return {"status": "Failed", "results": []}


def test_run_queries_raises_on_a_failed_query() -> None:
    with pytest.raises(obs.ObservabilityError):
        obs._run_queries(_FailingLogs(), "/neu/api", 0, 10)


def test_summary_uses_the_cache_within_the_ttl(monkeypatch) -> None:
    class Configured(_Settings):
        observability_log_group = "/neu/api"

    calls = {"n": 0}

    def fake_run(client, group, start, end):
        calls["n"] += 1
        return {name: [] for name in obs.QUERIES}

    monkeypatch.setattr(obs, "get_settings", lambda: Configured())
    monkeypatch.setattr(obs.boto3, "client", lambda *a, **k: object())
    monkeypatch.setattr(obs, "_run_queries", fake_run)
    obs._cache.clear()
    first = obs.summary(24)
    second = obs.summary(24)
    assert first == second
    assert calls["n"] == 1
    obs._cache.clear()
