from app.api.routers.projects import CasesToProjectIn, split_accounts


def test_new_accounts_are_added_in_order_and_duplicates_collapse():
    to_add, already = split_accounts(["A2", "A1", "A2", "A3"], existing=set())
    assert to_add == ["A2", "A1", "A3"]
    assert already == []


def test_accounts_already_in_the_project_are_reported_not_added():
    to_add, already = split_accounts(["A1", "A2", "A1"], existing={"A1"})
    assert to_add == ["A2"]
    assert already == ["A1"]


def test_empty_selection_is_refused_by_the_schema():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CasesToProjectIn(case_ids=[])
