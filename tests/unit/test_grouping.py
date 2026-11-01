"""Case grouping: linking accounts by shared counterparties, components, the size cap, membership rules, case score and
party tokens. Pure functions, no database."""

import pandas as pd
import pytest

from app.services import grouping as g


def _transfers(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: (from, to, timestamp)."""
    return pd.DataFrame({
        "from_account": [a for a, _, _ in rows],
        "to_account": [b for _, b, _ in rows],
        "ts": pd.to_datetime([t for _, _, t in rows]),
    })


def test_two_accounts_sharing_two_counterparties_are_linked() -> None:
    df = _transfers([
        ("A", "X", "2022-09-01 10:00"), ("B", "X", "2022-09-01 12:00"),
        ("A", "Y", "2022-09-01 10:00"), ("B", "Y", "2022-09-01 12:00"),
    ])
    assert g.linked_pairs(df, {"A", "B"}) == {("A", "B")}


def test_sharing_one_counterparty_is_not_enough() -> None:
    df = _transfers([("A", "X", "2022-09-01 10:00"), ("B", "X", "2022-09-01 12:00")])
    assert g.linked_pairs(df, {"A", "B"}) == set()


def test_shared_counterparties_outside_the_window_do_not_link() -> None:
    df = _transfers([
        ("A", "X", "2022-09-01 10:00"), ("B", "X", "2022-09-05 10:00"),
        ("A", "Y", "2022-09-01 10:00"), ("B", "Y", "2022-09-05 10:00"),
    ])
    assert g.linked_pairs(df, {"A", "B"}) == set()


def test_only_alerted_accounts_are_considered() -> None:
    df = _transfers([
        ("A", "X", "2022-09-01 10:00"), ("B", "X", "2022-09-01 12:00"),
        ("A", "Y", "2022-09-01 10:00"), ("B", "Y", "2022-09-01 12:00"),
    ])
    assert g.linked_pairs(df, {"A"}) == set()


def test_components_join_linked_accounts_transitively() -> None:
    scores = {"A": 0.9, "B": 0.7, "C": 0.65, "D": 0.1}
    out = g.components(scores, {("A", "B"), ("B", "C")})
    members = [c.accounts for c in out]
    assert ("A", "B", "C") in members
    assert ("D",) in members


def test_a_component_above_the_cap_is_split_in_score_order() -> None:
    scores = {f"A{i:02d}": 1.0 - i / 100 for i in range(5)}
    links = {(a, b) for a in scores for b in scores if a < b}  # all linked: one component of 5
    out = g.components(scores, links, max_accounts=2)
    assert [c.accounts for c in out] == [("A00", "A01"), ("A02", "A03"), ("A04",)]
    assert sum(len(c.accounts) for c in out) == 5


def test_component_score_is_its_best_member() -> None:
    out = g.components({"A": 0.4, "B": 0.8}, {("A", "B")})
    assert out[0].best_score == 0.8


@pytest.mark.parametrize("status, expected", [
    (None, g.NEW_CASE),
    ("open", g.ADD),
    ("failed", g.ADD),
    ("awaiting_review", g.ADD),
    ("approved", g.NEW_CASE),
    ("closed", g.NEW_CASE),
    ("superseded", g.SKIP),
])
def test_membership_rules(status, expected) -> None:
    assert g.decide_membership(status) == expected


def test_case_score_is_the_highest_member_score() -> None:
    assert g.case_score([0.2, 0.9, 0.6]) == 0.9


def test_case_score_needs_a_member() -> None:
    with pytest.raises(ValueError):
        g.case_score([])


def test_tokens_are_stable_and_unique() -> None:
    assert g.assign_tokens(["A", "B"]) == {"A": "PARTY_001", "B": "PARTY_002"}
    with pytest.raises(ValueError):
        g.assign_tokens(["A", "A"])
