"""agentkit on sieve's /v1 surface -- card O9a.

sieve's /v1 is its live API: the web app calls it with gate's session cookie and
machine callers call it with `SIEVE_TOKENS` bearers. The kit is vendored at
`_vendor/agentkit` (the receipt is `tests/test_vendored_agentkit.py`), and this
module is the whole of the seam between the two.

Every part of it is behind `AGENT_V1`:

* unset, empty, `off`, `no` -- `agent_v1()` is False: `require` and
  `require_read` never call in here, `mount()` does nothing at all, and the box
  answers exactly as it did before the kit arrived.
* `1`, `on`, `true`, `yes`, `enabled` -- §3.1's credential rule replaces
  sieve's own reading of the same two headers, and the kit's errors, limits,
  idempotency, audit and self-description are mounted on /v1.

Nothing here is a second identity system. `SieveKeys` reads the same
`SIEVE_TOKENS` records and the same `tokens` rows sieve has always read,
`owner_status` asks the same gate, `session_lookup` is sieve's own session call,
and `_token_for` hands the route the same `sieve.api.auth.Token` it has always
been handed -- so no route body changed. What changed is who decides, and what
the caller is told when they are refused.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from fastapi import Request
from fastapi.routing import APIRoute, request_response

from sieve import __version__
from sieve import owners
from sieve import tokens as script_tokens
from sieve.api import auth

#: The app's own root: `sieve/api/aio.py` -> `sieve/api` -> `sieve` -> root.
_ROOT = Path(__file__).resolve().parents[2]
#: The kit's vendored tree. The vendored files import themselves as `agentkit`,
#: so this directory goes on `sys.path` before they are imported -- that is the
#: kit's own vendoring shape (`_vendor/agentkit/`), not a choice made here.
_VENDOR = _ROOT / "_vendor"

AGENT_V1_ENV = "AGENT_V1"
_ON = frozenset({"1", "on", "true", "yes", "enabled"})


def agent_v1() -> bool:
    """Is the kit's rule the one this box answers with?

    Read from the environment on every call rather than cached at import: the
    tests flip it between apps in one process, and an operator flipping it needs
    a restart either way.
    """
    return os.environ.get(AGENT_V1_ENV, "").strip().lower() in _ON


@dataclass(frozen=True)
class _Kit:
    """The vendored modules, under the names this module reads them by."""

    audit: Any
    auth: Any
    errors: Any
    guide: Any
    idem: Any
    limits: Any


_KIT: _Kit | None = None


def _kit() -> _Kit:
    """The vendored kit, imported the first time something asks for it.

    Deliberately not imported at module level: a box running with `AGENT_V1`
    off -- the live one -- never reads `_vendor/` at all, so a half-copied tree
    cannot take the API down.
    """
    global _KIT
    if _KIT is None:
        vendored = str(_VENDOR)
        if vendored not in sys.path:
            sys.path.insert(0, vendored)
        import agentkit.audit as audit
        import agentkit.auth as kit_auth
        import agentkit.errors as errors
        import agentkit.guide as guide
        import agentkit.idem as idem
        import agentkit.limits as limits

        _KIT = _Kit(
            audit=audit,
            auth=kit_auth,
            errors=errors,
            guide=guide,
            idem=idem,
            limits=limits,
        )
    return _KIT


# -- the scope vocabulary ------------------------------------------------ #

#: sieve's scopes -> the kit's (§3.2). The two vocabularies are not the same
#: words for the same acts: sieve names the resource (`profiles:write`), the kit
#: names the act (`write`), and `apply` -- shipping a chain to a live gateway --
#: is the kit's `run`.
KIT_SCOPES: Mapping[str, str] = {
    "read": "read",
    "profiles:write": "write",
    "apply": "run",
    "admin": "admin",
}

#: `telemetry` has no kit equivalent -- machines reporting outcomes is sieve's
#: own act -- so it keeps its own name. The kit treats a scope it does not know
#: as opaque, and §3.1's role cap still applies to it, which is the point: only
#: the owner may report outcomes.
PASSTHROUGH_SCOPES = frozenset({"telemetry"})


def kit_scopes(scopes: Iterable[str]) -> frozenset[str]:
    """sieve's scope names as the kit's, dropping anything neither one knows.

    Dropping is the strict reading: a scope this box cannot enforce is not a
    scope a caller gets to hold. A key whose scopes are *all* unknown ends up
    empty, and §3.1 refuses an empty-scope key rather than handing it a route it
    was never granted.
    """
    found = {str(one).strip() for one in scopes if str(one).strip()}
    return frozenset(
        KIT_SCOPES[one] for one in found if one in KIT_SCOPES
    ) | frozenset(found & PASSTHROUGH_SCOPES)


def kit_role_scopes() -> dict[str, frozenset[str]]:
    """`auth.ROLE_SCOPES` in the kit's vocabulary.

    Derived from sieve's own table rather than written out again, so the cap the
    kit applies to a key cannot drift away from the scopes a session has.
    """
    return {role: kit_scopes(scopes) for role, scopes in auth.ROLE_SCOPES.items()}


#: §3.1's public paths: what a caller with no credential at all may read. sieve
#: has no `/v1/health` route today and one is listed anyway -- the card names it,
#: and a health route that needed a credential would be a trap.
PUBLIC_PATHS = ("/v1/guide", "/v1/openapi.json", "/healthz", "/v1/health")

#: The role a key's owner is taken to hold when there is nobody to ask. See
#: `owner_status`.
OWNER_ROLE = "owner"


# -- §3.1's two readers, fed sieve's own records ------------------------- #


class SieveKeys:
    """The kit's key store, over sieve's two places a secret can live.

    The kit asks a store for one thing: `verify(secret) -> Key | None`. sieve's
    secrets are in `SIEVE_TOKENS` (the box's own configuration, authenticating
    as the gate owner) and in the `tokens` table (where a token authenticates as
    whoever minted it). Both are read here, in that order, exactly as
    `auth.resolve_bearer` reads them -- this adapter adds no third place and
    grants nothing the route would not have granted.

    `app` is sieve's name in gate's vocabulary: the kit refuses a store that
    belongs to another app.
    """

    def __init__(self, state: Any) -> None:
        self.app = auth.GATE_APP
        self._state = state

    def verify(self, secret: str) -> Any:
        """The key this secret is, or None.

        A revoked row is not a key: the kit's own store filters those before
        answering, and a caller must not learn the difference between "never
        existed" and "was revoked".
        """
        token = str(secret or "").strip()
        if not token:
            return None
        kit = _kit()
        configured = auth.Tokens.from_env().lookup(token)
        if configured is not None:
            return kit.auth.Key(
                id=configured.name,
                app=self.app,
                owner=self.owner_id(),
                name=configured.name,
                scopes=kit_scopes(configured.scopes),
                created=0,
                expires=0,
            )
        store = getattr(self._state, "store", None)
        if store is None:
            return None
        minted = script_tokens.lookup(store, token)
        if minted is None or getattr(minted, "revoked", False):
            return None
        return kit.auth.Key(
            id=str(minted.id),
            app=self.app,
            owner=str(minted.owner_id or ""),
            name=str(minted.name),
            scopes=kit_scopes(minted.scopes),
            created=0,
            expires=0,
        )

    def owner_id(self) -> str:
        """The gate owner's local id, as a `SIEVE_TOKENS` call inherits it.

        Empty on a box where the owner has never signed in: there is no id to
        name, every row is unowned, and `owner_status` reads that case the same
        way sieve's own reader does.
        """
        store = getattr(self._state, "store", None)
        if store is None:
            return ""
        found = owners.owner(store)
        return str(found.id) if found is not None else ""


class _CookieCall:
    """A request-shaped shim for `session_lookup`.

    The kit hands its `session_lookup` the `Cookie` header and nothing else, and
    sieve's session reader wants a request: this is the smallest object that
    satisfies it -- the header it reads, the app whose store it resolves the
    signed-in person against, and a state to leave a note on.
    """

    def __init__(self, app: Any, cookie: str | None) -> None:
        self.app = app
        self.headers = {"cookie": str(cookie or "")}
        self.state = _Note()


class _Note:
    """A stand-in for `request.state`, which sieve's reader may write to."""


