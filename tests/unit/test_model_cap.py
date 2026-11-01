"""Model alerts are per edge: a build keeps the highest-scoring ones per account (services/membership.cap_model_alerts)."""

from datetime import datetime

from app.services.membership import AlertRow, cap_model_alerts

TS = datetime(2022, 9, 1, 10, 0)


def _row(alert_id: int, account: str, score: float, source: str = "model") -> AlertRow:
    return AlertRow(alert_id, account, score, TS, ["MODEL" if source == "model" else "R1"], source)


def test_rule_alerts_are_never_capped() -> None:
    rows = [_row(i, "A", 0.9, source="rules") for i in range(1, 6)]
    kept, left_out = cap_model_alerts(rows, cap=1)
    assert len(kept) == 5
    assert left_out == {}


def test_the_highest_scoring_model_alerts_are_kept_per_account() -> None:
    rows = [_row(1, "A", 0.5), _row(2, "A", 0.9), _row(3, "A", 0.7), _row(4, "B", 0.6)]
    kept, left_out = cap_model_alerts(rows, cap=2)
    assert sorted(r.id for r in kept) == [2, 3, 4]
    assert left_out == {"A": 1}


def test_ties_keep_the_lower_alert_id() -> None:
    rows = [_row(5, "A", 0.8), _row(2, "A", 0.8), _row(9, "A", 0.8)]
    kept, _ = cap_model_alerts(rows, cap=2)
    assert sorted(r.id for r in kept) == [2, 5]


def test_an_account_under_the_cap_loses_nothing() -> None:
    kept, left_out = cap_model_alerts([_row(1, "A", 0.6), _row(2, "A", 0.7)], cap=20)
    assert len(kept) == 2
    assert left_out == {}
