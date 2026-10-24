import pytest

from app.services import sync


@pytest.mark.parametrize("status", sorted(sync.BUSY_STATUSES))
def test_busy_import_without_thread_can_be_reset(status):
    assert sync.can_reset(status, live=False) is None


def test_busy_import_with_live_thread_cannot_be_reset():
    reason = sync.can_reset("loading", live=True)
    assert reason is not None and "still running" in reason


@pytest.mark.parametrize("status", ["uploaded", "rejected", "triaged", "failed"])
def test_finished_import_cannot_be_reset(status):
    reason = sync.can_reset(status, live=False)
    assert reason is not None and status in reason


def test_live_registry_is_cleared_when_the_thread_ends(monkeypatch):
    def boom(*_args):
        raise RuntimeError("stop")

    monkeypatch.setattr(sync, "_run", boom)
    with sync._live_lock:
        sync._live.add(999)
    assert sync.is_live(999)
    with pytest.raises(RuntimeError):
        sync._thread_main(999, "unused.csv", {})  # _run raises; the finally block still clears the registry
    assert not sync.is_live(999)


def test_sync_statuses_match_the_router_busy_set():
    assert sync.BUSY_STATUSES == {"validating", "loading", "triaging"}
