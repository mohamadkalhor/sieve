"""`/v1/connectors` — add a router at runtime, test it, switch it on.

Seven routes and one rule: **a token never appears in a response**, because a
connector never holds one. It holds `token_env`, the name of the environment
variable the operator set, and the API answers `token_present` so a screen can
say "that variable is not set in the service" without ever reading the value.

Every GET is answered from the store and touches no network -- a list of
routers should not be as slow as the slowest one, and a gateway that is down
should not make the page that would tell you so hang. `test` and `pull` are the
only two calls that leave the box, and both are explicit.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from sieve.api.auth import Token
from sieve.api.auth import require as require_scope
from sieve.api.routes.v1 import Read, config_of, error, owner_of, store_of
from sieve.api.sse import events
from sieve.connectors.base import ConnectorError
from sieve.connectors.loop import build_registry, refresh
from sieve.connectors.registry import KINDS, adapter_for, kinds, writing_kinds
from sieve.connectors.seed import seed_from_toml
from sieve.contracts import Connector
from sieve.store import Store

router = APIRouter(prefix="/v1/connectors", tags=["connectors"])

#: A connector name addresses it on a screen and keys its inventory rows, so it
#: stays boring, like a profile name.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.I)

#: An environment variable name, not a secret. The point of the check is that
#: somebody pasting the token itself into `token_env` is told so immediately,
#: rather than storing a key in a database that was built never to hold one.
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

#: The options a connector may carry. Every one of them *names* something.
#: There is deliberately nowhere to put a value that has to stay secret.
OPTIONS = {"admin_token_env", "timeout"}


class ConnectorBody(BaseModel):
    """What `POST /v1/connectors` accepts. No token field exists, by design."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    base_url: str
    token_env: str | None = None
    read: bool = True
    write: bool = False
    poll_minutes: int = 60
    options: dict[str, Any] = Field(default_factory=dict)


