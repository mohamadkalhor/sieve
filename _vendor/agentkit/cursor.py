"""§3.6 -- the opaque list cursor.

``GET /v1/…?limit=&cursor=`` answers ``{"items": […], "next_cursor": …}`` in
``(created, id)`` descending order. The cursor names the last row of the page
*and* the filters the page was built from, signed with a key the app holds, so a
client cannot move the window, cannot guess another app's page, and cannot
reuse a cursor against a query it does not belong to (a cursor used with
different filters is a 400 ``bad_cursor``, §3.6).

The cursor is opaque: it is base64url of a compact JSON object plus an
HMAC-SHA256 over those same bytes, ``<payload>.<mac>``. Nothing in the payload
is a secret, but nothing in it is promised either -- only :func:`decode`'s
return value is API.

The key is per app (and per deployment): a key of at least 16 bytes. Keys are
the app's own secret material; this module never logs, stores or echoes them,
and a wrong key and a tampered cursor are the same answer: 400 ``bad_cursor``.
:func:`key_from_env` is where an app gets that key: ``AIO_CURSOR_KEY``, or a
random per-process key under ``APP_ENV=dev``, and a refusal to start anywhere
else. See §3.10.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
from collections.abc import Mapping
from typing import Any

from agentkit.errors import APIError

__all__ = [
    "encode",
    "decode",
    "key_from_env",
    "MIN_KEY_BYTES",
    "ENV_VAR",
    "APP_ENV_VAR",
    "DEV",
]

logger = logging.getLogger("agentkit.cursor")

# The cursor is not a credential, so the key length is a hygiene rule rather
# than a policy: a shorter key means a guessable MAC.
MIN_KEY_BYTES = 16

# §3.6's key and §3.10's environment: ``APP_ENV`` unset is production, and the
# one value that says otherwise is exactly ``dev``.
ENV_VAR = "AIO_CURSOR_KEY"
APP_ENV_VAR = "APP_ENV"
DEV = "dev"

# The dev fallback, and the warning that goes with it: one key for the process
# (so cursors survive a second call) and one line in the log, not two.
_DEV_KEY: bytes | None = None
_WARNED = False

_CREATED = "c"
_ID = "i"
_FILTERS = "f"

_BAD = "The cursor is not valid for this query."


def key_from_env(environ: Mapping[str, str] | None = None) -> bytes:
    """The cursor key this deployment runs with: ``AIO_CURSOR_KEY``, §3.10.

    The key is read from the environment and nowhere else, so nothing that ships
    contains one: a value shorter than :data:`MIN_KEY_BYTES` raises
    :class:`ValueError` naming the variable (a twelve-byte key is a guessable
    MAC). A blank value counts as unset -- an empty variable is an accident of
    an env file, not a key -- and so does a value that is only spaces, which is
    stripped.

    With the variable unset, §3.10 decides: under exactly ``APP_ENV=dev`` the
    app gets a random 32-byte key for this process and one warning, so a dev
    machine keeps working (cursors do not survive a reload, which is why it
    warns); anywhere else -- unset ``APP_ENV`` is production -- it raises
    :class:`RuntimeError` and the app refuses to start, rather than signing
    cursors with a key that is in the clear in the source.

    ``environ`` defaults to the process environment; a mapping is accepted so a
    caller can answer the question for an env file before it is exported.
    """
    env = os.environ if environ is None else environ
    raw = str(env.get(ENV_VAR) or "").strip()
    if raw:
        key = raw.encode("utf-8")
        if len(key) < MIN_KEY_BYTES:
            raise ValueError(
                f"{ENV_VAR} must be at least {MIN_KEY_BYTES} bytes long; this "
                f"one is {len(key)}"
            )
        return key
    if str(env.get(APP_ENV_VAR) or "").strip() == DEV:
        return _dev_key()
    raise RuntimeError(
        f"{ENV_VAR} is required outside {APP_ENV_VAR}={DEV}: set it, or set "
        f"{APP_ENV_VAR}={DEV} for a development machine"
    )


def _dev_key() -> bytes:
    """The process's own random key under ``APP_ENV=dev``, warned about once."""
    global _DEV_KEY, _WARNED
    if _DEV_KEY is None:
        _DEV_KEY = secrets.token_bytes(32)
    if not _WARNED:
        _WARNED = True
        logger.warning(
            "%s is unset and %s=%s: signing cursors with a random %d-byte key "
            "for this process; they will not survive a restart",
            ENV_VAR,
            APP_ENV_VAR,
            DEV,
            len(_DEV_KEY),
        )
    return _DEV_KEY


def encode(last_created: Any, last_id: Any, filters: dict[str, Any], key: bytes) -> str:
    """A cursor naming the last row of this page and the filters of this query.

    ``last_created`` is whatever the store orders by (an epoch number or an
    ISO timestamp) and ``last_id`` is that row's id; both are carried through
    :func:`decode` unchanged. ``filters`` must be JSON-encodable with string
    keys -- the query's filters, so that a cursor cannot be replayed against a
    different one.
    """
    _check_key(key)
    payload = {_CREATED: last_created, _ID: last_id, _FILTERS: dict(filters)}
    try:
        body = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:  # a cursor is not a place for a NaN
        raise ValueError(f"a cursor only holds JSON values: {exc}") from exc
    mac = hmac.new(key, body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(mac)}"


def decode(cursor: str, filters: dict[str, Any], key: bytes) -> tuple[Any, Any]:
    """``(last_created, last_id)`` of ``cursor``, or 400 ``bad_cursor``.

    Every failure is the same failure: a malformed cursor, a MAC that does not
    match, a payload that is not the shape this module wrote, or filters that
    differ from the query being asked -- a client learns that its cursor is not
    usable here, and nothing else.
    """
    _check_key(key)
    body = _verified(cursor, key)
    try:
        payload = json.loads(body)
        created = payload[_CREATED]
        last_id = payload[_ID]
        carried = payload[_FILTERS]
    except (ValueError, TypeError, KeyError) as exc:
        raise _bad("The cursor does not carry a page position.") from exc
    if not isinstance(carried, dict) or carried != dict(filters):
        raise _bad("The cursor belongs to a different query.")
    return created, last_id


def _verified(cursor: str, key: bytes) -> bytes:
    """The cursor's JSON bytes, once its MAC is proven. 400 otherwise."""
    if not isinstance(cursor, str):
        raise _bad(_BAD)
    payload, _, mac = cursor.partition(".")
    if not payload or not mac or "." in mac:
        raise _bad(_BAD)
    try:
        body = _unb64(payload)
        given = _unb64(mac)
    except (binascii.Error, ValueError) as exc:
        raise _bad(_BAD) from exc
    expected = hmac.new(key, body, hashlib.sha256).digest()
    if not hmac.compare_digest(given, expected):
        raise _bad(_BAD)
    return body


def _check_key(key: bytes) -> None:
    if not isinstance(key, (bytes, bytearray)) or len(key) < MIN_KEY_BYTES:
        raise ValueError(f"the cursor key must be at least {MIN_KEY_BYTES} bytes")


def _bad(message: str) -> APIError:
    return APIError(400, "bad_cursor", message)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)
