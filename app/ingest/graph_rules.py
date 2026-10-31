"""Graph rules R5 to R7 (cycle, scatter-gather, gather-scatter). Pure pandas, USD edges only.

  R5 cycle            directed cycle of length 3 or 4, every hop inside WINDOW
  R6 scatter-gather   source a sends to >=3 intermediates, >=3 of which send to one sink c, inside WINDOW
  R7 gather-scatter   hub b receives from >=3 sources and sends to >=3 targets, inside WINDOW

Edges are USD rows (same filter as R1 to R4). Nodes with more than DEGREE_CAP in or out edges are skipped to bound the
joins. Each group found is yielded once as (account, rule, ts, amount, txn_ids, is_laundering) for build_alerts().

Memory: two-hop paths a->b->c are never built for the whole graph, because on HI-Small there can be tens of millions.
They are built in batches of at most PATH_BATCH paths:
  - the anchor pass groups paths by source a. R5 and R6 only need the paths that start at a, so every group is complete
    inside its batch;
  - the hub pass groups paths by middle node b. R7 only needs the paths through b, so again every group is complete.
The results are the same as building all paths in one go (tests/unit/test_graph_rules_batched.py checks this).
"""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd

WINDOW = pd.Timedelta(hours=72)
DEGREE_CAP = 500
MIN_INTERMEDIATES = 3
PATH_BATCH = 2_000_000  # most two-hop paths held in memory at once
EXT_BATCH = 20_000  # rows of two-hop paths extended to four-cycles at once

Hit = tuple[str, str, pd.Timestamp, float, list[int], bool]


def _edges(usd: pd.DataFrame) -> pd.DataFrame:
    e = pd.DataFrame(
        {
            "src": usd["from_account"].astype(object).values,
            "dst": usd["to_account"].astype(object).values,
            "t": usd["ts"].values,
            "amt": usd["amount_received"].astype(float).values,
            "id": usd["txn_id"].astype(int).values,
            "lab": usd["is_laundering"].astype(bool).values,
        }
    )
    e = e[e["src"] != e["dst"]]
    out_deg = e.groupby("src")["id"].transform("size")
    in_deg = e.groupby("dst")["id"].transform("size")
    return e[(out_deg <= DEGREE_CAP) & (in_deg <= DEGREE_CAP)].reset_index(drop=True)


def _in_view(e: pd.DataFrame) -> pd.DataFrame:
    """First leg a->b of a path."""
    return e.rename(columns={"src": "a", "dst": "b", "t": "t1", "amt": "amt1", "id": "id1", "lab": "lab1"})[
        ["a", "b", "t1", "amt1", "id1", "lab1"]]


def _out_view(e: pd.DataFrame) -> pd.DataFrame:
    """Second leg b->c of a path."""
    return e.rename(columns={"src": "b", "dst": "c", "t": "t2", "id": "id2", "lab": "lab2"})[
        ["b", "c", "t2", "id2", "lab2"]]


def _back_view(e: pd.DataFrame) -> pd.DataFrame:
    """Closing leg c->a of a three-cycle."""
    return e.rename(columns={"src": "c", "dst": "a", "t": "t3", "id": "id3", "lab": "lab3"})[
        ["c", "a", "t3", "id3", "lab3"]]


def _cd_view(e: pd.DataFrame) -> pd.DataFrame:
    """Third leg c->d of a four-cycle."""
    return e.rename(columns={"src": "c", "dst": "d", "t": "t3", "id": "id3", "lab": "lab3"})[
        ["c", "d", "t3", "id3", "lab3"]]


def _da_view(e: pd.DataFrame) -> pd.DataFrame:
    """Closing leg d->a of a four-cycle."""
    return e.rename(columns={"src": "d", "dst": "a", "t": "t4", "id": "id4", "lab": "lab4"})[
        ["d", "a", "t4", "id4", "lab4"]]


def _join_paths(legs: pd.DataFrame, out: pd.DataFrame) -> pd.DataFrame:
    """Two-hop paths a->b->c, with the window and a != c rules applied."""
    p = legs.merge(out, on="b")
    p = p[(p["t2"] >= p["t1"]) & (p["t2"] <= p["t1"] + WINDOW) & (p["a"] != p["c"])]
    return p.reset_index(drop=True)


def _batches(weights: pd.Series) -> Iterator[list]:
    """Groups the keys so each group's total weight is at most PATH_BATCH. A heavier key gets a group of its own."""
    batch: list = []
    total = 0
    for key, weight in weights.items():
        if batch and total + int(weight) > PATH_BATCH:
            yield batch
            batch, total = [], 0
        batch.append(key)
        total += int(weight)
    if batch:
        yield batch


def _cycle_hits(members: list[str], ids: tuple, ts: pd.Timestamp, amount: float, lab: bool) -> Iterator[Hit]:
    # Every account in the cycle gets the alert (one per account and rule).
    for acct in members:
        yield (acct, "R5_cycle", ts, float(amount), [int(i) for i in ids], bool(lab))


