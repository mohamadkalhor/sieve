"""`/v1/connectors` — add a router at runtime, test it, switch it on.

Seven routes and one rule: **a token never appears in a response**, because a
connector never holds one. It holds `secret`, the id of an entry in
`[secrets.<id>]` in `sieve.toml` -- the file that says which environment
variable that id names and which connector kinds may use it -- and the API
answers `token_present` so a screen can say "that variable is not set in the
service" without ever reading the value. A body that names an environment
variable instead (`token_env`, `admin_token_env`, anything ending in `_env`) is
refused with a 422: a name is a pointer into this box's environment, and the
API is not where that pointer gets set.

Every GET is answered from the store and touches no network -- a list of
routers should not be as slow as the slowest one, and a gateway that is down
should not make the page that would tell you so hang. `test` and `pull` are the
only two calls that leave the box, and both are explicit.

Every write here -- create, update, delete, `test`, `pull` -- needs the `admin`
scope, which the gate owner's role alone carries. A connector says which host a
token this box holds is sent to, so a member or a viewer may tune their own
profiles without being able to add, redirect or delete one. What a kind may
carry in `options` is listed per kind below, and an option key a kind does not
name is refused rather than stored and quietly ignored -- `admin_secret` is a
field of its own, because only a kind with an admin API has one.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sieve.api.auth import Token
from sieve.api.auth import require as require_scope
from sieve.api.routes.v1 import Read, config_of, error, owner_of, store_of
from sieve.api.sse import events
from sieve.connectors.base import ConnectorError
from sieve.connectors.loop import build_registry, refresh
from sieve.connectors.registry import KINDS, adapter_for, kinds, writing_kinds
from sieve.connectors.seed import seed_from_toml
from sieve.contracts import Connector
from sieve.secrets import Secrets, env_key_paths, refuse_environment_names
from sieve.store import Store

router = APIRouter(prefix="/v1/connectors", tags=["connectors"])

#: A connector name addresses it on a screen and keys its inventory rows, so it
#: stays boring, like a profile name.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.I)

#: The options a connector carries when its kind has not said otherwise. Every
#: one of them *names* something. There is deliberately nowhere to put a value
#: that has to stay secret -- a credential is named by `secret`, an id from
#: `[secrets.*]`, and never carried here.
OPTIONS = frozenset({"timeout"})


def allowed_options(kind: str) -> frozenset[str]:
    """The option keys this kind understands. Kinds are free to say nothing."""
    return OPTIONS


def env_keys(raw: Any, path: str = "") -> list[str]:
    """Every key in a body that names an environment variable, with its path.

    Everywhere, not just at the top: `token_env`, `admin_token_env` inside
    `options`, or a key somebody invented. A connector that carried a variable
    name would send this box's token wherever a writer of that name pointed it,
    so the answer is a refusal that quotes the key rather than a lesson in which
    fields are checked. Shared with the bundle route and the seeder, so the rule
    reads the same at every door.
    """
    return env_key_paths(raw, path)


def _refuse_environment_names(raw: Any) -> Any:
    """A pydantic `before` validator: a 422 naming every `*_env` key there is."""
    return refuse_environment_names(raw)


class ConnectorBody(BaseModel):
    """What `POST /v1/connectors` accepts. No token field exists, by design."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    base_url: str
    #: the id of a `[secrets.<id>]` entry; the value lives in the environment
    #: variable that entry names and passes through this route without ever
    #: being read.
    secret: str | None = None
    #: the second credential, for a kind whose admin API wants its own token
    admin_secret: str | None = None
    read: bool = True
    write: bool = False
    poll_minutes: int = 60
    options: dict[str, Any] = Field(default_factory=dict)

    _no_environment_names = model_validator(mode="before")(_refuse_environment_names)


class ConnectorPatch(BaseModel):
    """What `PUT /v1/connectors/{id}` accepts: only the fields being changed."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    secret: str | None = None
    admin_secret: str | None = None
    read: bool | None = None
    write: bool | None = None
    poll_minutes: int | None = None
    options: dict[str, Any] | None = None

    _no_environment_names = model_validator(mode="before")(_refuse_environment_names)


def row(connector: Connector, secrets: Secrets | None = None) -> dict[str, Any]:
    """One connector as JSON: everything but the token, which it does not have.

    `token_present` is the question a screen actually asks -- the connector names
    a secret id, the config file says which variable that id names, and the
    service's environment decides whether it is set. Those facts live in three
    places, and only the last one is a yes or no.
    """
    registry = secrets if secrets is not None else Secrets()
    body = connector.model_dump(mode="json")
    body.pop("token_env", None)
    body["token_present"] = _present(registry, connector.secret, connector.kind)
    body["admin_token_present"] = _present(registry, connector.admin_secret, connector.kind)
    return body


def _present(secrets: Secrets, secret_id: str | None, kind: str) -> bool:
    """Whether the variable this id names is set here, and the id is usable.

    An id the config does not have, or one bound to other kinds, is not present:
    the connector would send nothing, and a screen that said otherwise would be
    lying about a credential.
    """
    return bool(os.environ.get(secrets.env(secret_id, kind) or ""))


def connectors_of(request: Request) -> Store:
    """The store, with the one-time migration from `sieve.toml` already done."""
    store = store_of(request)
    seed_from_toml(config_of(request), store, owner_of(request))
    return store


def secrets_of(request: Request) -> Secrets:
    """This box's `[secrets.*]` table: the only thing an id resolves against."""
    return config_of(request).secret_registry


