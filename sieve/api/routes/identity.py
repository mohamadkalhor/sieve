"""Who am I, and the script tokens I hold (AMS-28).

Two small surfaces that only make sense once several people share a box:
`/v1/me` so a page can say whose seat it is drawing, and `/v1/tokens` so a
person can mint a credential for their own cron job without an operator editing
a unit file.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Request

from sieve import owners
from sieve import tokens as script_tokens
from sieve.api import aio
from sieve.api.auth import ROLE_SCOPES, Token, bearer, owner_of_request
from sieve.api.auth import require as require_scope
from sieve.api.routes.v1 import Read, error, store_of

router = APIRouter(prefix="/v1", tags=["identity"])

_legacy_gate = require_scope("profiles:write")
_keys_gate = require_scope("keys")


def _keys_dependency(
    request: Request, authorization: str | None = Header(default=None, include_in_schema=False)
) -> Token:
    """Minting and listing keys: `profiles:write` as ever, and with `AGENT_V1` on
    the kit's `keys` scope, which no write-only key holds."""
    gate = _keys_gate if aio.agent_v1() else _legacy_gate
    return gate(request, authorization)


setattr(_keys_dependency, "__aio_scope__", "keys")
Keys = Depends(_keys_dependency)


def _me(request: Request) -> Any:
    owner_id = owner_of_request(request)
    if owner_id is None:
        return None
    return owners.by_id(store_of(request), owner_id)


@router.get("/me")
def get_me(request: Request, _: Read = None) -> Any:
    """The seat this call is answered from.

    On a box with no sign-ins there is no user row, and rather than inventing
    one this says so: `user_id` None, role "owner", because a lone operator with
    the configured token *is* the owner of everything in the store.
    """
    store = store_of(request)
    who = _me(request)
    if who is None:
        return {
            "user_id": None,
            "email": None,
            "role": "owner",
            "slug": None,
            "counts": {
                "profiles": int(
                    store.db.execute("SELECT COUNT(*) n FROM profiles").fetchone()["n"]
                ),
                "connectors": len(store.connectors()),
                "axes": int(store.db.execute("SELECT COUNT(*) n FROM axes").fetchone()["n"]),
                "tokens": 0,
            },
        }
    return {**who.json(), "counts": owners.counts_for(store, who)}


@router.get("/tokens")
def get_tokens(
    request: Request, token: Annotated[Token, Keys]
) -> Any:
    owner_id = owner_of_request(request)
    if owner_id is None:
        return error(409, "no_identity", "nobody has signed in on this box yet")
    return [item.json() for item in script_tokens.tokens(store_of(request), owner_id)]


@router.post("/tokens", status_code=201)
def post_token(
    request: Request,
    body: Annotated[dict[str, Any], Body()],
    token: Annotated[Token, Keys],
) -> Any:
    """Mint a token. The secret is in this reply and nowhere else, ever.

    A person may not mint more than they hold: a viewer handing themselves a
    writing token would be a way around their own role.
    """
    owner_id = owner_of_request(request)
    if owner_id is None:
        return error(409, "no_identity", "nobody has signed in on this box yet")
    who = owners.by_id(store_of(request), owner_id)
    # A token whose owner row is gone mints as a viewer, never as an owner:
    # "owner" carries admin, and a missing record must not grant more than the
    # seat the token was made for. Refusing outright would strand a token that
    # still works for reading, so the fallback is the smallest role there is.
    allowed = set(ROLE_SCOPES.get(who.role if who else "viewer", frozenset())) | {"telemetry"}
    wanted = set(body.get("scopes") or ["read"])
    if bearer(request.headers.get("authorization")):
        # A key never mints scopes its own principal lacks.
        allowed &= set(token.scopes)
    if not wanted <= allowed:
        return error(
            403,
            "scope_refused",
            f"your role cannot grant {', '.join(sorted(wanted - allowed))}",
        )
    try:
        made, secret = script_tokens.mint(
            store_of(request), owner_id, str(body.get("name") or ""), wanted
        )
    except ValueError as exc:
        return error(400, "bad_token", str(exc))
    except sqlite3.IntegrityError:
        # Two calls minting the same name at once: the second is told so, in
        # the same sentence, rather than becoming a 500 on a page.
        return error(409, "exists", "a token of that name already exists")
    return {**made.json(), "secret": secret}


@router.delete("/tokens/{token_id}")
def delete_token(
    request: Request,
    token_id: str,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    owner_id = owner_of_request(request)
    if owner_id is None:
        return error(409, "no_identity", "nobody has signed in on this box yet")
    if not script_tokens.revoke(store_of(request), owner_id, token_id):
        return error(404, "not_found", f"no token {token_id!r}")
    return {"revoked": token_id}
