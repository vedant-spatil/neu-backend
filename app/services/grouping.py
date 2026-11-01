"""Groups alerted accounts into cases (plan: case grouping). Pure functions: no database, so they are unit-tested directly.

Two accounts are linked when they share at least SHARED_COUNTERPARTIES counterparties, where each shared counterparty
has a transfer from each account within WINDOW of the other. A case is one connected component of linked accounts,
capped at MAX_ACCOUNTS (larger components are split in score order).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import pandas as pd

SHARED_COUNTERPARTIES = 2  # K: counterparties two accounts must share to be linked
MAX_ACCOUNTS = 25  # M: most accounts in one case
WINDOW = pd.Timedelta(hours=72)

ADD = "add"  # the account joins its existing case
NEW_CASE = "new_case"  # a new case is opened for the account
SKIP = "skip"  # nothing changes


@dataclass(frozen=True)
class Component:
    accounts: tuple[str, ...]  # sorted
    best_score: float


def linked_pairs(
    transfers: pd.DataFrame,
    accounts: set[str],
    shared: int = SHARED_COUNTERPARTIES,
    window: pd.Timedelta = WINDOW,
) -> set[tuple[str, str]]:
    """Pairs of accounts (from `accounts`) that share at least `shared` counterparties within `window`.

    `transfers` needs from_account, to_account and ts. A counterparty c links accounts a and b when a and b both have a
    transfer with c and the two transfers are within `window` of each other."""
    legs = pd.concat([
        pd.DataFrame({"acct": transfers["from_account"], "other": transfers["to_account"], "ts": transfers["ts"]}),
        pd.DataFrame({"acct": transfers["to_account"], "other": transfers["from_account"], "ts": transfers["ts"]}),
    ], ignore_index=True)
    legs = legs[legs["acct"].isin(accounts)]
    shared_count: Counter[tuple[str, str]] = Counter()
    for _, group in legs.groupby("other"):
        rows = list(group[["acct", "ts"]].itertuples(index=False, name=None))
        seen: set[tuple[str, str]] = set()
        for i, (a, ta) in enumerate(rows):
            for b, tb in rows[i + 1:]:
                if a == b or abs(ta - tb) > window:
                    continue
                pair = (a, b) if a < b else (b, a)
                if pair not in seen:
                    seen.add(pair)
                    shared_count[pair] += 1
    return {pair for pair, n in shared_count.items() if n >= shared}


def components(
    accounts_scores: dict[str, float],
    links: set[tuple[str, str]],
    max_accounts: int = MAX_ACCOUNTS,
) -> list[Component]:
    """Connected components of the linked alerted accounts, each capped at `max_accounts`. Larger components are split
    into chunks of `max_accounts` in score order, highest first. Accounts with no link are single-account components."""
    parent = {a: a for a in accounts_scores}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in links:
        if a in parent and b in parent:
            parent[find(a)] = find(b)

    groups: dict[str, list[str]] = defaultdict(list)
    for a in accounts_scores:
        groups[find(a)].append(a)

    out: list[Component] = []
    for members in groups.values():
        ordered = sorted(members, key=lambda a: (-accounts_scores[a], a))
        for start in range(0, len(ordered), max_accounts):
            chunk = ordered[start:start + max_accounts]
            out.append(Component(tuple(sorted(chunk)), max(accounts_scores[a] for a in chunk)))
    return sorted(out, key=lambda c: (-c.best_score, c.accounts))


def case_score(member_scores: list[float]) -> float:
    """A case's score is its highest member score."""
    if not member_scores:
        raise ValueError("a case needs at least one member score")
    return max(member_scores)


def decide_membership(active_case_status: str | None) -> str:
    """What happens to an account that has an alert in a group.

    `active_case_status` is the status of the account's active case, or None when it has none. superseded cases are not
    active and are passed as None. open and failed cases take the account (the workflow re-runs). A case awaiting review
    takes it as an addendum, and its SAR is marked stale. Approved and closed cases are never changed."""
    if active_case_status is None:
        return NEW_CASE
    if active_case_status in ("open", "failed", "awaiting_review"):
        return ADD
    if active_case_status in ("approved", "closed"):
        return NEW_CASE
    return SKIP


def assign_tokens(accounts: list[str]) -> dict[str, str]:
    """Stable party tokens for a case's accounts, in the order given: PARTY_001, PARTY_002, ..."""
    if len(set(accounts)) != len(accounts):
        raise ValueError("accounts must be unique")
    return {acct: f"PARTY_{i:03d}" for i, acct in enumerate(accounts, start=1)}
