"""HTTP API the agents upload to, plus /healthz and /metrics."""

from __future__ import annotations

import hmac
import logging
import threading
import time
import uuid as uuid_lib
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from . import catchup, db, names
from .config import Settings, token_hash
from .metrics import PlaytimeCollector

log = logging.getLogger(__name__)

MAX_ACCOUNT_ID = 2**32 - 1
MAX_APP_ID = 2**31 - 1
MAX_SESSION_SECONDS = 7 * 24 * 3600
MAX_CLOCK_AHEAD_SECONDS = 24 * 3600


class SessionIn(BaseModel):
    uuid: str
    account_id: int
    app_id: int
    started_at: int
    ended_at: int


class SyncIn(BaseModel):
    sent_at: int
    sessions: list[SessionIn] = Field(default_factory=list, max_length=1000)
    current: SessionIn | None = None


class TotalIn(BaseModel):
    account_id: int
    app_id: int
    playtime_minutes: int = Field(ge=0)
    last_played: int | None = None


class TotalsIn(BaseModel):
    taken_at: int
    totals: list[TotalIn] = Field(default_factory=list, max_length=100_000)
    games: dict[int, str] = Field(default_factory=dict)


def session_problem(s: SessionIn, now: float) -> str | None:
    """Why a session can't be stored, or None if it's fine."""
    try:
        uuid_lib.UUID(s.uuid)
    except ValueError:
        return "bad uuid"
    if not 0 < s.account_id <= MAX_ACCOUNT_ID or not 0 < s.app_id <= MAX_APP_ID:
        return "bad account or app id"
    if s.ended_at < s.started_at or s.ended_at - s.started_at > MAX_SESSION_SECONDS:
        return "bad duration"
    if s.ended_at > now + MAX_CLOCK_AHEAD_SECONDS:
        return "in the future"
    return None


def create_app(settings: Settings, pool: ConnectionPool | None = None, background: bool = True) -> FastAPI:
    pool = pool or ConnectionPool(settings.database_url, kwargs={"autocommit": True}, min_size=1, max_size=4,
                                  open=False)
    registry = CollectorRegistry()   # collects only when scraped
    registry.register(PlaytimeCollector(pool))
    stop = threading.Event()
    recomputer = catchup.Recomputer(pool, stop)
    names_wanted = threading.Event()

    @asynccontextmanager
    async def lifespan(_):
        pool.open(wait=True)
        with pool.connection() as conn:
            db.migrate(conn)
            db.sync_settings(conn, settings)
        if background and settings.fetch_game_names:
            threading.Thread(target=names.run_forever, args=(pool, stop, names_wanted), daemon=True, name="names").start()
        if background:
            threading.Thread(target=recomputer.run_forever, daemon=True, name="catchup").start()
            recomputer.request()
        yield
        stop.set()
        recomputer.request()
        names_wanted.set()
        pool.close()

    app = FastAPI(title="Steam playtime tracker", lifespan=lifespan, docs_url=None, redoc_url=None)
    bearer = HTTPBearer(auto_error=False)

    def device(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
        if credentials is not None:
            presented = token_hash(credentials.credentials)
            for known, name in settings.device_token_hashes.items():
                if hmac.compare_digest(presented, known):
                    return name
        raise HTTPException(status_code=401, detail="unknown device token")

    @app.post("/api/v1/sync")
    def sync(body: SyncIn, device: str = Depends(device)):
        """Stores finished sessions and the device's current game. Replies with every uuid the agent may forget:
        stored now, stored before, or rejected as invalid (logged; resending wouldn't help)."""
        now = time.time()
        valid = []
        for s in body.sessions:
            problem = session_problem(s, now)
            if problem:
                log.warning("Dropping session from %s (%s): %s", device, problem, s.model_dump())
            else:
                valid.append(s.model_dump())
        current = body.current.model_dump() if body.current and not session_problem(body.current, now) else None
        with pool.connection() as conn:
            db.store_sessions(conn, device, valid, current, datetime.now(timezone.utc))
        if valid:
            log.info("%s uploaded %d sessions", device, len(valid))
            recomputer.request()
            names_wanted.set()
        return {"stored": [s.uuid for s in body.sessions]}

    @app.post("/api/v1/totals")
    def totals(body: TotalsIn, device: str = Depends(device)):
        rows = [t.model_dump() for t in body.totals
                if 0 < t.account_id <= MAX_ACCOUNT_ID and 0 < t.app_id <= MAX_APP_ID]
        game_names = {k: v.strip() for k, v in body.games.items() if 0 < k <= MAX_APP_ID and v.strip()}
        with pool.connection() as conn:
            changed = db.store_totals(conn, device, body.taken_at, rows, game_names)
        log.info("%s sent lifetime totals: %d games, %d changed", device, len(rows), changed)
        if changed:
            recomputer.request()
            names_wanted.set()
        return {"changed": changed}

    @app.get("/healthz")
    def healthz():
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"ok": True}

    @app.get("/metrics")
    def metrics(_: Request):
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    return app


def main():
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(Settings.from_env()), host="0.0.0.0", port=8080, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
