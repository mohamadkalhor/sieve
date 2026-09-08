"""The `webhook` target: POST the chain somewhere when it changes.

For a system that does not read an API and does not read a file -- a queue, a
config service, somebody's own deployment script. It pushes; it cannot read
back, and it says so rather than pretending.

**The signature is the point.** A receiver that accepts an unsigned POST will
route traffic for anyone who can reach the URL, so every request carries an
HMAC-SHA256 of the exact bytes sent, and the secret comes from an environment
variable named in the config -- never from the config itself, which lives in git.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from sieve.contracts import Chain, TargetConfig, TargetResult
from sieve.targets.base import planned_ids

#: The header the receiver checks. `sha256=<hex>`, the way GitHub writes it,
#: because a receiver written against that convention already exists everywhere.
SIGNATURE_HEADER = "X-Sieve-Signature"
TIMESTAMP_HEADER = "X-Sieve-Timestamp"

DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.5
DEFAULT_TIMEOUT = 10.0


class WebhookError(RuntimeError):
    """The push failed and the caller should know. Never swallowed."""


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`sha256=<hex>` over `timestamp.body`.

    The timestamp is inside the signed material so a captured request cannot be
    replayed a week later against a receiver that keeps a window.
    """
    material = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def payload(chains: list[Chain]) -> dict[str, Any]:
    """What the receiver is sent. One document, every profile, no partial state."""
    return {
        "source": "sieve",
        "version": 1,
        "chains": [
            {
                "profile": chain.profile,
                "primary": chain.primary,
                "fallbacks": list(chain.fallbacks),
                "local": {k: list(v) for k, v in chain.local.items()},
                "computed_at": chain.computed_at.isoformat(),
            }
            for chain in chains
        ],
    }


class WebhookTarget:
    name = "webhook"

    def plan(self, cfg: TargetConfig, chains: list[Chain]) -> dict[str, list[str]]:
        """Canonical ids: this target writes the chain as the engine computed it."""
        return planned_ids(chains)

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]:
        """Unsupported, honestly.

        A webhook is one-way: nothing here can say what the receiver did with
        the last push, and returning `{}` would let `sieve diff` claim the
        target is empty -- which reads as "everything is a change" rather than
        "unknown". The engine treats a target with no `current()` as undiffable.
        """
        raise NotImplementedError(
            "webhook is one-way: it cannot read back what the receiver holds, so "
            "there is nothing honest to diff against"
        )

    def write(self, cfg: TargetConfig, chains: list[Chain], dry_run: bool) -> TargetResult:
        url = cfg.url
        if not url:
            raise WebhookError(f"target {cfg.name!r}: no `url` configured")

        secret_env = str(cfg.options.get("secret_env") or "")
        if not secret_env:
            raise WebhookError(
                f"target {cfg.name!r}: set `secret_env` to the name of the environment "
                "variable holding the shared secret -- an unsigned webhook lets anyone "
                "who can reach the URL choose your models"
            )
        secret = os.environ.get(secret_env, "")
        if not secret and not dry_run:
            raise WebhookError(
                f"target {cfg.name!r}: {secret_env} is not set, so the push cannot be signed"
            )

        body = json.dumps(payload(chains), separators=(",", ":"), sort_keys=True).encode("utf-8")
        timestamp = str(int(time.time()))

        detail: dict[str, Any] = {
            "url": url,
            "bytes": len(body),
            "chains": len(chains),
            "signed_with": secret_env,
        }

        if dry_run:
            detail["pushed"] = False
            return TargetResult(target=cfg.name, written=[], dry_run=True, detail=detail)

        attempts = int(cfg.options.get("retries", DEFAULT_RETRIES))
        backoff = float(cfg.options.get("backoff", DEFAULT_BACKOFF))
        status, error = self._post(url, body, timestamp, secret, cfg, attempts, backoff)

        detail["status"] = status
        detail["pushed"] = error is None
        if error is not None:
            detail["error"] = error
            raise WebhookError(f"target {cfg.name!r}: {error}")

        return TargetResult(
            target=cfg.name,
            written=[f"{url} ({len(chains)} chain{'s' if len(chains) != 1 else ''})"],
            dry_run=False,
            detail=detail,
        )

    def _post(
        self,
        url: str,
        body: bytes,
        timestamp: str,
        secret: str,
        cfg: TargetConfig,
        attempts: int,
        backoff: float,
    ) -> tuple[int | None, str | None]:
        """POST with backoff. Returns `(status, error)`; error None means it landed.

        A 4xx is not retried: the receiver understood and refused, and sending
        it again changes nothing except the load. 5xx and a dropped connection
        are retried, because those are the receiver having a moment.
        """
        import httpx

        headers = {
            "content-type": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(secret, timestamp, body),
            "user-agent": "sieve/0.1",
        }
        timeout = float(cfg.options.get("timeout", DEFAULT_TIMEOUT))

        last: str | None = None
        status: int | None = None
        for attempt in range(max(1, attempts)):
            try:
                response = httpx.post(url, content=body, headers=headers, timeout=timeout)
                status = response.status_code
                if 200 <= status < 300:
                    return status, None
                last = f"HTTP {status} from {url}"
                if 400 <= status < 500:
                    return status, last + " -- refused, not retried"
            except Exception as exc:  # a dropped connection is worth retrying
                last = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < max(1, attempts):
                time.sleep(backoff * (2**attempt))
        return status, f"{last} (after {max(1, attempts)} attempt(s))"
