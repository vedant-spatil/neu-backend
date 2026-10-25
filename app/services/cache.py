"""In-process cache for read-heavy responses (roles plan, group E).

Every entry is tagged with the global change version. Any change calls `invalidate()`, which bumps the version and drops
the entries, so a cached value is never served after a change. The live stream reads the same version to decide when
to push. This is correct only with one API worker process; with several, use Redis with the same version scheme.
"""

import threading
from collections.abc import Callable
from typing import Any

_lock = threading.Lock()
_version = 0
_entries: dict[str, tuple[int, Any]] = {}


def version() -> int:
    with _lock:
        return _version


def invalidate() -> None:
    """Call after any change that a dashboard, a list or a notification count can show."""
    global _version
    with _lock:
        _version += 1
        _entries.clear()


def get_or_compute(key: str, compute: Callable[[], Any]) -> Any:
    with _lock:
        hit = _entries.get(key)
        seen = _version
        if hit is not None and hit[0] == seen:
            return hit[1]
    value = compute()
    with _lock:
        # Store only if nothing changed while computing. A change in between means the value may be stale.
        if seen == _version:
            _entries[key] = (seen, value)
    return value