def _three_cycles(p: pd.DataFrame, back: pd.DataFrame, seen: set[tuple]) -> Iterator[Hit]:
    # 3-cycles: a->b->c->a
    c3 = p.merge(back, on=["c", "a"])
    c3 = c3[(c3["t3"] >= c3["t2"]) & (c3["t3"] <= c3["t1"] + WINDOW)]
    for row in c3.itertuples(index=False):
        ids = (row.id1, row.id2, row.id3)
        key = tuple(sorted(ids))
        if key in seen:
            continue
        seen.add(key)
        yield from _cycle_hits([row.a, row.b, row.c], ids, row.t1, row.amt1, row.lab1 or row.lab2 or row.lab3)


def _four_cycles(p: pd.DataFrame, cd: pd.DataFrame, da: pd.DataFrame, seen: set[tuple]) -> Iterator[Hit]:
    # 4-cycles: a->b->c->d->a. Each path a->b->c is extended by c->d and then d->a. The extension runs in slices of
    # EXT_BATCH paths, so the intermediate join stays small.
    for start in range(0, len(p), EXT_BATCH):
        part = p.iloc[start:start + EXT_BATCH]
        m = part.merge(cd, on="c")
        m = m[(m["d"] != m["a"]) & (m["d"] != m["b"]) & (m["t3"] >= m["t2"])]
        m = m.merge(da, on=["d", "a"])
        m = m[(m["t4"] >= m["t3"]) & (m["t4"] <= m["t3"] + WINDOW) & (m["t4"] <= m["t1"] + WINDOW)]
        for row in m.itertuples(index=False):
            ids = (row.id1, row.id2, row.id3, row.id4)
            key = tuple(sorted(ids))
            if key in seen:
                continue
            seen.add(key)
            yield from _cycle_hits([row.a, row.b, row.c, row.d], ids, row.t1, row.amt1,
                                   row.lab1 or row.lab2 or row.lab3 or row.lab4)


def _scatter_gather(p: pd.DataFrame) -> Iterator[Hit]:
    # Source a and sink c with >=3 distinct intermediates on a->b->c paths.
    counts = p.groupby(["a", "c"])["b"].nunique().rename("n").reset_index()
    hot = p.merge(counts[counts["n"] >= MIN_INTERMEDIATES][["a", "c"]], on=["a", "c"])
    for (a, c), legs in hot.groupby(["a", "c"]):
        ids = list(dict.fromkeys(legs[["id1", "id2"]].values.ravel().tolist()))
        lab = bool(legs[["lab1", "lab2"]].values.any())
        ts = legs["t1"].min()
        amount = float(legs["amt1"].sum())
        for acct in (a, c):
            yield (acct, "R6_scatter_gather", ts, amount, [int(i) for i in ids], lab)


def _gather_scatter(p: pd.DataFrame) -> Iterator[Hit]:
    # Hub b with >=3 distinct sources (a) and >=3 distinct targets (c) on its paths.
    stats = p.groupby("b").agg(na=("a", "nunique"), nc=("c", "nunique")).reset_index()
    hubs = stats[(stats["na"] >= MIN_INTERMEDIATES) & (stats["nc"] >= MIN_INTERMEDIATES)][["b"]]
    hot = p.merge(hubs, on="b")
    for hub, legs in hot.groupby("b"):
        ids = list(dict.fromkeys(legs[["id1", "id2"]].values.ravel().tolist()))
        lab = bool(legs[["lab1", "lab2"]].values.any())
        yield (hub, "R7_gather_scatter", legs["t1"].min(), float(legs["amt1"].sum()), [int(i) for i in ids], lab)


def _anchor_pass(e: pd.DataFrame) -> Iterator[Hit]:
    """R5 and R6: batches of source accounts, each with all of its two-hop paths."""
    seen: set[tuple] = set()
    in_v, out_v, back = _in_view(e), _out_view(e), _back_view(e)
    cd, da = _cd_view(e), _da_view(e)
    out_deg = e.groupby("src").size()
    in_v["n"] = in_v["b"].map(out_deg).fillna(0).astype("int64")  # paths each first leg starts
    anchor_w = in_v.groupby("a")["n"].sum()
    for anchors in _batches(anchor_w):
        p = _join_paths(in_v[in_v["a"].isin(anchors)], out_v)
        if p.empty:
            continue
        yield from _scatter_gather(p)
        yield from _three_cycles(p, back, seen)
        yield from _four_cycles(p, cd, da, seen)


def _hub_pass(e: pd.DataFrame) -> Iterator[Hit]:
    """R7: batches of hub accounts, each with all of its two-hop paths."""
    in_v, out_v = _in_view(e), _out_view(e)
    hub_w = (e.groupby("dst").size() * e.groupby("src").size()).dropna()  # in-edges times out-edges, per hub
    for hubs in _batches(hub_w):
        p = _join_paths(in_v[in_v["b"].isin(hubs)], out_v[out_v["b"].isin(hubs)])
        if not p.empty:
            yield from _gather_scatter(p)


def graph_hits(usd: pd.DataFrame) -> Iterator[Hit]:
    """Yield every R5 to R7 group hit from the USD rows."""
    e = _edges(usd)
    print(f"[graph] edges after degree cap={len(e)}", flush=True)
    if e.empty:
        return
    yield from _anchor_pass(e)
    print("[graph] anchor pass done (R5, R6)", flush=True)
    yield from _hub_pass(e)
    print("[graph] hub pass done (R7)", flush=True)
