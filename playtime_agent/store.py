"""The agent's small on-disk state (SQLite): finished sessions waiting for upload, the open session, and a few
timestamps. Only sessions are stored, never individual observations, so it stays tiny even after months offline.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .tracker import Session

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    uuid TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    app_id INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at INTEGER NOT NULL,
    open INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_SCHEMA)

    def close(self):
        self.db.close()

    def open_session(self) -> Session | None:
        row = self.db.execute(
            "SELECT uuid, account_id, app_id, started_at, ended_at FROM sessions WHERE open = 1").fetchone()
        return Session(*row) if row else None

    def save(self, current: Session | None, finished: list[Session]):
        """Saves the tracker's state in one transaction: finished sessions join the upload queue."""
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE open = 1")
            rows = [(s.uuid, s.account_id, s.app_id, s.started_at, s.ended_at, 0) for s in finished]
            if current:
                rows.append((current.uuid, current.account_id, current.app_id,
                             current.started_at, current.ended_at, 1))
            self.db.executemany("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?)", rows)

    def pending(self, limit: int = 500) -> list[Session]:
        rows = self.db.execute(
            "SELECT uuid, account_id, app_id, started_at, ended_at FROM sessions WHERE open = 0 "
            "ORDER BY started_at LIMIT ?", (limit,)).fetchall()
        return [Session(*r) for r in rows]

    def acknowledge(self, uuids: list[str]):
        with self.db:
            self.db.executemany("DELETE FROM sessions WHERE open = 0 AND uuid = ?", [(u,) for u in uuids])

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, value))
