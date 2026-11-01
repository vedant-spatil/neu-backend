"""What one component of linked accounts does when cases are built (services/grouping.plan_component). Pure: no database."""

from app.services import grouping as g


def test_all_free_accounts_open_a_new_case() -> None:
    plan = g.plan_component(("A", "B"), {})
    assert plan.kind == g.PLAN_NEW
    assert plan.accounts == ("A", "B")
    assert plan.excluded == ()


def test_free_accounts_join_the_one_open_case_their_members_have() -> None:
    plan = g.plan_component(("A", "B", "C"), {"A": (7, "open")})
    assert plan.kind == g.PLAN_ADD
    assert plan.case_id == 7
    assert plan.accounts == ("B", "C")


def test_members_already_in_the_case_add_nothing_new() -> None:
    plan = g.plan_component(("A", "B"), {"A": (7, "open"), "B": (7, "open")})
    assert plan.kind == g.PLAN_ADD
    assert plan.case_id == 7
    assert plan.accounts == ()


def test_two_linked_cases_merge_into_the_one_with_the_highest_score() -> None:
    plan = g.plan_component(
        ("A", "B", "C"),
        {"A": (7, "awaiting_review"), "B": (8, "failed")},
        {"A": 0.7, "B": 0.9, "C": 0.1},
    )
    assert plan.kind == g.PLAN_MERGE
    assert plan.case_id == 8
    assert plan.losers == (7,)
    assert plan.accounts == ("C",)


def test_equal_scores_merge_into_the_older_case() -> None:
    plan = g.plan_component(("A", "B"), {"A": (9, "failed"), "B": (4, "failed")}, {"A": 0.8, "B": 0.8})
    assert plan.kind == g.PLAN_MERGE
    assert plan.case_id == 4
    assert plan.losers == (9,)


def test_a_running_case_defers_the_merge_to_the_next_build() -> None:
    plan = g.plan_component(("A", "B"), {"A": (7, "open"), "B": (8, "awaiting_review")}, {"A": 0.9, "B": 0.8})
    assert plan.kind == g.PLAN_DEFER
    assert plan.case_id is None


def test_member_in_an_approved_case_is_excluded_and_the_rest_join_the_open_case() -> None:
    plan = g.plan_component(("A", "B", "C"), {"A": (7, "open"), "B": (9, "approved")})
    assert plan.kind == g.PLAN_ADD
    assert plan.case_id == 7
    assert plan.accounts == ("C",)
    assert plan.excluded == ("B",)


def test_member_in_a_closed_case_is_excluded_from_a_new_case() -> None:
    plan = g.plan_component(("A", "B", "C"), {"B": (9, "closed")})
    assert plan.kind == g.PLAN_NEW
    assert plan.accounts == ("A", "C")
    assert plan.excluded == ("B",)


def test_every_member_in_a_decided_case_is_nothing() -> None:
    plan = g.plan_component(("A", "B"), {"A": (1, "approved"), "B": (2, "closed")})
    assert plan.kind == g.PLAN_NOTHING
    assert plan.excluded == ("A", "B")


def test_failed_and_awaiting_review_cases_take_new_members() -> None:
    failed = g.plan_component(("A", "B"), {"A": (3, "failed")})
    awaiting = g.plan_component(("A", "B"), {"A": (4, "awaiting_review")})
    assert (failed.kind, failed.case_id, failed.accounts) == (g.PLAN_ADD, 3, ("B",))
    assert (awaiting.kind, awaiting.case_id, awaiting.accounts) == (g.PLAN_ADD, 4, ("B",))


def test_accounts_keep_the_order_given() -> None:
    plan = g.plan_component(("C", "A", "B"), {})
    assert plan.accounts == ("C", "A", "B")


def test_a_pending_case_takes_its_linked_accounts() -> None:
    plan = g.plan_component(("A", "B"), {"A": (5, "pending")})
    assert plan.kind == g.PLAN_ADD
    assert plan.case_id == 5
    assert plan.accounts == ("B",)
