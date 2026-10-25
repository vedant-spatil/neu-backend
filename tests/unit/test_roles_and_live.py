"""Roles, assignment visibility, live-push timing, the change-version cache and rerun thread ids (roles plan, B to F)."""

from types import SimpleNamespace

from app.services import cache
from app.services.access import can_see_case, case_scope
from app.services.cases import thread_id
from app.services.stream import PUSH_INTERVAL, should_push, sse


def _user(role: str, user_id: int = 1):
    return SimpleNamespace(id=user_id, role=role)


def _case(assignee_id):
    return SimpleNamespace(id=10, assignee_id=assignee_id)


# ---- B: access -------------------------------------------------------------------------------------------------

def test_admin_sees_every_case():
    assert can_see_case(_user("admin"), _case(None))
    assert can_see_case(_user("admin"), _case(7))


def test_qa_sees_only_cases_assigned_to_them():
    assert can_see_case(_user("qa", user_id=7), _case(7))
    assert not can_see_case(_user("qa", user_id=7), _case(8))
    assert not can_see_case(_user("qa", user_id=7), _case(None))


def test_case_scope_is_unrestricted_for_admin_and_filtered_for_qa():
    assert case_scope(_user("admin")) is None
    assert case_scope(_user("qa", user_id=7)) is not None


# ---- D: push timing -------------------------------------------------------------------------------------------

def test_no_push_without_a_change():
    assert not should_push(changed=False, last_push=0.0, now=100.0)


def test_a_change_before_the_interval_waits_until_it_has_passed():
    last = 100.0
    assert not should_push(changed=True, last_push=last, now=last + 0.5)
    assert not should_push(changed=True, last_push=last, now=last + PUSH_INTERVAL - 0.01)
    assert should_push(changed=True, last_push=last, now=last + PUSH_INTERVAL)


def test_a_burst_of_changes_is_one_push_at_the_next_tick():
    # Changes every 0.5 s for 2.9 s: nothing goes out until 3 s after the last push, then one push carries all of them.
    last = 0.0
    pushes = [t / 10 for t in range(31) if should_push(changed=True, last_push=last, now=t / 10)]
    assert pushes == [3.0]


def test_sse_frames_are_well_formed():
    frame = sse("snapshot", {"unread": 2})
    assert frame == 'event: snapshot\ndata: {"unread": 2}\n\n'


# ---- E: cache --------------------------------------------------------------------------------------------------

def test_cache_serves_a_value_until_a_change_invalidates_it():
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return calls["n"]

    cache.invalidate()
    assert cache.get_or_compute("k", compute) == 1
    assert cache.get_or_compute("k", compute) == 1  # cached
    cache.invalidate()
    assert cache.get_or_compute("k", compute) == 2  # after a change, recomputed


def test_cache_version_moves_on_every_change():
    before = cache.version()
    cache.invalidate()
    assert cache.version() == before + 1


# ---- F: rerun ---------------------------------------------------------------------------------------------------

def test_the_first_run_keeps_the_old_thread_id_and_a_rerun_gets_a_new_one():
    assert thread_id(88) == "88"
    assert thread_id(88, 0) == "88"
    assert thread_id(88, 1) == "88:1"
    assert thread_id(88, 1) != thread_id(88, 2)