def owner_status(app: Any, owner_id: str) -> tuple[str, str | None]:
    """§3.1's G1: is this owner still active, and what may they do?

    sieve's answer, in the kit's shape. A key's scopes are capped by the role
    its owner holds *now*, which is why this is asked per request rather than
    stored on the key:

    * no local id at all -- the single-user box, where nobody has signed in and
      every row is unowned. sieve answers that case by matching everything, so
      it is `active` and the owner's role.
    * gate is not configured: there is nobody to demote the owner, and the kit's
      cap is a no-op because a key already carries the scopes it was minted
      with. `active`, owner.
    * gate is configured and knows this person: gate's own answer, through the
      kit's `GateLookup` -- one hop, cached, and `auth_unavailable` rather than a
      guess when gate cannot be reached.
    * gate is configured and has never issued this person an id: gate cannot
      vouch for a role, and a role nobody can vouch for is not one. `gone`.
    """
    owner = str(owner_id or "").strip()
    if not owner:
        return ("active", OWNER_ROLE)
    store = getattr(getattr(app, "state", None), "store", None)
    if store is None:
        return ("active", OWNER_ROLE)
    found = owners.by_id(store, owner)
    if found is None:
        return ("gone", None)
    lookup = _gate_lookup()
    if lookup is None:
        return ("active", OWNER_ROLE)
    gate_id = str(getattr(found, "gate_id", "") or "").strip()
    if not gate_id:
        return ("gone", None)
    return lookup(gate_id)


