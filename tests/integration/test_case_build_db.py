"""Case building against a real Postgres (plan: case grouping). These tests TRUNCATE the case, alert and transfer tables, so
they run only against a throwaway database: set CASE_BUILD_TEST_DB=1 and a DATABASE_URL whose database name contains
'test'. Otherwise they are skipped."""

import os
from datetime import datetime

import pytest
from sqlalchemy import func, select, text

from app.db.models import Alert, Case, CaseAccount, CaseAlert, CaseEvent, Transaction
from app.db.session import SessionLocal
from app.services import membership

TS = datetime(2022, 9, 1, 10, 0)

pytestmark = pytest.mark.skipif(
    os.environ.get("CASE_BUILD_TEST_DB") != "1" or "test" not in os.environ.get("DATABASE_URL", ""),
    reason="needs a throwaway Postgres: set CASE_BUILD_TEST_DB=1 and a DATABASE_URL with 'test' in the database name",
)


@pytest.fixture(autouse=True)
def clean_db():
    with SessionLocal() as db:
        db.execute(text("TRUNCATE case_events, case_alerts, case_accounts, cases, alerts, transactions "
                        "RESTART IDENTITY CASCADE"))
        db.commit()
    yield


def _alert(db, account: str, score: float = 0.8, source: str = "rules") -> Alert:
    alert = Alert(account_id=account, amount=100, ts=TS, source_label="HI-Small", risk_score=score,
                  rules=["R2_fan_out"] if source == "rules" else ["MODEL"], raw_json={"txn_ids": [1]}, source=source)
    db.add(alert)
    db.flush()
    return alert


def _linked_network(db) -> None:
    """A and B both send to X and Y within the window, so they share two counterparties and are linked."""
    for src in ("A", "B"):
        for dst in ("X", "Y"):
            db.add(Transaction(ts=TS, from_bank="001", from_account=src, to_bank="002", to_account=dst,
                               amount_received=10, receiving_currency="US Dollar", amount_paid=10,
                               payment_currency="US Dollar", payment_format="ACH", is_laundering=False))


def _case(db, account: str, status: str, score: float = 0.8) -> Case:
    """An existing case for an account, with its link rows, as start_case writes them."""
    alert = _alert(db, account, score)
    case = Case(alert_id=alert.id, account_id=account, status=status, risk_score=score, state={})
    db.add(case)
    db.flush()
    db.add(CaseAccount(case_id=case.id, account_id=account, role="subject", active=True))
    db.add(CaseAlert(case_id=case.id, alert_id=alert.id, active=True))
    db.flush()
    return case


def _count_events(db) -> int:
    return db.scalar(select(func.count()).select_from(CaseEvent))


def test_a_linked_pair_becomes_one_case_and_a_second_build_changes_nothing() -> None:
    with SessionLocal() as db:
        _alert(db, "A")
        _alert(db, "B")
        _linked_network(db)
        db.commit()
    with SessionLocal() as db:
        first = membership.build_cases(db)
    assert first.created == 1
    assert len(first.jobs) == 1
    with SessionLocal() as db:
        assert sorted(db.execute(select(CaseAccount.account_id)).scalars()) == ["A", "B"]
        events = _count_events(db)
    with SessionLocal() as db:
        second = membership.build_cases(db)
    assert (second.created, second.added, second.merged) == (0, 0, 0)
    assert second.jobs == []
    with SessionLocal() as db:
        assert _count_events(db) == events


def test_a_free_account_joins_the_open_case_its_partner_is_in() -> None:
    with SessionLocal() as db:
        _case(db, "A", "failed")
        _alert(db, "B")
        _linked_network(db)
        db.commit()
    with SessionLocal() as db:
        result = membership.build_cases(db)
    assert result.added == 1
    assert result.jobs == []  # a failed case is flagged, not re-run
    with SessionLocal() as db:
        case = db.execute(select(Case).where(Case.account_id == "A")).scalar_one()
        assert case.state.get("members_changed") is True
        assert case.status == "failed"
        assert sorted(db.execute(select(CaseAccount.account_id).where(CaseAccount.case_id == case.id)).scalars()) == ["A", "B"]


def test_an_awaiting_review_case_that_gains_members_marks_its_sar_stale() -> None:
    with SessionLocal() as db:
        _case(db, "A", "awaiting_review")
        _alert(db, "B")
        _linked_network(db)
        db.commit()
    with SessionLocal() as db:
        membership.build_cases(db)
    with SessionLocal() as db:
        case = db.execute(select(Case).where(Case.account_id == "A")).scalar_one()
        assert case.state.get("sar_stale") is True
        assert case.state.get("members_changed") is True


def test_two_linked_cases_merge_into_the_higher_scoring_one() -> None:
    with SessionLocal() as db:
        loser = _case(db, "A", "awaiting_review", score=0.7)
        winner = _case(db, "B", "failed", score=0.9)
        _linked_network(db)
        db.commit()
        loser_id, winner_id = loser.id, winner.id
    with SessionLocal() as db:
        result = membership.build_cases(db)
    assert result.merged == 1
    with SessionLocal() as db:
        assert db.get(Case, loser_id).status == "superseded"
        assert db.get(Case, loser_id).state["merged_into"] == winner_id
        assert db.get(Case, winner_id).state["members_changed"] is True
        moved = db.execute(select(CaseAccount.account_id).where(
            CaseAccount.case_id == winner_id, CaseAccount.active.is_(True))).scalars()
        assert sorted(moved) == ["A", "B"]
        assert db.execute(select(func.count()).select_from(CaseAccount).where(
            CaseAccount.case_id == loser_id)).scalar_one() == 0


def test_a_running_case_defers_the_merge_to_the_next_build() -> None:
    with SessionLocal() as db:
        _case(db, "A", "open", score=0.7)
        _case(db, "B", "awaiting_review", score=0.9)
        _linked_network(db)
        db.commit()
    with SessionLocal() as db:
        result = membership.build_cases(db)
    assert (result.merged, result.deferred) == (0, 1)
    with SessionLocal() as db:
        assert sorted(db.execute(select(Case.status)).scalars()) == ["awaiting_review", "open"]


def test_an_approved_account_stays_out_and_is_recorded_once() -> None:
    with SessionLocal() as db:
        _case(db, "A", "approved")
        _alert(db, "B")
        _linked_network(db)
        db.commit()
    with SessionLocal() as db:
        first = membership.build_cases(db)
    assert first.created == 1
    with SessionLocal() as db:
        new_case = db.execute(select(Case).where(Case.account_id == "B")).scalar_one()
        created = db.execute(select(CaseEvent).where(CaseEvent.case_id == new_case.id,
                                                     CaseEvent.kind == "created")).scalar_one()
        assert created.payload_json["excluded"] == ["A"]
        assert db.execute(select(CaseAccount.account_id).where(CaseAccount.case_id == new_case.id)).scalars().all() == ["B"]
    with SessionLocal() as db:
        second = membership.build_cases(db)
    assert (second.created, second.added) == (0, 0)
