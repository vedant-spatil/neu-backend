"""Batched graph rules (R5 to R7) must find the same groups as the original all-at-once version.

The reference below is the original implementation. R5 hits are compared by their member accounts and transfer ids: a
cycle is emitted from whichever of its accounts comes first, so its timestamp can differ between the two versions.
R6 and R7 hits must match exactly.
"""

import numpy as np
import pandas as pd
import pytest

from app.ingest import graph_rules as gr
from app.ingest.alerts import _usd


def _leg_ref(e: pd.DataFrame, src: str, dst: str, k: str) -> pd.DataFrame:
    return e.rename(columns={"src": src, "dst": dst, "t": f"t{k}", "amt": f"amt{k}", "id": f"id{k}", "lab": f"lab{k}"})


def _two_hop_ref(e: pd.DataFrame) -> pd.DataFrame:
    p = _leg_ref(e, "a", "b", "1").merge(_leg_ref(e, "b", "c", "2"), on="b")
    p = p[(p["t2"] >= p["t1"]) & (p["t2"] <= p["t1"] + gr.WINDOW) & (p["a"] != p["c"])]
    return p.reset_index(drop=True)


def _ref_hits(usd: pd.DataFrame) -> list[tuple]:
    e = gr._edges(usd)
    if e.empty:
        return []
    p = _two_hop_ref(e)
    if p.empty:
        return []
    hits = []
    c3 = p.merge(_leg_ref(e, "c", "a", "3"), on=["c", "a"])
    c3 = c3[(c3["t3"] >= c3["t2"]) & (c3["t3"] <= c3["t1"] + gr.WINDOW)]
    for row in c3.itertuples(index=False):
        ids = (row.id1, row.id2, row.id3)
        hits += [(acct, "R5_cycle", tuple(sorted(ids))) for acct in (row.a, row.b, row.c)]
    q = p.rename(columns={"a": "x", "b": "d", "c": "y", "t1": "t3", "t2": "t4", "id1": "id3", "id2": "id4",
                          "amt1": "amt3", "lab1": "lab3", "lab2": "lab4"})
    m = p.merge(q, left_on=["a", "c"], right_on=["y", "x"])
    m = m[(m["d"] != m["a"]) & (m["d"] != m["b"]) & (m["t3"] >= m["t2"]) & (m["t4"] <= m["t1"] + gr.WINDOW)]
    for row in m.itertuples(index=False):
        ids = (row.id1, row.id2, row.id3, row.id4)
        hits += [(acct, "R5_cycle", tuple(sorted(ids))) for acct in (row.a, row.b, row.c, row.d)]
    for acct, rule, ts, amount, ids, lab in gr._scatter_gather(p):
        hits.append((acct, rule, ts, round(amount, 6), tuple(sorted(ids)), lab))
    for acct, rule, ts, amount, ids, lab in gr._gather_scatter(p):
        hits.append((acct, rule, ts, round(amount, 6), tuple(sorted(ids)), lab))
    return hits


def _new_hits(usd: pd.DataFrame) -> list[tuple]:
    hits = []
    for acct, rule, ts, amount, ids, lab in gr.graph_hits(usd):
        if rule == "R5_cycle":
            hits.append((acct, rule, tuple(sorted(ids))))
        else:
            hits.append((acct, rule, ts, round(amount, 6), tuple(sorted(ids)), lab))
    return hits


def _frame(seed: int, n: int = 500, accounts: int = 18) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frm = rng.integers(0, accounts, n)
    to = (frm + rng.integers(1, accounts, n)) % accounts
    ts = pd.Timestamp("2022-09-01") + pd.to_timedelta(rng.uniform(0, 3 * 24, n), unit="h")
    amt = rng.uniform(100, 5000, n).round(2)
    return pd.DataFrame({
        "ts": pd.Series(ts).dt.floor("min"),
        "from_account": [f"A{i}" for i in frm],
        "to_account": [f"A{i}" for i in to],
        "amount_received": amt,
        "amount_paid": amt,
        "receiving_currency": "US Dollar",
        "payment_currency": "US Dollar",
        "payment_format": "ACH",
        "is_laundering": (rng.random(n) < 0.1).astype(int),
        "txn_id": np.arange(1, n + 1),
    })


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_batched_matches_reference_with_small_batches(seed: int, monkeypatch) -> None:
    usd = _usd(_frame(seed))
    expected = sorted(_ref_hits(usd), key=repr)
    monkeypatch.setattr(gr, "PATH_BATCH", 120)
    monkeypatch.setattr(gr, "EXT_BATCH", 13)
    got = sorted(_new_hits(usd), key=repr)
    assert len(expected) > 0, "the random data should produce graph hits"
    assert got == expected


def test_batched_matches_reference_with_one_batch(monkeypatch) -> None:
    usd = _usd(_frame(4))
    expected = sorted(_ref_hits(usd), key=repr)
    got = sorted(_new_hits(usd), key=repr)
    assert got == expected
