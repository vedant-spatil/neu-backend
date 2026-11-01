"""The sync's rule-alert upsert decisions (app/ingest/load.plan_upsert). Pure: no database."""

from app.ingest.load import alert_key, plan_upsert


def test_key_is_account_and_first_rule() -> None:
    assert alert_key("A", ["R2_fan_out"]) == ("A", "R2_fan_out")
    assert alert_key("A", []) == ("A", "")


def test_a_produced_key_with_one_row_keeps_its_id() -> None:
    plan = plan_upsert({("A", "R1"): [(10, True)]}, {("A", "R1")})
    assert plan.update == {("A", "R1"): 10}
    assert plan.insert == []
    assert plan.delete == []


def test_a_new_key_is_inserted() -> None:
    plan = plan_upsert({}, {("B", "R3"), ("A", "R1")})
    assert plan.insert == [("A", "R1"), ("B", "R3")]
    assert plan.update == {}


def test_an_unlinked_key_no_longer_produced_is_deleted() -> None:
    plan = plan_upsert({("A", "R1"): [(10, False)]}, set())
    assert plan.delete == [10]


def test_a_linked_key_no_longer_produced_is_kept_and_counted() -> None:
    plan = plan_upsert({("A", "R1"): [(10, True)]}, set())
    assert plan.delete == []
    assert plan.kept_unproduced == 1


def test_duplicate_unlinked_rows_keep_the_lowest_id_and_delete_the_rest() -> None:
    plan = plan_upsert({("A", "R1"): [(10, False), (4, False), (7, False)]}, {("A", "R1")})
    assert plan.update == {("A", "R1"): 4}
    assert plan.delete == [7, 10]


def test_the_linked_row_is_the_one_kept_when_a_duplicate_exists() -> None:
    # The sync used to insert a twin of a linked alert. The linked row must keep its id, and the unlinked twin goes.
    plan = plan_upsert({("A", "R1"): [(4, True), (12, False)]}, {("A", "R1")})
    assert plan.update == {("A", "R1"): 4}
    assert plan.delete == [12]
    assert plan.linked_duplicates == 0


def test_a_second_linked_duplicate_is_kept_and_reported() -> None:
    plan = plan_upsert({("A", "R1"): [(4, True), (9, True)]}, {("A", "R1")})
    assert plan.update == {("A", "R1"): 4}
    assert plan.delete == []
    assert plan.linked_duplicates == 1


def test_a_mixed_sync_is_planned_in_one_pass() -> None:
    existing = {
        ("A", "R1"): [(1, False)],  # produced again: updated
        ("B", "R2"): [(2, True)],  # not produced, linked: kept
        ("C", "R3"): [(3, False)],  # not produced, unlinked: deleted
    }
    plan = plan_upsert(existing, {("A", "R1"), ("D", "R4")})
    assert plan.update == {("A", "R1"): 1}
    assert plan.insert == [("D", "R4")]
    assert plan.delete == [3]
    assert plan.kept_unproduced == 1