def problem(body: ConnectorBody, secrets: Secrets | None = None) -> tuple[int, str, str] | None:
    """Why this connector cannot be stored, or None.

    A (status, code, sentence) triple rather than a bare sentence: an option
    key this kind does not understand, and a secret id the config does not have
    or does not allow for this kind, are refused as 422 `bad_connector`, the
    same status a schema violation gets, while everything else here is an
    ordinary 400 `bad_request`.
    """
    if not _NAME.match(body.name):
        return (
            400,
            "bad_request",
            f"{body.name!r} is not a usable connector name: letters, digits, "
            "hyphen and underscore, up to 64 characters",
        )
    if body.kind not in KINDS:
        return (400, "bad_request", f"no connector kind {body.kind!r}; have {', '.join(kinds())}")
    if not re.match(r"^https?://", body.base_url.strip()):
        return (400, "bad_request", f"base_url must be an http(s) URL, not {body.base_url!r}")
    if body.write and not KINDS[body.kind].writes:
        return (
            400,
            "bad_request",
            f"kind {body.kind!r} can only be read from; the kinds that can be "
            f"written to are {', '.join(writing_kinds())}",
        )
    if body.poll_minutes < 1:
        return (400, "bad_request", "poll_minutes must be at least 1")
    allowed = allowed_options(body.kind)
    unknown = sorted(set(body.options) - allowed)
    if unknown:
        return (
            422,
            "bad_connector",
            f"unknown option(s) {', '.join(unknown)} for kind {body.kind!r}; "
            f"it carries {', '.join(sorted(allowed))}",
        )
    if body.admin_secret and not KINDS[body.kind].admin_api:
        return (
            422,
            "bad_connector",
            f"admin_secret is the second credential of a kind with an admin API, "
            f"and {body.kind!r} has none",
        )
    registry = secrets if secrets is not None else Secrets()
    for field, secret_id in (("secret", body.secret), ("admin_secret", body.admin_secret)):
        reason = registry.problem(secret_id, body.kind)
        if reason:
            return (422, "bad_connector", f"{field}: {reason}")
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
    secrets = secrets_of(request)
    return [row(c, secrets) for c in connectors_of(request).connectors(owner_of(request))]


@router.get("/{connector_id}")
def get_connector(request: Request, connector_id: str, _: Read = None) -> Any:
    connector = found(connectors_of(request), connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    return row(connector, secrets_of(request))


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
    token: Annotated[Token, Depends(require_scope("admin"))],
) -> Any:
    store = connectors_of(request)
    secrets = secrets_of(request)
    complaint = problem(body, secrets)
    if complaint:
        return error(*complaint)
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
    return row(connector, secrets)


@router.put("/{connector_id}")
def update_connector(
    request: Request,
    connector_id: str,
    body: Annotated[ConnectorPatch, Body()],
    token: Annotated[Token, Depends(require_scope("admin"))],
) -> Any:
    store = connectors_of(request)
    secrets = secrets_of(request)
    connector = found(store, connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    changes = body.model_dump(exclude_none=True)
    merged = connector.model_copy(update=changes)
    complaint = problem(
        ConnectorBody(**merged.model_dump(include=set(ConnectorBody.model_fields))), secrets
    )
    if complaint:
        return error(*complaint)
    clash = store.connector_named(merged.name, owner_of(request))
    if clash is not None and clash.id != connector.id:
        return error(409, "conflict", f"a connector named {merged.name!r} already exists")
    store.put_connector(merged)
    events.publish("connector", {"connector": merged.name, "change": "updated"})
    return row(merged, secrets)


@router.delete("/{connector_id}")
def delete_connector(
    request: Request,
    connector_id: str,
    token: Annotated[Token, Depends(require_scope("admin"))],
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
    token: Annotated[Token, Depends(require_scope("admin"))],
) -> Any:
    """Reach the router now. Answers 200 with `ok: false` when it is down.

    A connector that cannot be reached is a fact about the world, not a server
    error, and a 500 would lose the sentence that says which one it is.

    Gated on `admin` (gate v2, then A01; CONTRACTS section 10). It used to
    carry no scope dependency at all, and then `profiles:write`, on the theory
    that testing a router decides nothing about where traffic goes. That
    reasoning stood only while every caller reached this box over loopback;
    behind nginx's edge a bare read would have let anybody with a live gate
    session -- or nobody at all, before gate existed -- make this box call out
    to an arbitrary connector's `base_url` on demand. `admin`, like every other
    connector write: the call leaves the box carrying a credential the owner
    put there, whatever the answer turns out to be.
    """
    store = connectors_of(request)
    connector = found(store, connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    try:
        outcome = adapter_for(connector, secrets_of(request)).test()
    except ConnectorError as exc:
        store.touch_connector(connector_id, error=str(exc))
        return {"ok": False, "models_count": 0, "error": str(exc)}
    store.touch_connector(connector_id, error=outcome.error)
    return outcome


@router.post("/{connector_id}/pull")
def pull_connector(
    request: Request,
    connector_id: str,
    token: Annotated[Token, Depends(require_scope("admin"))],
) -> Any:
    """Refresh this connector's inventory now, instead of waiting for the hour.

    Gated on `admin` for the same reason `test` is: it reaches an arbitrary
    connector's `base_url` on demand and spends the credential that connector
    names, and behind nginx's edge that is not something a member or a viewer
    should be able to trigger.
    """
    cfg, store = config_of(request), connectors_of(request)
    connector = found(store, connector_id, owner_of(request))
    if connector is None:
        return error(404, "not_found", f"no connector {connector_id!r}")
    try:
        count = refresh(store, connector, build_registry(cfg, store), cfg.secret_registry)
    except ConnectorError as exc:
        return error(502, "connector_unreachable", str(exc))
    events.publish("pull", {"connector": connector.name, "found": count.found})
    return {
        "connector": connector.name,
        "found": count.found,
        "matched": count.matched,
        "unmatched": count.unmatched,
    }
