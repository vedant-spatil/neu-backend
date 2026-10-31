"""R4 pass-through: the indexed version must give the same alerts as the original per-account loop."""

from bisect import bisect_right

import numpy as np
import pandas as pd
import pytest

from app.ingest.alerts import H48, _pass_through, _usd


def _reference(usd: pd.DataFrame) -> list[tuple]:
    inbound = usd.sort_values("ts")
    outbound_by_acct = {a: g.sort_values("ts") for a, g in usd.groupby("from_account", observed=True)}
    out = []
    for acct, g in inbound.groupby("to_account", observed=True):
        if acct not in outbound_by_acct:
            continue
        out_g = outbound_by_acct[acct]
        out_times = out_g["ts"].tolist()
        out_cum = out_g["amount_paid"].cumsum().tolist()
        for t0, received, txn_id, lab in zip(g["ts"], g["amount_received"], g["txn_id"], g["is_laundering"]):
            lo = bisect_right(out_times, t0)
            hi = bisect_right(out_times, t0 + H48)
            if hi <= lo:
                continue
            sent = out_cum[hi - 1] - (out_cum[lo - 1] if lo > 0 else 0.0)
            if received > 0 and sent >= 0.8 * received:
                out.append((acct, t0, float(received), int(txn_id), bool(lab)))
    return out


def _frame(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n, accounts = 600, 25
    frm = rng.integers(0, accounts, n)
    to = (frm + rng.integers(1, accounts, n)) % accounts
    ts = pd.Timestamp("2022-09-01") + pd.to_timedelta(rng.uniform(0, 6 * 24, n), unit="h")
    amt = rng.uniform(100, 5000, n).round(2)
    return pd.DataFrame({
        "ts": pd.Series(ts).dt.floor("min"),
        "from_account": [f"A{i}" for i in frm],
        "to_account": [f"A{i}" for i in to],
        "amount_received": amt,
        "amount_paid": (amt * rng.uniform(0.7, 1.0, n)).round(2),
        "receiving_currency": "US Dollar",
        "payment_currency": "US Dollar",
        "payment_format": "ACH",
        "is_laundering": 0,
        "txn_id": np.arange(1, n + 1),
    })


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_pass_through_matches_the_reference_loop(seed: int) -> None:
    usd = _usd(_frame(seed))
    expected = sorted(_reference(usd), key=lambda r: (r[0], r[1], r[3]))
    got = sorted(_pass_through(usd), key=lambda r: (r[0], r[1], r[3]))
    assert len(expected) > 0, "the random data should produce some pass-through hits"
    assert got == expected
