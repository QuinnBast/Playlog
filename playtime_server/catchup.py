"""Playtime that Steam counted but no agent recorded ("other device / untracked"): play on a device without an agent,
before an agent was installed, or while it wasn't running.

Per account and game, Steam's lifetime total (the highest any device has reported - totals sync across devices, so
they're never added up) grows over time. Growth that tracked sessions don't explain is untracked time. Each device's
first report for an account is the starting point: anything new in it happened before tracking. It's recomputed from scratch, so sessions that arrive late (the Deck after months away)
take back time that was untracked before.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime

log = logging.getLogger(__name__)

# Agents see a session from its first to its last once-a-minute observation, so Steam counts up to ~2 minutes more
# per session; plus Steam rounds totals to whole minutes.
SLACK_MINUTES_PER_SESSION = 2
SLACK_MINUTES = 5


def untracked(snapshots, sessions, account_start: datetime) -> list[tuple[datetime, datetime, int]]:
    """For one account and game: the untracked minutes, as (period_start, period_end, minutes) rows.

    snapshots: (taken_at, playtime_minutes, baseline) from every device. baseline marks a device's first report for
    the account: whatever that shows beyond what's already known happened before tracking, not untracked play.
    sessions: (started_at, ended_at) tracked sessions. account_start: the account's first report from any device.
    """
    snapshots = sorted(snapshots)
    sessions = sorted(sessions, key=lambda s: s[1])
    rows = []
    base = high = 0
    envelope = 0
    previous = account_start
    tracked_seconds, tracked_count, i = 0, 0, 0
    for taken_at, minutes, baseline in snapshots:
        if baseline:
            if minutes > high:
                base += minutes - high
                high = minutes
            previous = max(previous, taken_at)
            continue
        high = max(high, minutes)
        while i < len(sessions) and sessions[i][1] <= taken_at:
            started, ended = sessions[i]
            if started >= account_start:
                tracked_seconds += (ended - started).total_seconds()
                tracked_count += 1
            i += 1
        unexplained = (high - base) - tracked_seconds / 60 - SLACK_MINUTES_PER_SESSION * tracked_count - SLACK_MINUTES
        # Only ever grows: a total briefly ahead of the uploaded sessions isn't taken back by this rule; late
        # sessions are handled by recomputing everything.
        if unexplained > envelope:
            rows.append((previous, taken_at, int(unexplained) - envelope))
            envelope = int(unexplained)
        previous = taken_at
    return [r for r in rows if r[2] > 0]


def recompute(conn) -> int:
    """Rebuilds the untracked_playtime table; returns the number of rows."""
    with conn.transaction():
        account_start = dict(conn.execute(
            "SELECT account_id, min(taken_at) FROM totals_snapshots GROUP BY account_id").fetchall())
        snapshots = defaultdict(list)
        for account_id, app_id, taken_at, minutes, baseline in conn.execute(
                """
                SELECT account_id, app_id, taken_at, playtime_minutes,
                       taken_at = min(taken_at) OVER (PARTITION BY device, account_id)
                FROM totals_snapshots
                """):
            snapshots[account_id, app_id].append((taken_at, minutes, baseline))
        sessions = defaultdict(list)
        for account_id, app_id, started, ended in conn.execute(
                "SELECT account_id, app_id, started_at, ended_at FROM sessions"):
            sessions[account_id, app_id].append((started, ended))
        rows = []
        for (account_id, app_id), snaps in snapshots.items():
            for start, end, minutes in untracked(snaps, sessions.get((account_id, app_id), []),
                                                 account_start[account_id]):
                rows.append((account_id, app_id, start, end, minutes))
        conn.execute("DELETE FROM untracked_playtime")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO untracked_playtime (account_id, app_id, period_start, period_end, minutes) "
                            "VALUES (%s, %s, %s, %s, %s)", rows)
    return len(rows)


class Recomputer:
    """Runs recompute() in the background after uploads (coalesced) and hourly."""

    def __init__(self, pool, stop: threading.Event, every: float = 3600):
        self.pool, self.stop, self.every = pool, stop, every
        self.wanted = threading.Event()

    def request(self):
        self.wanted.set()

    def run_forever(self):
        while not self.stop.is_set():
            self.wanted.wait(self.every)
            self.wanted.clear()
            if self.stop.is_set():
                return
            try:
                with self.pool.connection() as conn:
                    recompute(conn)
            except Exception:
                log.exception("Untracked playtime recompute failed")
