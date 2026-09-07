"""Scoped bearer tokens, read from the environment only (CONTRACTS section 5).

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
from dataclasses import dataclass, field

from fastapi import Header, HTTPException, Request

ENV_VAR = "SIEVE_TOKENS"


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
    if not secret:
        return "anonymous"
    token = Tokens.from_env().lookup(secret)
    return token.name if token else "anonymous"


def require(scope: str):  # type: ignore[no-untyped-def]
    """FastAPI dependency: the call must carry a token holding `scope`."""

    def dependency(request: Request, authorization: str | None = Header(default=None)) -> Token:
        secret = bearer(authorization)
        if not secret:
            raise unauthorized(f"this call needs a bearer token with the {scope!r} scope")
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
            raise unauthorized("this server requires a token for reads")
        token = Tokens.from_env().lookup(secret)
        if token is None or not token.allows("read"):
            raise unauthorized("unknown token or missing 'read' scope")
        request.state.actor = token.name
        return token

    return dependency
