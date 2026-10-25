"""Live updates (roles plan, group D).

A connected user gets the dashboard and the unread count pushed when something changed. The push goes out at most once
every PUSH_INTERVAL seconds: a burst of changes is one push at the next tick, carrying the latest state. Changes that
keep coming still produce one push every PUSH_INTERVAL seconds.
"""

import json

PUSH_INTERVAL = 3.0  # seconds between pushes to one connection
HEARTBEAT_S = 15.0  # a comment line this often keeps proxies from closing an idle stream
CHECK_S = 0.5  # how often the sender looks at the change version


def should_push(changed: bool, last_push: float, now: float, interval: float = PUSH_INTERVAL) -> bool:
    """A push goes out only when something changed and the last push was at least `interval` seconds ago."""
    return changed and now - last_push >= interval


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