_GATE: list[Any] = []


def _gate_lookup() -> Any:
    """The kit's gate lookup, built once -- or None when no gate is configured."""
    if not _GATE:
        base = os.environ.get("SIEVE_GATE_URL", "").strip().rstrip("/")
        _GATE.append(None if not base else _kit().auth.GateLookup(base, auth.GATE_APP))
    return _GATE[0]


def session_lookup(app: Any, cookie: str | None) -> Any:
    """§3.1's session row: sieve's own reader, in the kit's shape.

    sieve's reader answers a `Token` -- scopes, not a role -- so the role is the
    one whose table those scopes are. An unknown set is no session at all, which
    is what sieve already does: gate answering a role this box does not know
    grants nothing.
    """
    token = auth.gate_identity(_CookieCall(app, cookie))
    if token is None:
        return None
    role = _role_of(token.scopes)
    if role is None:
        return None
    return _kit().auth.Session(id=str(token.owner_id or ""), role=role)


def _role_of(scopes: frozenset[str]) -> str | None:
    for role, known in auth.ROLE_SCOPES.items():
        if frozenset(scopes) == known:
            return role
    return None


# -- the rule the routes actually run ------------------------------------ #


def auth_config(app: Any) -> Any:
    """The one `AuthConfig` for this app, built on first use and kept.

    Kept because the kit builds its credential dependency once per config and
    FastAPI caches a dependency by identity: one config per app is what makes
    one lookup per request rather than one per dependency. Both readers are
    closed over *this* app, so two apps in one process -- the tests -- cannot
    answer for each other.
    """
    found = getattr(app.state, "aio_auth", None)
    if found is None:
        kit = _kit()
        found = kit.auth.AuthConfig(
            app=auth.GATE_APP,
            store=SieveKeys(app.state),
            owner_status=lambda owner_id: owner_status(app, owner_id),
            role_scopes=kit_role_scopes(),
            session_lookup=lambda cookie: session_lookup(app, cookie),
            public_paths=PUBLIC_PATHS,
            min_role="viewer",
            roles=("viewer", "member", "owner"),
        )
        app.state.aio_auth = found
    return found


def limits_for(app: Any) -> Any:
    """§3.7's buckets for this app: the contract's numbers, overridable."""
    found = getattr(app.state, "aio_limits", None)
    if found is None:
        found = _kit().limits.Limits.from_env()
        app.state.aio_limits = found
    return found


def charge(request: Request, principal: Any) -> None:
    """§3.7: one request against its buckets, `429` when one is empty."""
    kit = _kit()
    wait = limits_for(request.app).check(
        principal.id,
        method=request.method,
        run=kit.limits.is_run_route(request),
    )
    if wait is not None:
        raise kit.errors.APIError(
            429,
            "rate_limited",
            kit.errors.MESSAGES["rate_limited"],
            retry_after=wait,
        )


def _refuse(status: int, code: str) -> Exception:
    kit = _kit()
    return kit.errors.APIError(status, code, kit.errors.MESSAGES[code])


def _token_for(request: Request, principal: Any) -> Any:
    """The `Token` the route is handed: sieve's own answer, unchanged.

    The kit decided *whether* this call may proceed; the route still needs the
    identity sieve has always given it -- `owner_id` for row scoping, `name` for
    the audit trail. That is the same reader sieve used before, asked once more,
    and it can only agree with the kit here: both read the same two headers.
    """
    secret = auth.bearer(request.headers.get("authorization"))
    if secret:
        found = auth.resolve_bearer(request, secret)
        if found is None:
            raise _refuse(401, "bad_key")
        return found
    if principal.kind == "anonymous":
        return None
    found = auth.gate_identity(request)
    if found is None:
        raise _refuse(401, "sign_in")
    return found


def require_scope(request: Request, scope: str) -> Any:
    """`require` with the kit in front of it.

    The order is §3.1's: the credential rule decides who this is (and refuses
    with the kit's envelope), the buckets are charged, and only then is the
    scope demanded. The scope demanded is the kit's name for the one the route
    asked for, so `profiles:write` is demanded as `write`.
    """
    principal = auth_config(request.app).dependency(request)
    charge(request, principal)
    wanted = KIT_SCOPES.get(scope, scope)
    if not principal.has(wanted):
        raise _refuse(403, "not_allowed")
    token = _token_for(request, principal)
    if token is None:
        raise _refuse(401, "sign_in")
    request.state.actor = token.name
    request.state.owner_id = token.owner_id
    return token


