"""Who is calling: a scoped bearer token, or a gate sign-in.

Two identity sources, one answer. A script sends `Authorization: Bearer ...`
and is a `SIEVE_TOKENS` record; a person's browser sends the `gate_session`
cookie and is whoever gate says they are, with their role mapped onto the same
scopes. Nothing else in the API can tell the difference, which is the point:
the token line keeps working exactly as it did.

Bearer tokens (CONTRACTS section 5).

`SIEVE_TOKENS` is `name:scope,scope:secret;name:scope:secret`. A scope may
itself contain a colon (`profiles:write`), so a record is read as: the name up
to the first colon, the secret after the last colon, the comma-separated scope
list in between. A secret must therefore not contain a colon.

Reads are open unless `[server] read_token = true`; writes always need the
scope named in the table of CONTRACTS section 6.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from dataclasses import dataclass, field

import httpx
from fastapi import Header, HTTPException, Request

ENV_VAR = "SIEVE_TOKENS"

# --- gate, the sign-in service (CONTRACTS section 10) ------------------------

GATE_URL_ENV = "SIEVE_GATE_URL"      # e.g. http://127.0.0.1:8112 — unset: off
GATE_TOKEN_ENV = "SIEVE_GATE_TOKEN"  # the service token gate knows us by
GATE_COOKIE = "gate_session"

#: A gate role, as the scopes this API already understands. Owner and member
#: may write; a viewer may only read. Nothing here grants `telemetry`: that is
#: for machines reporting outcomes, and a machine carries a token.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": frozenset({"read", "profiles:write", "apply"}),
    "member": frozenset({"read", "profiles:write", "apply"}),
    "viewer": frozenset({"read"}),
}

#: gate is one hop away on loopback, but a page can make a dozen calls and
#: each would ask again. A minute of memory is the compromise: that is also
#: the longest a revoked session keeps working here, which is short enough to
#: be honest about in CONTRACTS.
_GATE_TTL = 60.0
_GATE_MISS_TTL = 10.0
_gate_cache: dict[str, tuple[float, Token | None]] = {}
_gate_lock = threading.Lock()


def gate_identity(request: Request) -> Token | None:
    """The signed-in person in front of this request, or None.

    Silent when `SIEVE_GATE_URL` is unset, so a box without gate behaves
    exactly as it did before. gate being unreachable is never a yes.
    """
    base = os.environ.get(GATE_URL_ENV, "").rstrip("/")
    cookie = request.cookies.get(GATE_COOKIE)
    if not base or not cookie:
        return None

    key = hashlib.sha256(cookie.encode()).hexdigest()
    now = time.monotonic()
    with _gate_lock:
        hit = _gate_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    token: Token | None = None
    try:
        params = {}
        service = os.environ.get(GATE_TOKEN_ENV, "")
        if service:
            params["token"] = service
        reply = httpx.get(
            f"{base}/v1/session",
            params=params or None,
            cookies={GATE_COOKIE: cookie},
            timeout=2.0,
        )
        if reply.status_code == 200:
            body = reply.json()
            scopes = ROLE_SCOPES.get(str(body.get("role", "")), frozenset())
            if scopes:
                token = Token(
                    name=f"gate:{body.get('email') or body.get('user_id')}",
                    scopes=scopes,
                    sha256=key,
                )
    except Exception:
        # A gate that is down, slow or confused is not a gate that said yes.
        token = None

    with _gate_lock:
        if len(_gate_cache) > 4096:
            _gate_cache.clear()
        _gate_cache[key] = (now + (_GATE_TTL if token else _GATE_MISS_TTL), token)
    return token


@dataclass(frozen=True)
class Token:
    name: str
    scopes: frozenset[str]
    sha256: str

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


def actor_for(request: Request, authorization: str | None) -> str:
    """The actor recorded on every decision this call produces."""
    secret = bearer(authorization)
    if secret:
        token = Tokens.from_env().lookup(secret)
        if token:
            return token.name
        return "anonymous"
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
            return signed_in
        token = Tokens.from_env().lookup(secret)
        if token is None:
            raise unauthorized("unknown token")
        if not token.allows(scope):
            raise forbidden(f"token {token.name!r} does not hold the {scope!r} scope")
        request.state.actor = token.name
        return token

    return dependency


def require_read():  # type: ignore[no-untyped-def]
    """Reads are open unless the server is configured to demand a token."""

    def dependency(
        request: Request, authorization: str | None = Header(default=None)
    ) -> Token | None:
        cfg = getattr(request.app.state, "config", None)
        if cfg is None or not cfg.server.read_token:
            return None
        secret = bearer(authorization)
        if not secret:
            signed_in = gate_identity(request)
            if signed_in is not None and signed_in.allows("read"):
                request.state.actor = signed_in.name
                return signed_in
            raise unauthorized("this server requires a token for reads")
        token = Tokens.from_env().lookup(secret)
        if token is None or not token.allows("read"):
            raise unauthorized("unknown token or missing 'read' scope")
        request.state.actor = token.name
        return token

    return dependency
