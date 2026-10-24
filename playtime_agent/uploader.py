"""Talks to the server. Any network or HTTP error raises UploadError; the caller backs off and retries later."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class UploadError(Exception):
    pass


class Uploader:
    def __init__(self, server_url: str, token: str, timeout: float = 10):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            self.server_url + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise UploadError(f"{path}: HTTP {e.code} {e.read()[:200]!r}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise UploadError(f"{path}: {e}") from e

    def sync(self, sessions: list[dict], current: dict | None, now: int) -> list[str]:
        """Uploads finished sessions plus the one in progress; returns the uuids the server has stored."""
        reply = self._post("/api/v1/sync", {"sent_at": now, "sessions": sessions, "current": current})
        return [u for u in reply.get("stored", []) if isinstance(u, str)]

    def totals(self, totals: list[dict], game_names: dict[int, str], now: int):
        self._post("/api/v1/totals", {"taken_at": now, "totals": totals,
                                      "games": {str(k): v for k, v in game_names.items()}})
