"""§3.1 and §3.2 -- API keys, and the one credential rule every ``/v1`` route uses.

Two halves of one job:

* :class:`KeyStore` (§3.2) -- keys in SQLite, in the shape
  ``<app>_<keyid>_<secret>`` (press's ``press/auth.py:56``): 12 hex chars of id,
  32 urlsafe characters of secret, the sha256 of the secret stored and the
  secret itself shown exactly once. A key is revoked by one row; a key that is
  unknown, revoked or expired is a key that does not verify.
* :func:`credential` (§3.1) -- the dependency every ``/v1`` route takes, which
  answers the whole table below, in this order, before anything else runs.

| Request carries | Result |
|---|---|
| ``Authorization`` and a Cookie that resolves to a live gate session | 400 ``mixed_credentials`` |
| ``Authorization`` and any other cookie | the cookie is ignored; the bearer rows apply |
| Bearer only, valid, owner active | the key's identity, scopes capped by the owner's current role |
| Bearer only, anything else | 401 ``bad_key`` -- never falls through to a cookie or an owner |
| Cookie only | the app's session path, with the app's own minimum role |
| Neither | 401 ``sign_in``, except the app's public paths |

Three readings of that table are decisions, and each is the stricter one:

1. **Mixed credentials is checked first.** The table's first row is evaluated
   before the bearer is even parsed, so a live cookie beside *any*
   ``Authorization`` header is a 400, valid bearer or not (press asks the same
   question first, ``press/press/system.py:326``). It never grants the cookie's
   identity: a bad bearer is refused, and the refusal says both credentials were
   sent.
2. **A live session below the app's minimum role is 403 ``not_allowed``**, not
   401 ``sign_in``: the caller *is* signed in, and signing in again would not
   help (press answers the same way, ``press/press/app.py:184``). 401
   ``sign_in`` is the row for *no* live session.
3. **"Demoted below the key's need" (401) is an empty cap.** A demotion
   narrows a key -- ``scopes = key scopes ∩ the owner's role scopes`` -- and
   only when that intersection is empty while the key asked for something is
   the key dead (401 ``bad_key``). A key that asked for nothing stays a key
   with no scopes, and ``require()`` is what refuses it.

Three smaller decisions:

* A public path with no credentials at all gets a principal of kind
  ``("key" | "user" | "anonymous")`` -- ``ANONYMOUS``, no owner and no scopes --
  rather than ``None``, so a route can type its parameter as ``Principal`` and
  ``require()`` still refuses it (403). ``anonymous`` is only ever produced on a
  path the app listed in ``public_paths``.
* The sha256 is of the **secret**, not of the whole token (the column is
  ``secret_sha256``): the id picks the row, the constant-time comparison guards
  the secret. The id is not a secret and is not treated as one.
* The app hands over **sync** callables (``owner_status``, ``session_lookup``)
  and the kit's own :class:`GateLookup` is sync too; the dependency is a plain
  ``def``, so FastAPI runs it in its threadpool and blocking on the gate is
  that thread's business, not the event loop's.

Client IP and the failed-bearer limiter (§3.1): the IP is ``CF-Connecting-IP``
when ``X-Gate-Edge`` is present, else the socket address. Failed authentication
is limited per IP: the first nine 401s in a minute are 401s, from the tenth on
a *failing* request from that IP is 429 ``rate_limited`` with ``Retry-After``.
A request with a valid key never touches the limiter -- that is the point of
putting it after verification rather than before it.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote

import httpx
from fastapi import Depends, Request

from agentkit.errors import APIError, MESSAGES

__all__ = [
    "ANONYMOUS",
    "AuthConfig",
    "FailedBearerLimiter",
    "GateLookup",
    "Key",
    "KeyStore",
    "Principal",
    "SCOPE_MARK",
    "Session",
    "bearer_token",
    "clean_scopes",
    "client_ip",
    "credential",
    "parse_key",
    "principal_of",
    "require",
]

# -- the shape of a key (§3.2) -----------------------------------------------

DEFAULT_DAYS = 90
MAX_DAYS = 365
DAY = 86400

KEY_ID_CHARS = 12  # 12 hex chars
SECRET_CHARS = 32  # 32 urlsafe characters
SECRET_BYTES = 24  # 24 bytes -> 32 urlsafe characters, no padding
KEY_ID_RE = re.compile(r"\A[0-9a-f]{12}\Z")
SECRET_RE = re.compile(r"\A[A-Za-z0-9_-]{32}\Z")
# An app's name is part of the token's prefix and is split on ``_``, so it may
# not contain one. Validated where a store or a lookup is built, not at request
# time: a misconfigured app name would make every key of that app unparseable.
APP_NAME_RE = re.compile(r"\A[a-z][a-z0-9-]{0,31}\Z")

# §3.2's scopes, plus the app sub-scope shape ``<scope>:<thing>``
# (``write:telemetry``). A scope is stored as given, lower case, and nothing
# else: a scope that cannot be written down is a typo, not a permission.
SCOPES = frozenset({"read", "write", "run", "admin", "keys"})
SCOPE_RE = re.compile(r"\A[a-z][a-z0-9_]*(?::[a-z0-9][a-z0-9_.-]*)?\Z")
MAX_SCOPE_CHARS = 64
MAX_TEXT_CHARS = 64

# The scope a :func:`require` dependency demands, written on the dependency
# itself so §3.9's parity check can read it off the route's own dependant tree
# (``registry.Tool.scope`` is checked against this, not the other way round).
# Namespaced so that an ordinary function attribute cannot be mistaken for it.
SCOPE_MARK = "__aio_scope__"

# -- the gate lookup (§3.1) ---------------------------------------------------

GATE_APP_HEADER = "X-Gate-App"
GATE_TTL = 30.0  # seconds; "disabling or demoting a user takes effect within 30 s"
GATE_TIMEOUT = 5.0
AUTH_UNAVAILABLE_RETRY_AFTER = 5

# -- the limiter (§3.1) -------------------------------------------------------

EDGE_HEADER = "X-Gate-Edge"
CLIENT_IP_HEADER = "CF-Connecting-IP"
FAILURES_PER_MINUTE = 10
FAILURE_WINDOW = 60.0
# Buckets kept before the expired ones are swept; a process already on the box
# can pick its own bucket, so this only has to stay bounded, not to be exact.
MAX_BUCKETS = 1024

logger = logging.getLogger("agentkit.auth")


def _digest(secret: str) -> str:
    """The stored value of a secret: its sha256, hex."""
    return sha256(secret.encode("utf-8")).hexdigest()


def _text(value: Any, what: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError(f"{what} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{what} must be at most {limit} characters")
    return text


def _app_name(app: Any) -> str:
    name = _text(app, "app", 32)
    if not APP_NAME_RE.match(name):
        raise ValueError(
            f"app must look like {APP_NAME_RE.pattern!r}: a key's prefix is "
            f"split on '_', so an app name may not contain one"
        )
    return name


def clean_scopes(scopes: Iterable[str] | None) -> frozenset[str]:
    """The scopes of a key or a role, validated, as a frozen set.

    ``None`` is "no scopes"; anything that is not a scope of this contract (or
    an app sub-scope of one) raises, because a permission that was silently
    dropped is a permission someone believes they granted.
    """
    out: set[str] = set()
    for scope in scopes or ():
        text = str(scope).strip().lower()
        if len(text) > MAX_SCOPE_CHARS or not SCOPE_RE.match(text):
            raise ValueError(f"not a scope: {text!r}")
        out.add(text)
    return frozenset(out)


def parse_key(raw: Any, app: str) -> tuple[str, str] | None:
    """``(key id, secret)`` for a well-formed key of ``app``, else ``None``.

    The token is split on its first two underscores only: a urlsafe secret may
    contain one, so the third field is everything that is left.
    """
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split("_", 2)
    if len(parts) != 3:
        return None
    prefix, key_id, secret = parts
    if prefix != app or not KEY_ID_RE.match(key_id):
        return None
    if len(secret) != SECRET_CHARS or not SECRET_RE.match(secret):
        return None
    return key_id, secret


def bearer_token(authorization: str | None) -> str | None:
    """The token of a ``Bearer`` header, else ``None``.

    Basic, a foreign scheme, an empty value: ``None``, which the rule turns
    into 401 ``bad_key`` -- never into a fall-through to a cookie.
    """
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    return value.strip() or None


@dataclass(frozen=True)
class Key:
    """One row of ``aio_keys``. ``raw`` is set only on the key ``mint`` returns.

    The secret is not here in any form: a :class:`Key` that came out of
    :meth:`KeyStore.verify` has no copy of it, and :meth:`__repr__` prints
    neither the secret nor a minted token, so a key cannot reach a log line by
    accident.
    """

    id: str
    app: str
    owner: str
    name: str
    scopes: frozenset[str]
    created: int
    expires: int
    last_used: int | None = None
    revoked: bool = False
    raw: str | None = None

    def __repr__(self) -> str:
        return (
            f"Key(id={self.id!r}, app={self.app!r}, owner={self.owner!r}, "
            f"name={self.name!r}, scopes={sorted(self.scopes)!r}, "
            f"revoked={self.revoked!r})"
        )


@dataclass(frozen=True)
class Session:
    """What an app's ``session_lookup`` returns for a live session cookie."""

    id: str
    role: str


