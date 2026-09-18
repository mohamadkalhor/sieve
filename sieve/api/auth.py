"""Who is calling: a scoped bearer token, or a gate sign-in.

Two identity sources, one answer. A script sends `Authorization: Bearer ...`
and is a `SIEVE_TOKENS` record; a person's browser carries gate's own session
cookie and is whoever gate says they are, with their role mapped onto the same
scopes. Nothing else in the API can tell the difference, which is the point:
the token line keeps working exactly as it did.

Bearer tokens (CONTRACTS section 5).

`SIEVE_TOKENS` is `name:scope,scope:secret;name:scope:secret`. A scope may
itself contain a colon (`profiles:write`), so a record is read as: the name up
to the first colon, the secret after the last colon, the comma-separated scope
list in between. A secret must therefore not contain a colon.

Reads are open unless `[server] read_token = true`; writes always need the
scope named in the table of CONTRACTS section 6. Note that a request arriving
through the nginx edge is gated a second, earlier time by
`sieve.api.edge.EdgeAuthMiddleware`, which this module has no dependency on
(that middleware calls back *into* `bearer`, `resolve_bearer` and
`gate_identity`, not the other way round).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Header, HTTPException, Request

ENV_VAR = "SIEVE_TOKENS"

# --- gate v2, the sign-in service (CONTRACTS section 10, AUTH-CONTRACT.md) --

#: e.g. `http://127.0.0.1:8112`. Unset means gate is off, on purpose: a box
#: run for local development or in CI never heard of gate and behaves exactly
#: as it always has. In production this is set in `/etc/default/sieve`; there
#: is no hardcoded fallback, because "off unless told" is the only shape that
#: keeps a bare `sieve serve` working with nothing configured.
GATE_URL_ENV = "SIEVE_GATE_URL"

#: This app's slug in gate's tenant registry (AUTH-CONTRACT.md section 1).
#: Sent as `X-Gate-App` on every call to gate; gate's answer must echo it
#: back in `app`, or the session is somebody else's app and does not count.
GATE_APP = "sieve"

#: gate v1 had a service token (`SIEVE_GATE_TOKEN`) and knew the cookie's own
#: name (`gate_session`). v2 needs neither: there is no service token to send
#: — the app proves nothing about itself, gate proves who the browser is —
#: and the app forwards whatever `Cookie` header the browser sent, verbatim,
#: rather than picking one cookie out of it by name. `SIEVE_GATE_TOKEN` is
#: retired; an operator may leave it set in the environment and it is simply
#: never read.

#: A gate role, as the scopes this API already understands. Owner and member
#: may write and apply; a viewer may write their own rows (they own private
#: copies of every profile, same as a member) but never ship a chain to a
#: live gateway. Nothing here grants `telemetry`: that is for machines
#: reporting outcomes, and a machine carries a token.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": frozenset({"read", "profiles:write", "apply"}),
    "member": frozenset({"read", "profiles:write", "apply"}),
    "viewer": frozenset({"read", "profiles:write"}),
}

#: gate is one hop away on loopback, but a page can make a dozen calls and
#: each would ask again. Thirty seconds of memory is AUTH-CONTRACT.md's own
#: number for a positive answer; a miss is remembered for five, so a session
#: that has just been created is not stuck looking anonymous for long. That
#: is also the longest a revoked session or a demotion keeps working here,
#: which is short enough to be honest about in CONTRACTS.
_GATE_TTL = 30.0
_GATE_MISS_TTL = 5.0
_gate_cache: dict[str, tuple[float, Token | None]] = {}
_gate_lock = threading.Lock()


def _store_for(request: Request) -> Any:
    """The app's store, if this app has one. Auth must not build one itself."""
    return getattr(request.app.state, "store", None)


def _profiles_dir(request: Request) -> Any:
    cfg = getattr(request.app.state, "config", None)
    return getattr(cfg, "profiles_dir", None) if cfg else None


