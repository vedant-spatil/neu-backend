from datetime import date

from app.api.routers.cases import merge_activity


def test_empty_inputs_give_no_days():
    assert merge_activity([], []) == []


def test_cases_and_decisions_are_merged_per_day_oldest_first():
    cases = [(date(2026, 10, 25), 3), (date(2026, 10, 24), 1)]
    decisions = [
        (date(2026, 10, 25), "approve", 2),
        (date(2026, 10, 25), "close", 1),
        (date(2026, 10, 24), "close", 4),
    ]
    assert merge_activity(cases, decisions) == [
        {"day": "2026-10-24", "cases_opened": 1, "approved": 0, "closed": 4},
        {"day": "2026-10-25", "cases_opened": 3, "approved": 2, "closed": 1},
    ]


def test_a_day_with_only_decisions_still_appears():
    assert merge_activity([], [(date(2026, 10, 23), "approve", 1)]) == [
        {"day": "2026-10-23", "cases_opened": 0, "approved": 1, "closed": 0},
    ]
