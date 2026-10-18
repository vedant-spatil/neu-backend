"""Unit tests for the model pipeline's pure parts (app/ml/features, metrics, alerts). No torch, no Postgres."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ml import features
from app.ml.alerts import build_alert_rows
from app.ml.metrics import threshold_metrics

RAW_COLUMNS = ["Timestamp", "From Bank", "Account", "To Bank", "Account.1", "Payment Currency",
               "Receiving Currency", "Payment Format", "Amount Paid", "Amount Received", "Is Laundering"]


def _raw_csv(path, rows):
    pd.DataFrame(rows, columns=RAW_COLUMNS).to_csv(path, index=False)
    return path


@pytest.fixture
def raw(tmp_path):
    # Out of time order on purpose, so the stable sort is exercised.
    rows = [
        ["2022/09/01 10:05", "10", "A1", "20", "B1", "US Dollar", "US Dollar", "ACH", 100.0, 100.0, 0],
        ["2022/09/01 09:00", "10", "A2", "30", "C1", "US Dollar", "Euro", "Wire", 900.0, 850.0, 1],
        ["2022/09/02 08:00", "20", "B1", "10", "A1", "Euro", "US Dollar", "ACH", 50.0, 50.0, 0],
        ["2022/09/02 08:00", "30", "C1", "40", "D1", "US Dollar", "US Dollar", "Cheque", 75.0, 75.0, 1],
    ]
    return _raw_csv(tmp_path / "raw.csv", rows)


def test_check_inputs_counts_rows_and_laundering(raw):
    assert features.check_inputs(raw) == {"rows": 4, "laundering": 2}


def test_build_formatted_writes_both_tables_aligned(raw, tmp_path):
    out = tmp_path / "out"
    formatted = features.build_formatted(raw, out)
    fmt = pd.read_csv(formatted)
    emap = pd.read_csv(out / features.EDGE_MAP)

    assert list(fmt["EdgeID"]) == list(emap["EdgeID"]) == [0, 1, 2, 3]
    assert fmt["Timestamp"].is_monotonic_increasing
    # Row i of the edge map is the same transaction as row i of the formatted file.
    assert list(fmt["Is Laundering"]) == list(emap["is_laundering"])
    assert list(emap["from_account"]) == ["10_A2", "10_A1", "20_B1", "30_C1"]
    # raw_row points back to the raw CSV row the edge came from.
    assert list(emap["raw_row"]) == [1, 0, 2, 3]


def test_validate_matches_raw_counts(raw, tmp_path):
    formatted = features.build_formatted(raw, tmp_path / "out")
    stats = features.validate(formatted, features.check_inputs(raw))
    assert stats["rows"] == 4 and stats["laundering"] == 2


def test_validate_rejects_count_mismatch(raw, tmp_path):
    formatted = features.build_formatted(raw, tmp_path / "out")
    with pytest.raises(ValueError, match="formatted rows"):
        features.validate(formatted, {"rows": 99, "laundering": 2})


def test_ibm_split_returns_ordered_cut_points():
    daily = np.array([10, 10, 10, 10, 10, 10, 10, 10, 10, 10])
    i, j = features.ibm_split(daily)
    assert 0 <= i <= j <= len(daily)


def test_threshold_metrics_counts_at_threshold():
    df = pd.DataFrame({"EdgeID": [0, 1, 2, 3], "y": [1, 1, 0, 0], "score": [0.9, 0.3, 0.5, 0.1]})
    m = threshold_metrics(df, 0.4)
    assert (m["tp"], m["fp"], m["fn"], m["flagged"]) == (1, 1, 1, 2)
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    assert m["threshold"] == 0.4


def test_threshold_metrics_no_positives_flagged():
    df = pd.DataFrame({"EdgeID": [0, 1], "y": [1, 0], "score": [0.1, 0.2]})
    m = threshold_metrics(df, 0.4)
    assert m["flagged"] == 0 and m["precision"] == 0.0 and m["recall"] == 0.0


def _edge_map():
    return pd.DataFrame({
        "EdgeID": [0, 1, 2],
        "raw_row": [1, 0, 2],
        "from_account": ["10_A2", "10_A1", "20_B1"],
        "to_account": ["20_B1", "20_B1", "10_A1"],
        "amount_paid": [900.0, 100.0, 50.0],
        "payment_format": ["Wire", "ACH", "ACH"],
        "timestamp": ["2022/09/01 09:00", "2022/09/01 10:05", "2022/09/02 08:00"],
        "is_laundering": [1, 0, 0],
    })


def test_build_alert_rows_only_flagged_edges_with_model_source():
    scores = pd.DataFrame({"EdgeID": [0, 1, 2], "y": [1, 0, 0], "score": [0.8, 0.2, 0.41]})
    rows = build_alert_rows(scores, _edge_map(), "model_h66", 0.40, "val")
    assert [r["raw_json"]["edge_id"] for r in rows] == [0, 2]
    first = rows[0]
    assert first["account_id"] == "10_A2"
    assert first["source"] == "model" and first["model_version"] == "model_h66"
    assert first["rules"] == ["MODEL"]
    assert first["is_laundering"] is True
    assert first["raw_json"]["split"] == "val"


def test_build_alert_rows_empty_when_nothing_flagged():
    scores = pd.DataFrame({"EdgeID": [0], "y": [0], "score": [0.01]})
    assert build_alert_rows(scores, _edge_map(), "model_h66", 0.40, "test") == []


def test_build_alert_rows_fails_on_unmapped_edge():
    scores = pd.DataFrame({"EdgeID": [99], "y": [1], "score": [0.9]})
    with pytest.raises(ValueError, match="missing from edge_map"):
        build_alert_rows(scores, _edge_map(), "model_h66", 0.40, "val")