def gate_identity(request: Request) -> Token | None:
    """The signed-in person in front of this request, or None.

    Silent when `SIEVE_GATE_URL` is unset, so a box without gate behaves
    exactly as it did before. gate being unreachable is never a yes.

    The v2 protocol (AUTH-CONTRACT.md section 9, `GET /v1/session`): forward
    the browser's whole `Cookie` header, verbatim, with `X-Gate-App: sieve`.
    No service token — gate does not ask this app to prove itself, only the
    browser — and no cookie-name knowledge: gate owns the cookie's name and
    shape, and a v1-shaped assumption about either is exactly what a v2
    migration is for. The cache key follows the same header, so a browser
    carrying other cookies alongside gate's does not collide with one that
    is not.
    """
    base = os.environ.get(GATE_URL_ENV, "").rstrip("/")
    cookie_header = request.headers.get("cookie")
    if not base or not cookie_header:
        return None

    key = hashlib.sha256(cookie_header.encode()).hexdigest()
    now = time.monotonic()
    with _gate_lock:
        hit = _gate_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    token: Token | None = None
    try:
        reply = httpx.get(
            f"{base}/v1/session",
            headers={"Cookie": cookie_header, "X-Gate-App": GATE_APP},
            timeout=2.0,
        )
        if reply.status_code == 200:
            body = reply.json()
            # Both must hold: the session has to be *this* app's, and it has
            # to be a live, approved account. Anything else is no session —
            # a pending signup and a session gate issued to another tenant
            # read exactly the same way here, as "not signed in".
            if str(body.get("app", "")) == GATE_APP and str(body.get("status", "")) == "active":
                role = str(body.get("role", ""))
                scopes = ROLE_SCOPES.get(role, frozenset())
                email = str(body.get("email") or "")
                user_id = str(body.get("user_id") or "")
                if scopes:
                    token = Token(
                        name=f"gate:{email or user_id}",
                        scopes=scopes,
                        sha256=key,
                        owner_id=_resolve_owner(request, email, role, user_id),
                    )
    except Exception:
        # A gate that is down, slow, or answers something that is not the
        # session shape, is not a gate that said yes.
        token = None

    with _gate_lock:
        if len(_gate_cache) > 4096:
            _gate_cache.clear()
        _gate_cache[key] = (now + (_GATE_TTL if token else _GATE_MISS_TTL), token)
    return token


def _resolve_owner(request: Request, email: str, role: str, gate_id: str) -> str | None:
    """Turn what gate said into the local `users.id` that owns this call's rows.

    `gate_id` is gate's own `user_id` for this session (a v2 field; v1 carried
    the same idea under the same name). `owners.sign_in` keeps it on the local
    row so a later rename on gate's side never has to be chased by hand.

    First sign-in is where the work happens: the configured owner adopts every
    unowned row, anybody else is given private copies of the seed profiles. Both
    are in `sieve.owners`, because they are facts about the data, not about HTTP.

    A store that is not there yet (an app built without a lifespan) resolves to
    None, which is the single-user behaviour -- never somebody else's id.
    """
    store = _store_for(request)
    if store is None or not email:
        return None
    try:
        from sieve import owners

        return owners.sign_in(store, email, role, _profiles_dir(request), gate_id or None).id
    except Exception:
        return None


@dataclass(frozen=True)
class Token:
    name: str
    scopes: frozenset[str]
    sha256: str
    #: The `users.id` whose rows this call may see and write. None on a box
    #: where nobody has signed in and only `SIEVE_TOKENS` is configured -- the
    #: single-user case, where every row is unowned and everything matches.
    owner_id: str | None = None

    def allows(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass
class Tokens:
    """Every configured token, looked up by secret."""

    by_secret: dict[str, Token] = field(default_factory=dict)

    @classmethod
    def from_env(cls, raw: str | None = None) -> Tokens:
        text = raw if raw is not None else os.environ.get(ENV_VAR, "")
        tokens: dict[str, Token] = {}
        for record in text.split(";"):
            record = record.strip()
            if not record:
                continue
            parts = record.split(":")
            if len(parts) < 3:
                continue
            name, secret = parts[0].strip(), parts[-1].strip()
            scopes = frozenset(s.strip() for s in ":".join(parts[1:-1]).split(",") if s.strip())
            if not name or not secret or not scopes:
                continue
            tokens[secret] = Token(
                name=name, scopes=scopes, sha256=hashlib.sha256(secret.encode()).hexdigest()
            )
        return cls(by_secret=tokens)

    def lookup(self, secret: str) -> Token | None:
        for candidate, token in self.by_secret.items():
            if hmac.compare_digest(candidate, secret):
                return token
        return None

    def __bool__(self) -> bool:
        return bool(self.by_secret)


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": {"code": "unauthorized", "message": message}},
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=403, detail={"error": {"code": "forbidden", "message": message}}
    )


