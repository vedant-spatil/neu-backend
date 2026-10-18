import pandas as pd

from app.ingest.alerts import build_alerts, score_alert


def _df(rows):
    df = pd.DataFrame(
        rows,
        columns=["ts", "from_account", "to_account", "amount_received", "amount_paid",
                 "receiving_currency", "payment_currency", "payment_format", "is_laundering"],
    )
    df["ts"] = pd.to_datetime(df["ts"])
    df["txn_id"] = df.index + 1
    return df


def test_structuring_three_cash_under_10k_in_24h():
    rows = [
        ("2022-09-01 10:00", "A", f"B{i}", 9000, 9000, "US Dollar", "US Dollar", "Cash", 1 if i == 0 else 0)
        for i in range(3)
    ]
    alerts = build_alerts(_df(rows))
    rules = {r for a in alerts for r in a.rules}
    assert "R1_structuring" in rules
    structuring = [a for a in alerts if "R1_structuring" in a.rules][0]
    assert structuring.account_id == "A"
    assert structuring.rule_counts == {"R1_structuring": 1}
    assert structuring.risk_score == score_alert({"R1_structuring": 1})
    assert structuring.is_laundering is True


def test_non_usd_rows_are_ignored_for_rules():
    rows = [
        ("2022-09-01 10:00", "A", f"B{i}", 9000, 9000, "Euro", "Euro", "Cash", 0)
        for i in range(3)
    ]
    assert build_alerts(_df(rows)) == []


def _usd_ach(ts, src, dst, amount=100, lab=0):
    return (ts, src, dst, amount, amount, "US Dollar", "US Dollar", "ACH", lab)


def test_three_cycle_alerts_every_member():
    rows = [
        _usd_ach("2022-09-01 10:00", "A", "B", lab=1),
        _usd_ach("2022-09-01 11:00", "B", "C"),
        _usd_ach("2022-09-01 12:00", "C", "A"),
    ]
    alerts = build_alerts(_df(rows))
    cycle = {a.account_id: a for a in alerts if "R5_cycle" in a.rules}
    assert set(cycle) == {"A", "B", "C"}
    assert cycle["A"].is_laundering is True
    assert cycle["A"].risk_score == score_alert({"R5_cycle": 1})


def test_four_cycle_alerts_every_member():
    rows = [
        _usd_ach("2022-09-01 10:00", "A", "B"),
        _usd_ach("2022-09-01 11:00", "B", "C"),
        _usd_ach("2022-09-01 12:00", "C", "D"),
        _usd_ach("2022-09-01 13:00", "D", "A"),
    ]
    cycle = {a.account_id for a in build_alerts(_df(rows)) if "R5_cycle" in a.rules}
    assert cycle == {"A", "B", "C", "D"}


def test_four_cycle_outside_72h_is_not_alerted():
    rows = [
        _usd_ach("2022-09-01 10:00", "A", "B"),
        _usd_ach("2022-09-01 11:00", "B", "C"),
        _usd_ach("2022-09-01 12:00", "C", "D"),
        _usd_ach("2022-09-05 13:00", "D", "A"),
    ]
    assert not any("R5_cycle" in a.rules for a in build_alerts(_df(rows)))


def test_cycle_outside_72h_is_not_alerted():
    rows = [
        _usd_ach("2022-09-01 10:00", "A", "B"),
        _usd_ach("2022-09-01 11:00", "B", "C"),
        _usd_ach("2022-09-05 12:00", "C", "A"),
    ]
    assert not any("R5_cycle" in a.rules for a in build_alerts(_df(rows)))


def test_scatter_gather_needs_three_intermediates():
    rows = [_usd_ach("2022-09-01 10:00", "A", f"M{i}") for i in range(3)]
    rows += [_usd_ach(f"2022-09-01 1{i + 1}:00", f"M{i}", "T", lab=1) for i in range(3)]
    alerts = build_alerts(_df(rows))
    sg = {a.account_id for a in alerts if "R6_scatter_gather" in a.rules}
    assert sg == {"A", "T"}
    with_two = rows[:2] + rows[3:5]
    assert not any("R6_scatter_gather" in a.rules for a in build_alerts(_df(with_two)))


def test_gather_scatter_hub_has_three_sources_and_three_targets():
    rows = [_usd_ach("2022-09-01 10:00", f"S{i}", "H") for i in range(3)]
    rows += [_usd_ach("2022-09-01 11:00", "H", f"T{i}") for i in range(3)]
    hubs = [a for a in build_alerts(_df(rows)) if "R7_gather_scatter" in a.rules]
    assert [a.account_id for a in hubs] == ["H"]


def test_fan_out_needs_ten_distinct_targets():
    rows = [
        ("2022-09-01 10:00", "A", f"T{i}", 100, 100, "US Dollar", "US Dollar", "ACH", 0)
        for i in range(10)
    ]
    alerts = build_alerts(_df(rows))
    assert any("R2_fan_out" in a.rules for a in alerts)
    fan = [a for a in alerts if a.rules == ["R2_fan_out"]]
    assert len(fan) == 1  # one alert per (account, rule), not per window
    assert fan[0].risk_score == score_alert({"R2_fan_out": fan[0].rule_counts["R2_fan_out"]})
