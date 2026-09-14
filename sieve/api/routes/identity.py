"""Who am I, and the script tokens I hold (AMS-28).

Two small surfaces that only make sense once several people share a box:
`/v1/me` so a page can say whose seat it is drawing, and `/v1/tokens` so a
person can mint a credential for their own cron job without an operator editing
a unit file.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request

from sieve import owners
from sieve import tokens as script_tokens
from sieve.api.auth import ROLE_SCOPES, Token, owner_of_request
from sieve.api.auth import require as require_scope
from sieve.api.routes.v1 import Read, error, store_of

router = APIRouter(prefix="/v1", tags=["identity"])


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
    request: Request, token: Annotated[Token, Depends(require_scope("profiles:write"))]
) -> Any:
    owner_id = owner_of_request(request)
    if owner_id is None:
        return error(409, "no_identity", "nobody has signed in on this box yet")
    return [item.json() for item in script_tokens.tokens(store_of(request), owner_id)]


@router.post("/tokens", status_code=201)
def post_token(
    request: Request,
    body: Annotated[dict[str, Any], Body()],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    """Mint a token. The secret is in this reply and nowhere else, ever.

    A person may not mint more than they hold: a viewer handing themselves a
    writing token would be a way around their own role.
    """
    owner_id = owner_of_request(request)
    if owner_id is None:
        return error(409, "no_identity", "nobody has signed in on this box yet")
    who = owners.by_id(store_of(request), owner_id)
    allowed = set(ROLE_SCOPES.get(who.role if who else "owner", frozenset())) | {"telemetry"}
    wanted = set(body.get("scopes") or ["read"])
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