def resolve_bearer(request: Request, secret: str) -> Token | None:
    """The identity a bearer secret carries, from either place it can live.

    `SIEVE_TOKENS` first: it is the box's own configuration, it authenticates as
    the gate owner, and that is what keeps every script written before this card
    working unchanged. Then the `tokens` table, where a token authenticates as
    whoever minted it.
    """
    configured = Tokens.from_env().lookup(secret)
    if configured is not None:
        return Token(
            name=configured.name,
            scopes=configured.scopes,
            sha256=configured.sha256,
            owner_id=owner_identity(request),
        )
    store = _store_for(request)
    if store is None:
        return None
    try:
        from sieve import tokens as script_tokens

        minted = script_tokens.lookup(store, secret)
    except Exception:
        return None
    if minted is None:
        return None
    return Token(
        name=minted.name, scopes=minted.scopes, sha256=minted.sha256, owner_id=minted.owner_id
    )


def owner_identity(request: Request) -> str | None:
    """The gate owner's local id, for a call that came in on a configured token.

    A script has no session, so it cannot sign anybody in; it inherits the
    owner. On a box where the owner has never signed in there is no id yet, and
    None is right: every row is unowned, so every row matches.
    """
    store = _store_for(request)
    if store is None:
        return None
    try:
        from sieve import owners

        found = owners.owner(store)
    except Exception:
        return None
    return found.id if found else None


def actor_for(request: Request, authorization: str | None) -> str:
    """The actor recorded on every decision this call produces."""
    secret = bearer(authorization)
    if secret:
        token = resolve_bearer(request, secret)
        return token.name if token else "anonymous"
    signed_in = gate_identity(request)
    return signed_in.name if signed_in else "anonymous"


def require(scope: str):  # type: ignore[no-untyped-def]
    """FastAPI dependency: the call must carry a token holding `scope`."""

    def dependency(request: Request, authorization: str | None = Header(default=None)) -> Token:
        secret = bearer(authorization)
        if not secret:
            # No token: this is a browser, so ask gate who it is.
            signed_in = gate_identity(request)
            if signed_in is None:
                raise unauthorized(f"this call needs a bearer token with the {scope!r} scope")
            if not signed_in.allows(scope):
                raise forbidden(f"your role does not allow {scope!r}")
            request.state.actor = signed_in.name
            request.state.owner_id = signed_in.owner_id
            return signed_in
        token = resolve_bearer(request, secret)
        if token is None:
            raise unauthorized("unknown token")
        if not token.allows(scope):
            raise forbidden(f"token {token.name!r} does not hold the {scope!r} scope")
        request.state.actor = token.name
        request.state.owner_id = token.owner_id
        return token

    return dependency


def require_read():  # type: ignore[no-untyped-def]
    """Reads are open unless the server is configured to demand a token.

    Open reads still resolve an identity when one is on the request, because
    "no token required" must not mean "everybody's rows": an anonymous read on
    a box with gate configured is answered as the owner, which is what it was
    before several people existed.
    """

    def dependency(
        request: Request, authorization: str | None = Header(default=None)
    ) -> Token | None:
        cfg = getattr(request.app.state, "config", None)
        secret = bearer(authorization)
        if cfg is None or not cfg.server.read_token:
            if secret:
                found = resolve_bearer(request, secret)
                if found is not None:
                    request.state.actor = found.name
                    request.state.owner_id = found.owner_id
                    return found
            signed_in = gate_identity(request)
            if signed_in is not None:
                request.state.actor = signed_in.name
                request.state.owner_id = signed_in.owner_id
                return signed_in
            request.state.owner_id = owner_identity(request)
            return None
        if not secret:
            signed_in = gate_identity(request)
            if signed_in is not None and signed_in.allows("read"):
                request.state.actor = signed_in.name
                request.state.owner_id = signed_in.owner_id
                return signed_in
            raise unauthorized("this server requires a token for reads")
        token = resolve_bearer(request, secret)
        if token is None or not token.allows("read"):
            raise unauthorized("unknown token or missing 'read' scope")
        request.state.actor = token.name
        request.state.owner_id = token.owner_id
        return token

    return dependency


def owner_of_request(request: Request) -> str | None:
    """Whose rows this call sees, once a dependency has run.

    One place, so a route never has to know whether the identity came from a
    cookie, a configured token or a minted one.
    """
    return getattr(request.state, "owner_id", None)