class ConnectorPatch(BaseModel):
    """What `PUT /v1/connectors/{id}` accepts: only the fields being changed."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    token_env: str | None = None
    read: bool | None = None
    write: bool | None = None
    poll_minutes: int | None = None
    options: dict[str, Any] | None = None


def row(connector: Connector) -> dict[str, Any]:
    """One connector as JSON: everything but the token, which it does not have.

    `token_present` is the question a screen actually asks -- the variable is
    named in the database and set (or not) in the service's environment, and
    those two facts live in different places.
    """
    body = connector.model_dump(mode="json")
    body["token_present"] = bool(connector.token_env and os.environ.get(connector.token_env))
    admin = connector.options.get("admin_token_env")
    if admin:
        body["admin_token_present"] = bool(os.environ.get(str(admin)))
    return body


def connectors_of(request: Request) -> Store:
    """The store, with the one-time migration from `sieve.toml` already done."""
    store = store_of(request)
    seed_from_toml(config_of(request), store, owner_of(request))
    return store


def problem(body: ConnectorBody) -> str | None:
    """Why this connector cannot be stored, in a sentence, or None."""
    if not _NAME.match(body.name):
        return (
            f"{body.name!r} is not a usable connector name: letters, digits, "
            "hyphen and underscore, up to 64 characters"
        )
    if body.kind not in KINDS:
        return f"no connector kind {body.kind!r}; have {', '.join(kinds())}"
    if not re.match(r"^https?://", body.base_url.strip()):
        return f"base_url must be an http(s) URL, not {body.base_url!r}"
    if body.write and not KINDS[body.kind].writes:
        return (
            f"kind {body.kind!r} can only be read from; the kinds that can be "
            f"written to are {', '.join(writing_kinds())}"
        )
    if body.poll_minutes < 1:
        return "poll_minutes must be at least 1"
    unknown = sorted(set(body.options) - OPTIONS)
    if unknown:
        return (
            f"unknown option(s) {', '.join(unknown)}; a connector carries "
            f"{', '.join(sorted(OPTIONS))}"
        )
    for field in ("token_env", "admin_token_env"):
        named = body.token_env if field == "token_env" else body.options.get(field)
        if named and not _ENV_NAME.match(str(named)):
            return (
                f"{field} is the NAME of an environment variable holding the token "
                f"(like GATEWAY_TOKEN), not the token; {str(named)[:8]}... is not a "
                "variable name"
            )
    return None


def found(store: Store, connector_id: str, owner_id: str | None = None) -> Connector | None:
    """One connector, if this caller may see it.

    A connector is never shared: it holds somebody's token and writing a combo
    to it spends their credentials. Somebody else's id reads as absent, not as
    forbidden, so the answer does not confirm that it exists.
    """
    connector = store.connector(connector_id)
    if connector is None:
        return None
    if (connector.owner_id or "") != (owner_id or ""):
        return None
    return connector


# --------------------------------------------------------------------------- #
# reading -- never touches the network
# --------------------------------------------------------------------------- #


@router.get("")
def list_connectors(request: Request, _: Read = None) -> list[dict[str, Any]]:
    return [row(c) for c in connectors_of(request).connectors(owner_of(request))]


@router.get("/{connector_id}")
def get_connector(request: Request, connector_id: str, _: Read = None) -> Any:
    connector = found(connectors_of(request), connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    return row(connector)


@router.get("/{connector_id}/models")
def get_connector_models(request: Request, connector_id: str, _: Read = None) -> Any:
    """What this connector was last seen serving. From the store, not the wire.

    `matched` is the canonical id where Sieve is confident; an id it will not
    guess at keeps `model_id` null and is listed for a person to alias.
    """
    store = connectors_of(request)
    connector = found(store, connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    rows = store.reachable_for(connector_id)
    return {
        "connector": connector.name,
        "last_pull_at": connector.last_pull_at,
        "models": [{"local_id": r.local_id, "model_id": r.model_id} for r in rows],
        "count": len(rows),
        "matched": sum(1 for r in rows if r.model_id),
    }


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #


@router.post("")
def create_connector(
    request: Request,
    body: Annotated[ConnectorBody, Body()],
    token: Annotated[Token, Depends(require_scope("apply"))],
) -> Any:
    store = connectors_of(request)
    reason = problem(body)
    if reason:
        return error(400, "bad_request", reason)
    owner_id = owner_of(request)
    if store.connector_named(body.name, owner_id):
        return error(409, "conflict", f"a connector named {body.name!r} already exists")
    connector = Connector(
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(UTC),
        owner_id=owner_id,
        **body.model_dump(),
    )
    store.add_connector(connector)
    events.publish("connector", {"connector": connector.name, "change": "created"})
    return row(connector)


@router.put("/{connector_id}")
def update_connector(
    request: Request,
    connector_id: str,
    body: Annotated[ConnectorPatch, Body()],
    token: Annotated[Token, Depends(require_scope("apply"))],
) -> Any:
    store = connectors_of(request)
    connector = found(store, connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    changes = body.model_dump(exclude_none=True)
    merged = connector.model_copy(update=changes)
    reason = problem(ConnectorBody(**merged.model_dump(include=set(ConnectorBody.model_fields))))
    if reason:
        return error(400, "bad_request", reason)
    clash = store.connector_named(merged.name, owner_of(request))
    if clash is not None and clash.id != connector.id:
        return error(409, "conflict", f"a connector named {merged.name!r} already exists")
    store.put_connector(merged)
    events.publish("connector", {"connector": merged.name, "change": "updated"})
    return row(merged)


@router.delete("/{connector_id}")
def delete_connector(
    request: Request,
    connector_id: str,
    token: Annotated[Token, Depends(require_scope("apply"))],
) -> Any:
    store = connectors_of(request)
    connector = found(store, connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    store.delete_connector(connector_id)
    events.publish("connector", {"connector": connector.name, "change": "deleted"})
    return {"deleted": connector_id, "name": connector.name}


# --------------------------------------------------------------------------- #
# the two calls that leave the box
# --------------------------------------------------------------------------- #


@router.post("/{connector_id}/test")
def test_connector(
    request: Request,
    connector_id: str,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    """Reach the router now. Answers 200 with `ok: false` when it is down.

    A connector that cannot be reached is a fact about the world, not a server
    error, and a 500 would lose the sentence that says which one it is.

    Gated on `profiles:write` (gate v2, CONTRACTS section 10): this used to
    carry no scope dependency at all, on the theory that testing a router
    decides nothing about where traffic goes. That reasoning stood only while
    every caller reached this box over loopback; behind nginx's edge a bare
    read would have let anybody with a live gate session -- or nobody at all,
    before gate existed -- make this box call out to an arbitrary connector's
    `base_url` on demand. `profiles:write` rather than `apply`: it is the same
    scope that already lets a caller *read* every connector's shape, and
    reaching one to ask if it answers is not a stronger act than that.
    """
    store = connectors_of(request)
    connector = found(store, connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    try:
        outcome = adapter_for(connector).test()
    except ConnectorError as exc:
        store.touch_connector(connector_id, error=str(exc))
        return {"ok": False, "models_count": 0, "error": str(exc)}
    store.touch_connector(connector_id, error=outcome.error)
    return outcome


@router.post("/{connector_id}/pull")
def pull_connector(
    request: Request,
    connector_id: str,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    """Refresh this connector's inventory now, instead of waiting for the hour.

    Gated on `profiles:write` for the same reason `test` now is: it reaches
    an arbitrary connector's `base_url` on demand, and behind nginx's edge
    that is not something an anonymous caller should be able to trigger.
    """
    cfg, store = config_of(request), connectors_of(request)
    connector = found(store, connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    try:
        count = refresh(store, connector, build_registry(cfg, store))
    except ConnectorError as exc:
        return error(502, "connector_unreachable", str(exc))
    events.publish("pull", {"connector": connector.name, "found": count.found})
    return {
        "connector": connector.name,
        "found": count.found,
        "matched": count.matched,
        "unmatched": count.unmatched,
    }
