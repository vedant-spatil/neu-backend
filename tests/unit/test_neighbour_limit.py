"""The graph slice keeps only each account's TOP_NEIGHBOURS most frequent counterparties, plus the flagged accounts."""

import pandas as pd

from app.services.sync import TOP_NEIGHBOURS, two_hop_slice


def _transfers(pairs):
    return pd.DataFrame({"from_account": [a for a, _ in pairs], "to_account": [b for _, b in pairs]})


def test_only_the_most_frequent_neighbours_are_kept() -> None:
    # F sends to N0..N11; N_i receives i + 1 transfers from F. The top 10 by count are N2..N11.
    pairs = [("F", f"N{i}") for i in range(12) for _ in range(i + 1)]
    edges, nodes = two_hop_slice(_transfers(pairs), {"F"})
    kept = set(nodes)
    assert TOP_NEIGHBOURS == 10
    assert "F" in kept
    assert {f"N{i}" for i in range(2, 12)} <= kept
    assert "N0" not in kept and "N1" not in kept
    assert all(a in kept and b in kept for a, b in zip(edges["from_account"], edges["to_account"]))


def test_flagged_accounts_are_kept_even_with_few_transfers() -> None:
    edges, nodes = two_hop_slice(_transfers([("F", "G")]), {"F", "H"})
    assert {"F", "G"} <= set(nodes)
