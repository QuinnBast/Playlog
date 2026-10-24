"""The agent's main loop: observe Steam, keep the session queue, upload when the server is reachable."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from . import steam
from .store import Store
from .tracker import Tracker
from .uploader import UploadError

log = logging.getLogger(__name__)

TOTALS_EVERY_SECONDS = 24 * 3600
BACKOFF_MIN, BACKOFF_MAX = 60, 1800
BATCH_SIZE = 500
# Non-Steam shortcuts get IDs with the high bit set; only Steam games are tracked.
MAX_STEAM_APP_ID = 2**31 - 1


class Agent:
    def __init__(self, steam_source, store: Store, uploader, steam_root: Path | None = None, clock=time.time):
        self.steam = steam_source
        self.store = store
        self.uploader = uploader
        self.steam_root_override = steam_root
        self.clock = clock
        self.tracker = Tracker(store.open_session())
        self.backoff = 0
        self.next_contact = 0

    def steam_root(self) -> Path | None:
        return self.steam_root_override or self.steam.steam_root()

    def tick(self):
        now = int(self.clock())
        account_id, app_id = self.steam.running()
        if app_id > MAX_STEAM_APP_ID:
            app_id = 0
        before = self.tracker.current
        finished = self.tracker.observe(now, account_id, app_id)
        for s in finished:
            log.info("Session finished: account %s app %s, %d min", s.account_id, s.app_id,
                     (s.ended_at - s.started_at) // 60)
        if finished or self.tracker.current or before:
            self.store.save(self.tracker.current, finished)
        if now >= self.next_contact:
            self.contact_server(now)

    def contact_server(self, now: int):
        try:
            self.upload_sessions(now)
            if self.store.pending(1) or self.tracker.current:
                return  # totals only when every session is finished and uploaded, see upload_totals
            last = int(self.store.get("totals_sent_at") or 0)
            if now - last >= TOTALS_EVERY_SECONDS:
                self.upload_totals(now)
        except UploadError as e:
            self.backoff = min(max(self.backoff * 2, BACKOFF_MIN), BACKOFF_MAX)
            self.next_contact = now + self.backoff
            log.info("Server unreachable (%s); retrying in %d s", e, self.backoff)
        else:
            self.backoff = 0

    def upload_sessions(self, now: int, max_batches: int = 20):
        current = self.tracker.current.to_dict() if self.tracker.current else None
        for _ in range(max_batches):
            batch = self.store.pending(BATCH_SIZE)
            stored = self.uploader.sync([s.to_dict() for s in batch], current, now)
            self.store.acknowledge(stored)
            if batch:
                log.info("Uploaded %d sessions", len(stored))
            if len(batch) < BATCH_SIZE or not stored:
                return

    def upload_totals(self, now: int):
        # Sent only once every finished session is uploaded, so the server never sees a lifetime total grow before
        # the sessions that explain it (it would count the difference as "other device" time).
        root = self.steam_root()
        if root is None:
            return
        totals = steam.lifetime_totals(root)
        self.uploader.totals(totals, steam.installed_game_names(root), now)
        self.store.put("totals_sent_at", str(now))
        log.info("Uploaded lifetime totals (%d games)", len(totals))
