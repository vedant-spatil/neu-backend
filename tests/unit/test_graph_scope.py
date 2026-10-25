import pandas as pd

from app.services.sync import two_hop_slice


def _edges(pairs):
    return pd.DataFrame({"from_account": [a for a, _ in pairs], "to_account": [b for _, b in pairs]})


def test_a_four_cycle_through_the_flagged_account_is_kept_whole():
    # a -> b -> c -> d -> a is a four-account cycle through a. The far pair p -> q touches nothing near a.
    df = _edges([("a", "b"), ("b", "c"), ("c", "d"), ("d", "a"), ("p", "q")])
    edges, nodes = two_hop_slice(df, {"a"})
    kept = set(zip(edges["from_account"], edges["to_account"]))
    assert kept == {("a", "b"), ("b", "c"), ("c", "d"), ("d", "a")}
    assert nodes == ["a", "b", "c", "d"]


def test_transfers_beyond_two_hops_are_dropped():
    # e is three hops from a (a-b, b-c, c-e); the edge c -> e has one end beyond two hops, so it is not loaded.
    df = _edges([("a", "b"), ("b", "c"), ("c", "e")])
    edges, _ = two_hop_slice(df, {"a"})
    kept = set(zip(edges["from_account"], edges["to_account"]))
    assert ("c", "e") not in kept
    assert ("a", "b") in kept and ("b", "c") in kept


def test_no_flagged_accounts_loads_nothing():
    df = _edges([("a", "b")])
    edges, nodes = two_hop_slice(df, set())
    assert len(edges) == 0 and nodes == []
