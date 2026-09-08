"""The only way to the network (CONTRACTS section 2).

`Http` sets timeouts, retries with backoff on 429 and 5xx, and records the
rate-limit headers a source reports. `FixturePlayer` implements the same
protocol from files on disk, so tests and `SIEVE_FIXTURES=1` never touch the
network.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from sieve.contracts import HttpResponse, RateLimit

USER_AGENT = "sieve/0.1"

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def fixtures_enabled() -> bool:
    return os.environ.get("SIEVE_FIXTURES", "") not in ("", "0", "false", "no")


def fixture_slug(url: str, params: dict[str, str] | None = None) -> str:
    """A stable filename for one request: host and path, non-alphanumerics folded.

    `https://openrouter.ai/api/v1/models` -> `openrouter.ai_api_v1_models`
    """
    trimmed = re.sub(r"^https?://", "", url).split("?")[0].rstrip("/")
    slug = re.sub(r"[^a-z0-9]+", "_", trimmed.lower()).strip("_")
    if params:
        extra = "_".join(f"{k}_{params[k]}" for k in sorted(params))
        slug = f"{slug}__{re.sub(r'[^a-z0-9]+', '_', extra.lower()).strip('_')}"
    return slug


#: The longest a retry will wait for a published reset. Past this a scheduled
#: run should fail and be retried by the timer, not hold the job open.
MAX_RESET_WAIT = 120.0


class Http:
    """Real HTTP. One instance per pull; it remembers the last rate limit seen."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 1.5,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._client = client or httpx.Client(
            timeout=timeout, headers={"user-agent": USER_AGENT}, follow_redirects=True
        )
        self._rate = RateLimit()

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> HttpResponse:
        last: httpx.Response | None = None
        for attempt in range(self.retries + 1):
            response = self._client.get(url, headers=headers, params=params)
            self._record(response.headers)
            if response.status_code not in RETRY_STATUS:
                return self._wrap(url, response)
            last = response
            if attempt < self.retries:
                time.sleep(self._wait_for(attempt))
        assert last is not None
        return self._wrap(url, last)

    def _wait_for(self, attempt: int) -> float:
        """How long to wait before retrying.

        When the server published `X-RateLimit-Reset`, wait until *that*:
        it is the only moment the next request can succeed, and retrying
        three times before it is three more refusals counted against a
        daily budget. Capped, because a reset an hour away should fail the
        run rather than hold a scheduled job open for an hour.
        """
        backoff = self.backoff**attempt
        reset_at = self._rate.reset_at
        if reset_at is None:
            return backoff
        seconds = (reset_at - datetime.now(UTC)).total_seconds()
        if seconds <= 0:
            return backoff
        return min(max(seconds, backoff), MAX_RESET_WAIT)

    def rate_limit(self) -> RateLimit:
        return self._rate

    def close(self) -> None:
        self._client.close()

    # -- internals ------------------------------------------------------ #

    def _record(self, headers: httpx.Headers) -> None:
        def as_int(name: str) -> int | None:
            raw = headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except ValueError:
                return None

        reset = headers.get("x-ratelimit-reset")
        reset_at: datetime | None = None
        if reset:
            try:
                reset_at = datetime.fromtimestamp(float(reset), tz=UTC)
            except (ValueError, OSError):
                reset_at = None
        limit = as_int("x-ratelimit-limit")
        remaining = as_int("x-ratelimit-remaining")
        if limit is not None or remaining is not None or reset_at is not None:
            self._rate = RateLimit(limit=limit, remaining=remaining, reset_at=reset_at)

    @staticmethod
    def _wrap(url: str, response: httpx.Response) -> HttpResponse:
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return HttpResponse(
            url=url,
            status=response.status_code,
            headers={k.lower(): v for k, v in response.headers.items()},
            body=body,
        )


class FixturePlayer:
    """Serves recorded payloads by URL. No socket is ever opened.

    A fixture is `<dir>/<fixture_slug(url)>.json`, holding either the raw body
    or `{"status": 200, "headers": {...}, "body": ...}`.
    """

    def __init__(self, directory: str | Path = "tests/fixtures") -> None:
        self.dir = Path(directory)
        self._rate = RateLimit(limit=1000, remaining=999)
        self.requested: list[str] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.requested.append(url)
        for candidate in (fixture_slug(url, params), fixture_slug(url)):
            file = self.dir / f"{candidate}.json"
            if file.exists():
                return self._read(url, file)
        return HttpResponse(
            url=url,
            status=404,
            headers={},
            body={
                "error": {
                    "code": "no_fixture",
                    "message": f"no fixture {fixture_slug(url)}.json under {self.dir}",
                }
            },
        )

    def rate_limit(self) -> RateLimit:
        return self._rate

    def _read(self, url: str, file: Path) -> HttpResponse:
        payload = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "body" in payload and "status" in payload:
            return HttpResponse(
                url=url,
                status=int(payload["status"]),
                headers={str(k).lower(): str(v) for k, v in payload.get("headers", {}).items()},
                body=payload["body"],
            )
        return HttpResponse(url=url, status=200, headers={}, body=payload)


def client(fixtures_dir: str | Path = "tests/fixtures") -> Http | FixturePlayer:
    """The client the CLI and API hand to sources: a player when SIEVE_FIXTURES is set."""
    return FixturePlayer(fixtures_dir) if fixtures_enabled() else Http()