def read_identity(request: Request) -> Any:
    """`require_read` with the kit in front of it.

    The kit demands a credential for a non-public `/v1` path, and that is the
    one behaviour change this card makes to reads: a caller with neither a key
    nor a session is told to sign in rather than answered as the owner. With a
    credential, the route gets exactly what it got before.
    """
    principal = auth_config(request.app).dependency(request)
    charge(request, principal)
    token = _token_for(request, principal)
    if token is None:
        request.state.actor = "anon"
        request.state.owner_id = auth.owner_identity(request)
        return None
    request.state.actor = token.name
    request.state.owner_id = token.owner_id
    return token


# -- what gets mounted --------------------------------------------------- #

#: A route that changes state: it takes an `Idempotency-Key` when one is sent,
#: and is replayed when the same one is sent twice.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: §3.4's run routes: the ones that do work rather than record it. A key is
#: *required* here, because a retried run that silently ran twice is the thing
#: idempotency exists to prevent. Matched on the route's own path format, so the
#: concrete `/v1/runs/rank` matches `/v1/runs/{step}`.
#:
#: This is deliberately the card's three and not every route that does work:
#: `POST /v1/profiles/{name}/apply` and `POST /v1/connectors/{id}/pull` do work
#: too, but `sieve-probe` calls the first one every 15 minutes from outside this
#: repo (probe_chain.py:164) and the card forbids changing a caller outside it.
#: Requiring a key there would turn a live hourly caller into a 400. A key sent
#: to those routes is still honoured and replayed -- the kit's wrapper is on
#: every write -- it is only *required* on the three below.
RUN_ROUTES = (
    "/v1/apply",
    "/v1/runs/{step}",
    "/v1/sources/{name}/pull",
)

#: The note this module leaves on a route it has already wrapped, so mounting
#: twice -- two `create_app` calls on one app object -- cannot wrap twice.
WRAPPED = "__aio_wrapped__"

LLMS_TEXT = """# sieve

sieve decides where an LLM request goes: it holds the profiles, axes, aliases
and outcomes, ranks candidate models against them, and applies a chosen chain to
a gateway.

Credentials: a bearer secret -- either one of the box's configured
`SIEVE_TOKENS` records or an API key minted through `POST /v1/tokens` -- or a
gate session cookie from a browser that has signed in. A call needs the scope
named on its route.

The guide is `/v1/guide` (markdown, the app's own operating notes). The machine
spec is `/v1/openapi.json`.
"""


def mount(app: Any, config: Any) -> None:
    """Put the kit on `app` -- and nothing at all when `AGENT_V1` is off.

    Called from `create_app` once the `/v1` routers are included and *before*
    the two catch-alls, because a catch-all registered first would answer
    `/v1/guide`, `/llms.txt` and anything else the kit adds with the app shell.
    """
    if not agent_v1():
        return
    kit = _kit()
    # Before the middleware stack is built: the handler table is read then.
    kit.errors.install(app)
    # §3.7's cap and §3.10's row-per-write. Both read `request.state.principal`,
    # which the credential rule leaves there, so both see who this was.
    app.add_middleware(kit.limits.BodyCap)
    kit.audit.mount(app, kit.audit.Audit(str(config.path("aio.db"))))
    _wrap_writes(app, kit.idem.Idempotency(str(config.path("aio.db"))))
    # sieve's own `/v1/guide` is the same document the kit serves; with the kit
    # on, the kit's route is the one that answers, so the older one comes out
    # rather than shadowing it (FastAPI answers with the first match).
    app.router.routes = [
        route
        for route in app.router.routes
        if not (isinstance(route, APIRoute) and route.path == "/v1/guide")
    ]
    guide_path = _ROOT / "OPERATING.md"
    if guide_path.is_file():
        kit.guide.mount(
            app,
            guide_path,
            LLMS_TEXT,
            version=__version__,
            surfaces=["errors", "credentials", "idempotency"],
        )


def _wrap_writes(app: Any, store: Any) -> None:
    """§3.4 on every write route sieve already has.

    The routes are wrapped here rather than edited one by one: the kit's wrapper
    is what makes a key required on a run, replays an answered call, and refuses
    a key reused with a different body -- and a route added later gets it by
    being a write under `/v1`, which is the property that matters.
    """
    kit = _kit()
    prefix = str(kit.errors.PREFIX)
    for route in list(app.router.routes):
        if not isinstance(route, APIRoute) or not route.path.startswith(prefix):
            continue
        if not (route.methods or set()) & WRITE_METHODS:
            continue
        endpoint = route.endpoint
        if getattr(endpoint, WRAPPED, False):
            continue
        required = route.path_format in RUN_ROUTES
        wrapped = store.idempotent(required=required)(endpoint)
        setattr(wrapped, WRAPPED, True)
        if required:
            kit.limits.run_route(wrapped)
        route.endpoint = wrapped
        route.dependant.call = wrapped
        route.app = request_response(route.get_route_handler())
