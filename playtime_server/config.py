"""Server settings, all from environment variables.

DATABASE_URL         postgresql://user:password@host:5432/db
DEVICE_TOKENS        pc=<token>,deck=<token>        (secret; each agent sends its own as a bearer token)
ACCOUNTS             12345678=Alice,<id>=<name>     (32-bit Steam account IDs -> names on the dashboards)
TZ                   local time zone for days and hours on the dashboards, e.g. Europe/Berlin (default UTC)
GRAFANA_DB_PASSWORD  optional: creates/updates a read-only Postgres role "grafana" with this password
FETCH_GAME_NAMES     "false" to never call the Steam store for game names (default true)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field


def _pairs(text: str, name: str) -> dict[str, str]:
    result = {}
    for item in filter(None, (p.strip() for p in text.split(","))):
        key, sep, value = item.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise ValueError(f"{name}: expected key=value, got {item!r}")
        result[key.strip()] = value.strip()
    return result


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass
class Settings:
    database_url: str
    device_token_hashes: dict[bytes, str] = field(default_factory=dict)   # sha256(token) -> device name
    accounts: dict[int, str] = field(default_factory=dict)
    grafana_db_password: str | None = None
    fetch_game_names: bool = True
    timezone: str = "UTC"

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        tokens = _pairs(env.get("DEVICE_TOKENS", ""), "DEVICE_TOKENS")
        if not tokens:
            raise ValueError("DEVICE_TOKENS is empty: no device could upload")
        if any(len(t) < 16 for t in tokens.values()):
            raise ValueError("DEVICE_TOKENS: use long random tokens (openssl rand -hex 24)")
        return cls(
            database_url=env["DATABASE_URL"],
            device_token_hashes={token_hash(t): device for device, t in tokens.items()},
            accounts={int(k): v for k, v in _pairs(env.get("ACCOUNTS", ""), "ACCOUNTS").items()},
            grafana_db_password=env.get("GRAFANA_DB_PASSWORD") or None,
            fetch_game_names=env.get("FETCH_GAME_NAMES", "true").lower() != "false",
            timezone=env.get("TZ") or "UTC",
        )
