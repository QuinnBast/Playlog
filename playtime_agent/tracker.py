"""Turns once-a-minute observations of "which account is running which game" into sessions.

Pure logic with no I/O, so tests can drive it with a fake clock. Times are unix seconds (UTC).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, asdict

# A longer gap between observations means the device slept (or the agent wasn't running): the session ends at the
# last observation and a new one starts.
MAX_GAP_SECONDS = 180


@dataclass
class Session:
    uuid: str
    account_id: int
    app_id: int
    started_at: int
    ended_at: int

    def to_dict(self) -> dict:
        return asdict(self)


class Tracker:
    def __init__(self, current: Session | None = None, max_gap: int = MAX_GAP_SECONDS):
        self.current = current
        self.max_gap = max_gap

    def observe(self, now: int, account_id: int, app_id: int) -> list[Session]:
        """Records one observation; returns the sessions it finished (at most one)."""
        finished = []
        cur = self.current
        playing = account_id > 0 and app_id > 0
        if cur is not None:
            same = playing and cur.account_id == account_id and cur.app_id == app_id
            in_time = cur.ended_at <= now <= cur.ended_at + self.max_gap
            if same and in_time:
                cur.ended_at = now
                return []
            # Stopped, switched game/account, slept, or the clock jumped: the session ends at its last observation.
            finished.append(cur)
            self.current = None
        if playing:
            self.current = Session(str(uuid.uuid4()), account_id, app_id, now, now)
        return finished