@dataclass(frozen=True)
class Principal:
    """Who is calling. ``id`` is the string other apps and audit rows use.

    ``kind`` is ``"key"`` or ``"user"`` for the two rows of §3.1; the third
    value, ``"anonymous"``, is produced only on a path the app listed as
    public, carries no owner and no scopes, and is refused by every
    :func:`require`.
    """

    kind: str
    id: str
    owner: str | None
    scopes: frozenset[str]
    key_id: str | None = None

    def has(self, scope: str) -> bool:
        return str(scope).strip().lower() in self.scopes


ANONYMOUS = Principal(kind="anonymous", id="anon", owner=None, scopes=frozenset())


class KeyStore:
    """§3.2's keys: mint, verify, revoke, list. One SQLite file, one app.

    ``path`` is the app's own file (``":memory:"`` for tests). ``clock`` is
    injectable so a test can move an expiry past without waiting for one.
    """

    def __init__(
        self,
        path: str,
        app: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.app = _app_name(app)
        self.path = str(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._create()

    # -- schema --------------------------------------------------------------

    def _create(self) -> None:
        with self._lock:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS aio_keys (
                    id            TEXT PRIMARY KEY,
                    owner         TEXT NOT NULL,
                    name          TEXT NOT NULL,
                    scopes        TEXT NOT NULL,
                    secret_sha256 TEXT NOT NULL,
                    created       INTEGER NOT NULL,
                    expires       INTEGER NOT NULL,
                    last_used     INTEGER,
                    revoked       INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS aio_keys_owner ON aio_keys (owner)"
            )
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _now(self) -> int:
        return int(self._clock())

    # -- writing -------------------------------------------------------------

    def mint(
        self,
        owner: str,
        name: str,
        scopes: Iterable[str] | None = None,
        days: int = DEFAULT_DAYS,
    ) -> Key:
        """Make a key and return it **once**, with its token in ``raw``.

        The token is never stored and never shown again: what is stored is the
        sha256 of the secret. ``days`` is the life of the key, 1 to 365.
        """
        owner_text = _text(owner, "owner")
        name_text = _text(name, "name")
        scopes_set = clean_scopes(scopes)
        life = int(days)
        if life < 1 or life > MAX_DAYS:
            raise ValueError(f"days must be between 1 and {MAX_DAYS}")
        key_id = secrets.token_hex(KEY_ID_CHARS // 2)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        now = self._now()
        expires = now + life * DAY
        with self._lock:
            self._db.execute(
                "INSERT INTO aio_keys (id, owner, name, scopes, secret_sha256,"
                " created, expires, last_used, revoked) VALUES (?, ?, ?, ?, ?,"
                " ?, ?, NULL, 0)",
                (
                    key_id,
                    owner_text,
                    name_text,
                    json.dumps(sorted(scopes_set)),
                    _digest(secret),
                    now,
                    expires,
                ),
            )
            self._db.commit()
        return Key(
            id=key_id,
            app=self.app,
            owner=owner_text,
            name=name_text,
            scopes=scopes_set,
            created=now,
            expires=expires,
            raw=f"{self.app}_{key_id}_{secret}",
        )

    def revoke(self, key_id: str) -> bool:
        """Revoke one key, now. ``False`` when no such key exists here."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE aio_keys SET revoked = 1 WHERE id = ?", (str(key_id),)
            )
            self._db.commit()
        return bool(cursor.rowcount)

    def touch(self, key_id: str) -> None:
        """Record a use: ``last_used`` is the one column a request writes."""
        with self._lock:
            self._db.execute(
                "UPDATE aio_keys SET last_used = ? WHERE id = ?",
                (self._now(), str(key_id)),
            )
            self._db.commit()

    # -- reading -------------------------------------------------------------

    def verify(self, raw: str | None) -> Key | None:
        """The key a token names, or ``None``: unknown, foreign, revoked, expired.

        The comparison is :func:`hmac.compare_digest` over the sha256 of the
        secret. A key that does not verify leaves no trace -- ``last_used``
        moves only for a key that did.
        """
        parsed = parse_key(raw, self.app)
        if parsed is None:
            return None
        key_id, secret = parsed
        digest = _digest(secret)
        with self._lock:
            row = self._db.execute(
                "SELECT id, owner, name, scopes, secret_sha256, created,"
                " expires, last_used, revoked FROM aio_keys WHERE id = ?",
                (key_id,),
            ).fetchone()
            if row is None:
                return None
            if not hmac.compare_digest(digest, str(row["secret_sha256"])):
                return None
            now = self._now()
            if bool(row["revoked"]) or int(row["expires"]) <= now:
                return None
            self._db.execute(
                "UPDATE aio_keys SET last_used = ? WHERE id = ?", (now, key_id)
            )
            self._db.commit()
        return self._key(row)

    def get(self, key_id: str) -> Key | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, owner, name, scopes, secret_sha256, created,"
                " expires, last_used, revoked FROM aio_keys WHERE id = ?",
                (str(key_id),),
            ).fetchone()
        return self._key(row) if row is not None else None

    def list(self, owner: str) -> list[Key]:
        """Every key of ``owner``, newest first, revoked ones included.

        A Keys page has to show the revoked ones: that is how a person knows
        which key they already turned off.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT id, owner, name, scopes, secret_sha256, created,"
                " expires, last_used, revoked FROM aio_keys WHERE owner = ?"
                " ORDER BY created DESC, id DESC",
                (str(owner),),
            ).fetchall()
        return [self._key(row) for row in rows]

    def _key(self, row: Any) -> Key:
        try:
            scopes = json.loads(row["scopes"])
        except (TypeError, ValueError):
            scopes = []
        return Key(
            id=str(row["id"]),
            app=self.app,
            owner=str(row["owner"]),
            name=str(row["name"]),
            scopes=clean_scopes(scopes if isinstance(scopes, list) else []),
            created=int(row["created"]),
            expires=int(row["expires"]),
            last_used=None if row["last_used"] is None else int(row["last_used"]),
            revoked=bool(row["revoked"]),
        )


class GateLookup:
    """``owner_status(owner_id)`` answered by the app's gate (§3.1, card G1).

    ``GET {base}/v1/user/{id}`` with ``X-Gate-App: <app>`` answers
    ``{"status": …, "role": …}``: ``active`` (plus the role) or anything else,
    which is read as ``gone``. Answers are cached for ``ttl`` seconds
    (30 by default, negative answers included) -- that cache is what makes
    "disabling or demoting a user takes effect within 30 s" true.

    Fail closed: a gate that cannot be reached, a 5xx, a body that is not the
    answer, or a 4xx that is not a 404 all raise
    ``APIError(503, "auth_unavailable")`` with ``Retry-After: 5`` -- a key is
    never accepted because the gate was silent. A 404 is different: the gate
    answered, and the user does not exist, so the key's owner is ``gone``.
    """

    def __init__(
        self,
        base_url: str,
        app_name: str,
        *,
        ttl: float = GATE_TTL,
        timeout: float = GATE_TIMEOUT,
        client: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.app_name = _app_name(app_name)
        self.ttl = float(ttl)
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, tuple[str, str | None]]] = {}
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def __call__(self, owner_id: str) -> tuple[str, str | None]:
        owner = str(owner_id).strip()
        cached = self._cached(owner)
        if cached is not None:
            return cached
        answer = self._ask(owner)
        with self._lock:
            self._cache[owner] = (self._clock() + self.ttl, answer)
        return answer

    def forget(self, owner_id: str | None = None) -> None:
        """Drop one cached answer, or all of them."""
        with self._lock:
            if owner_id is None:
                self._cache.clear()
            else:
                self._cache.pop(str(owner_id).strip(), None)

    @property
    def url(self) -> str:
        return f"{self.base_url}/v1/user/"

    def _cached(self, owner: str) -> tuple[str, str | None] | None:
        with self._lock:
            hit = self._cache.get(owner)
        if hit is None or self._clock() >= hit[0]:
            return None
        return hit[1]

    def _ask(self, owner: str) -> tuple[str, str | None]:
        url = f"{self.url}{quote(owner, safe='')}"
        try:
            response = self._client.get(
                url, headers={GATE_APP_HEADER: self.app_name}
            )
        except (httpx.HTTPError, OSError) as exc:
            raise self._down(f"the gate could not be reached: {exc!r}") from exc
        status = int(getattr(response, "status_code", 0))
        if status == 404:
            return ("gone", None)
        if status != 200:
            raise self._down(f"the gate answered {status} for a user lookup")
        try:
            body = response.json()
        except ValueError as exc:
            raise self._down("the gate's answer was not JSON") from exc
        if not isinstance(body, dict):
            raise self._down("the gate's answer was not an object")
        role = body.get("role")
        role_text = str(role) if role else None
        state = str(body.get("status") or "").strip().lower()
        return ("active" if state == "active" else "gone", role_text)

    def _down(self, why: str) -> APIError:
        # The caller is told the kit cannot check a key, not why: the reason
        # goes to the log, where the operator is.
        logger.warning("gate lookup failed: %s", why)
        return APIError(
            503,
            "auth_unavailable",
            MESSAGES["auth_unavailable"],
            retry_after=AUTH_UNAVAILABLE_RETRY_AFTER,
        )


class FailedBearerLimiter:
    """Failed authentication per client IP: ten a minute, then 429 (§3.1).

    A sliding window, in memory, one worker's worth (the apps run one). Only
    requests that failed authentication are recorded, and the limiter is
    consulted only after a failure -- so a valid key is never rate limited,
    whatever its IP did a moment ago.
    """

    def __init__(
        self,
        per_minute: int = FAILURES_PER_MINUTE,
        window: float = FAILURE_WINDOW,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if int(per_minute) < 1:
            raise ValueError("per_minute must be at least 1")
        self.per_minute = int(per_minute)
        self.window = float(window)
        self._clock = clock
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def record(self, ip: str) -> int:
        """Note one failure from ``ip``; return how many are in the window."""
        now = self._clock()
        bucket = str(ip or "unknown")
        with self._lock:
            hits = [hit for hit in self._hits.get(bucket, ()) if now - hit < self.window]
            hits.append(now)
            self._hits[bucket] = hits
            if len(self._hits) > MAX_BUCKETS:
                self._sweep(now)
            return len(hits)

    def count(self, ip: str) -> int:
        now = self._clock()
        with self._lock:
            hits = self._hits.get(str(ip or "unknown"), ())
            return len([hit for hit in hits if now - hit < self.window])

    def blocked(self, ip: str) -> bool:
        return self.count(ip) >= self.per_minute

    def retry_after(self, ip: str) -> int:
        """Seconds until this IP's oldest failure leaves the window, at least 1."""
        now = self._clock()
        with self._lock:
            hits = [
                hit
                for hit in self._hits.get(str(ip or "unknown"), ())
                if now - hit < self.window
            ]
        if not hits:
            return 1
        return max(1, int(self.window - (now - min(hits))) + 1)

    def forget(self, ip: str | None = None) -> None:
        with self._lock:
            if ip is None:
                self._hits.clear()
            else:
                self._hits.pop(str(ip or "unknown"), None)

    def _sweep(self, now: float) -> None:
        """Called with the lock held: drop the buckets that are all stale."""
        for bucket in list(self._hits):
            if not [hit for hit in self._hits[bucket] if now - hit < self.window]:
                del self._hits[bucket]


def client_ip(request: Any) -> str:
    """§3.1's client IP: ``CF-Connecting-IP`` behind the edge, else the socket.

    ``X-Gate-Edge`` is the edge's own header, and it is *presence* that counts:
    an edge request without ``CF-Connecting-IP`` falls back to the socket
    address rather than sharing one bucket with every other edge request.
    """
    headers = request.headers
    if EDGE_HEADER in headers:
        forwarded = (headers.get(CLIENT_IP_HEADER) or "").strip()
        if forwarded:
            return forwarded
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return str(host) if host else "unknown"


@dataclass
class AuthConfig:
    """Everything the credential rule needs from one app.

    ``owner_status`` is the app's own answer (§3.1): ``(owner_id) -> (state,
    role)`` with state ``"active"`` or ``"gone"`` -- :class:`GateLookup` is the
    kit's, and an app with no gate passes its own. ``role_scopes`` is what each
    of the app's roles may do: the cap on a key's scopes and the scopes of a
    session. ``session_lookup`` is the app's existing session call, handed the
    ``Cookie`` header, answering a :class:`Session` or ``None``.
    """

    app: str
    store: KeyStore
    owner_status: Callable[[str], tuple[str, str | None]]
    role_scopes: Mapping[str, Iterable[str]]
    session_lookup: Callable[[str | None], Session | None]
    public_paths: Iterable[str] = ()
    min_role: str = "member"
    roles: Sequence[str] = ("viewer", "member", "owner")
    limiter: FailedBearerLimiter = field(default_factory=FailedBearerLimiter)
    _dependency: Callable[..., Principal] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self.app = _app_name(self.app)
        if self.store.app != self.app:
            raise ValueError(
                f"this store belongs to {self.store.app!r}, not {self.app!r}"
            )
        if not callable(self.owner_status) or not callable(self.session_lookup):
            raise TypeError("owner_status and session_lookup must be callable")
        self.roles = tuple(str(role) for role in self.roles)
        if not self.roles:
            raise ValueError("roles must not be empty")
        self.role_scopes = {
            str(role): clean_scopes(scopes)
            for role, scopes in dict(self.role_scopes).items()
        }
        self.min_role = str(self.min_role)
        if self.min_role not in self.roles:
            raise ValueError(f"min_role {self.min_role!r} is not one of {self.roles}")
        self.public_paths = tuple(str(path) for path in self.public_paths)

    def rank(self, role: str | None) -> int:
        """Where a role sits, lowest first; an unknown role sits below all of them."""
        try:
            return self.roles.index(str(role))
        except ValueError:
            return -1

    @property
    def dependency(self) -> Callable[..., Principal]:
        """The one dependency of this config, built once.

        Built once on purpose: FastAPI caches a dependency by the identity of
        the callable, so ``Depends(credential(cfg))`` and
        ``Depends(require("write", cfg))`` inside one request resolve to the
        same object and the rule runs one time.
        """
        if self._dependency is None:
            self._dependency = _make_credential(self)
        return self._dependency


def credential(app_cfg: AuthConfig) -> Callable[..., Principal]:
    """The one dependency every ``/v1`` route takes (§3.1).

    Use it as ``principal: Principal = Depends(credential(app_cfg))``. It also
    leaves the principal on ``request.state.principal``, so :func:`require` and
    an audit hook can read it without asking for it again.
    """
    return app_cfg.dependency


def principal_of(request: Any) -> Principal | None:
    """The principal the credential rule already resolved for this request."""
    return getattr(request.state, "principal", None)


def require(scope: str, app_cfg: AuthConfig | None = None) -> Callable[..., Principal]:
    """403 ``not_allowed`` unless the principal holds ``scope``.

    ``Depends(require("write", app_cfg))`` is the form that cannot go wrong: it
    depends on the same credential dependency itself, so the rule has already
    run whatever the route's parameter order is. ``require("write")`` on its own
    reads ``request.state.principal``, and is only for a route that already
    depends on ``credential(app_cfg)`` -- FastAPI may resolve the two in either
    order, so the form with the config is the one to prefer.

    The dependency that comes back carries the scope in ``__aio_scope__``
    (:data:`SCOPE_MARK`), which is how §3.9's parity check reads a route's
    scope off the route itself instead of trusting the registry that names it.
    """
    wanted = str(scope).strip().lower()
    if not wanted:
        raise ValueError("scope must not be empty")

    if app_cfg is None:

        def dependency(request: Request) -> Principal:
            principal = principal_of(request)
            if principal is None:
                raise APIError(401, "sign_in", MESSAGES["sign_in"])
            return _demand(principal, wanted)

        setattr(dependency, SCOPE_MARK, wanted)
        return dependency

    def dependency(principal: Principal = Depends(credential(app_cfg))) -> Principal:
        return _demand(principal, wanted)

    setattr(dependency, SCOPE_MARK, wanted)
    return dependency


def _demand(principal: Principal, scope: str) -> Principal:
    if not principal.has(scope):
        raise APIError(
            403,
            "not_allowed",
            f"This caller does not have the {scope!r} scope.",
        )
    return principal


def _make_credential(cfg: AuthConfig) -> Callable[..., Principal]:
    def credential_dependency(request: Request) -> Principal:
        principal = _resolve(cfg, request)
        request.state.principal = principal
        return principal

    return credential_dependency


def _resolve(cfg: AuthConfig, request: Any) -> Principal:
    """§3.1's table, in its order."""
    authorization = request.headers.get("authorization")
    cookie = request.headers.get("cookie")

    if authorization is not None:
        # Row 1 before row 2: a live cookie beside any Authorization header is
        # a 400, whatever the header says, and never the cookie's identity.
        if _live_session(cfg, cookie) is not None:
            raise APIError(
                400, "mixed_credentials", MESSAGES["mixed_credentials"]
            )
        try:
            return _key_principal(cfg, authorization)
        except APIError as exc:
            if exc.status == 401 and exc.code == "bad_key":
                _limit_failure(cfg, request)
            raise

    if _is_public(cfg, request):
        return ANONYMOUS

    session = _live_session(cfg, cookie)
    if session is None:
        raise APIError(401, "sign_in", MESSAGES["sign_in"])
    return _session_principal(cfg, session)


def _key_principal(cfg: AuthConfig, authorization: str) -> Principal:
    token = bearer_token(authorization)
    key = cfg.store.verify(token) if token else None
    if key is None:
        raise _bad_key("the Authorization header is not a key of this app")
    state, role = cfg.owner_status(key.owner)
    if str(state) != "active":
        raise _bad_key("the key's owner is gone")
    scopes = key.scopes & cfg.role_scopes.get(str(role or ""), frozenset())
    if key.scopes and not scopes:
        raise _bad_key("the owner's role no longer covers any of the key's scopes")
    return Principal(
        kind="key",
        id=f"key:{key.id}",
        owner=key.owner,
        scopes=scopes,
        key_id=key.id,
    )


def _session_principal(cfg: AuthConfig, session: Session) -> Principal:
    role = str(session.role or "")
    if cfg.rank(role) < cfg.rank(cfg.min_role):
        # Signed in, but not allowed in here: 403, because signing in again
        # would change nothing.
        raise APIError(403, "not_allowed", MESSAGES["not_allowed"])
    return Principal(
        kind="user",
        id=f"user:{session.id}",
        owner=str(session.id),
        scopes=cfg.role_scopes.get(role, frozenset()),
    )


def _live_session(cfg: AuthConfig, cookie: str | None) -> Session | None:
    if not cookie:
        return None
    return cfg.session_lookup(cookie)


def _bad_key(why: str) -> APIError:
    # The reason is for the operator's log; the caller is told only the code,
    # so a probe cannot tell "unknown key" from "revoked key" from "owner gone".
    logger.info("bad key: %s", why)
    return APIError(401, "bad_key", MESSAGES["bad_key"])


def _limit_failure(cfg: AuthConfig, request: Any) -> None:
    ip = client_ip(request)
    cfg.limiter.record(ip)
    if cfg.limiter.blocked(ip):
        raise APIError(
            429,
            "rate_limited",
            MESSAGES["rate_limited"],
            retry_after=cfg.limiter.retry_after(ip),
        )


def _bare(path: str) -> str:
    return (path or "/").rstrip("/") or "/"


def _is_public(cfg: AuthConfig, request: Any) -> bool:
    """Is this path one the app listed as public?

    An entry matches its own path exactly, and an entry ending in ``*`` matches
    by prefix (``/v1/openapi*``). Trailing slashes do not matter.
    """
    path = _bare(request.url.path)
    for entry in cfg.public_paths:
        if entry.endswith("*"):
            if path.startswith(_bare(entry[:-1])):
                return True
        elif path == _bare(entry):
            return True
    return False
