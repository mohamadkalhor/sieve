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

import dataclasses
import logging
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from sieve import __version__
from sieve import owners
from sieve import tokens as script_tokens
from sieve.api import auth
from sieve.store import Store

#: The app's own root: `sieve/api/aio.py` -> `sieve/api` -> `sieve` -> root.
_ROOT = Path(__file__).resolve().parents[2]
#: The kit's vendored tree. The vendored files import themselves as `agentkit`,
#: so this directory goes on `sys.path` before they are imported -- that is the
#: kit's own vendoring shape (`_vendor/agentkit/`), not a choice made here.
_VENDOR = _ROOT / "_vendor"

AGENT_V1_ENV = "AGENT_V1"
_ON = frozenset({"1", "on", "true", "yes", "enabled"})

_log = logging.getLogger(__name__)


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
    cursor: Any
    errors: Any
    guide: Any
    idem: Any
    jobs: Any
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
        import agentkit.cursor as cursor
        import agentkit.errors as errors
        import agentkit.guide as guide
        import agentkit.idem as idem
        import agentkit.jobs as jobs
        import agentkit.limits as limits

        _KIT = _Kit(
            audit=audit,
            auth=kit_auth,
            cursor=cursor,
            errors=errors,
            guide=guide,
            idem=idem,
            jobs=jobs,
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
    """`auth.ROLE_SCOPES` in the kit's vocabulary, plus `telemetry` for the owner.

    Derived from sieve's own table rather than written out again, so the cap the
    kit applies to a key cannot drift away from the scopes a session has.

    `telemetry` is in no role's table, because no *browser* reports outcomes --
    but the kit caps a key by its owner's role, so without it here every
    telemetry key (sieve-feed, sieve-probe, brain's outcome poster, all minted
    by or configured for the owner) would lose the scope the moment `AGENT_V1`
    went on. The owner's role holds it; nobody else's does.
    """
    table = {role: kit_scopes(scopes) for role, scopes in auth.ROLE_SCOPES.items()}
    table[OWNER_ROLE] = table.get(OWNER_ROLE, frozenset()) | PASSTHROUGH_SCOPES
    return table


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

    def __init__(self, app: Any) -> None:
        self.app = auth.GATE_APP
        self._app = app

    def _store(self) -> Any:
        """The app's store, built the way `routes.v1.store_of` builds it.

        The kit asks for a key with no request in hand, so the store is taken
        from the app rather than from a call: the same object `store_of` stashes
        on `app.state`, built once from the app's config if nobody has yet.
        """
        store = getattr(self._app.state, "store", None)
        if store is not None:
            return store
        config = getattr(self._app.state, "config", None)
        if config is None:
            return None
        store = Store(config.db_path)
        self._app.state.store = store
        return store

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
        store = self._store()
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
        store = self._store()
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
    lookup = gate_lookup(app)
    if lookup is None:
        return ("active", OWNER_ROLE)
    gate_id = str(getattr(found, "gate_id", "") or "").strip()
    if not gate_id:
        return ("gone", None)
    return lookup(gate_id)


def gate_lookup(app: Any) -> Any:
    """The kit's gate lookup for *this* app, built once -- or None, no gate set.

    Per app, like `auth_config` and `limits_for`: two apps in one process must
    not share a role cache, and the cache is the thing that makes "demoting a
    user takes effect within 30 s" true. Built on first use rather than at
    mount time so a box that never looks a role up never dials gate at all.
    """
    found = getattr(app.state, "aio_gate", None)
    if found is not None:
        return found
    base = os.environ.get("SIEVE_GATE_URL", "").strip().rstrip("/")
    if not base:
        return None
    found = _kit().auth.GateLookup(base, auth.GATE_APP)
    app.state.aio_gate = found
    return found


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
            store=SieveKeys(app),
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
#: every write -- it is only *required* on the four below.
#:
#: `/v1/jobs` is §3.5's own submit, and it is a run route by the kit's marking
#: rather than by this module's: `jobs.router` puts `run_route` on it (a queued
#: job is work) and wraps it with a *required* key in the same store, so the
#: route is listed here to keep this tuple the whole truth about which `/v1`
#: routes do work. Nothing in `_wrap_writes` matches it: the kit's routes are
#: included after that pass, and they are never wrapped twice.
RUN_ROUTES = (
    "/v1/apply",
    "/v1/runs/{step}",
    "/v1/sources/{name}/pull",
    "/v1/jobs",
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


def data_dir(config: Any) -> Path:
    """The app's data directory: where its store already lives.

    `aio.db` is the kit's file next to the app's own data, so it is derived from
    the setting that already says where that is -- `[store] path` resolved
    against the config root (`Config.db_path`) -- and never from the working
    directory. Getting this wrong puts a database in whatever directory the
    process was started in; on this box the answer is `/srv/sieve/data/aio.db`
    (`/srv/sieve/sieve.toml`, `[store] path = "data/sieve.db"`).
    """
    db = getattr(config, "db_path", None)
    if db is None:  # a config with no store section: nothing to sit beside
        return Path(config.path("aio.db")).parent
    return Path(db).parent


def _aio_db(config: Any) -> str:
    """The kit's own file, created beside the store it belongs to."""
    directory = data_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory / "aio.db")


# -- §3.5's jobs ----------------------------------------------------------- #

#: How long a clean stop waits for handlers before it hands their rows back as
#: `interrupted` (checkpointed, so not an attempt). A pull is one source over
#: the network; a stop that waits longer than this is a box being killed, and
#: the restart rule re-queues whatever was in flight.
SHUTDOWN_GRACE = 5.0


class JobFailed(Exception):  # noqa: N818  (the name the kit's `_finish` answers to)
    """A handler's own refusal: the job ends `failed` with *this* code.

    The kit writes a handler that raises as `handler_error` plus the exception's
    type, which is right for a bug and wrong for an answer the app means to give
    -- `not_built` for a source whose plugin has not landed, `store_busy` when
    another writer holds the store. A handler raises this instead, and `_Jobs`
    writes the code and the sentence it carries.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)

    @classmethod
    def of(cls, answer: Any) -> JobFailed:
        """The same refusal one of `/v1`'s helpers answered as a JSONResponse."""
        try:
            import json

            body = json.loads(bytes(answer.body).decode("utf-8"))
            named = body["error"]
            return cls(str(named.get("code") or "bad_request"), str(named.get("message") or ""))
        except Exception:
            return cls("bad_request", "the answer to that call could not be read")


class _KeepsTheCode:
    """The kit's store, with one addition: a `JobFailed` keeps its code.

    Only the error a failed row carries is changed, and only when the handler
    named one (`ctx.failure`, set by `sieve.api.v1_jobs.register`'s wrapper);
    every state transition, guard and claim check is the kit's own `_finish`.
    """

    def _finish(
        self, job: Any, ctx: Any = None, *, result: Any = None, error: Any = None
    ) -> bool:
        failure = getattr(ctx, "failure", None) if ctx is not None else None
        if error is not None and isinstance(failure, dict):
            error = dict(failure)
        return super()._finish(job, ctx, result=result, error=error)  # type: ignore[misc]


def _jobs_class() -> Any:
    """The kit's `Jobs`, with `_KeepsTheCode` in front of it.

    Composed when it is first needed rather than at import: a `class` statement
    reads its base then, and this module must not read `_vendor/` at all while
    `AGENT_V1` is off.
    """
    return type("_Jobs", (_KeepsTheCode, _kit().jobs.Jobs), {})


def _jobs_key() -> bytes:
    """§3.6's key for the job list's cursors.

    `jobs.router` wants it when the routes are wired, which is when the surface
    is on -- and with no `AIO_CURSOR_KEY` outside `APP_ENV=dev` there is no key
    to give it. Then the list gets a random key for this process: paging a job
    list is not worth refusing to start an API for, and a cursor from before a
    restart is refused rather than misread.
    """
    try:
        return bytes(_kit().cursor.key_from_env())
    except Exception:
        _log.warning("AIO_CURSOR_KEY is not set: job list cursors do not survive a restart")
        return secrets.token_bytes(32)


def jobs_for(app: Any) -> Any:
    """§3.5's store for this app: the pool, its routes and this app's kinds.

    Per app, like `auth_config(app)` and `limits_for(app)`: two apps in one
    process must not share a pool or each other's registered kinds, and the
    tests build one app per test.
    """
    found = getattr(app.state, "aio_jobs", None)
    if found is None:
        found = _jobs_class()(
            _aio_db(getattr(app.state, "aio_config", None)),
            key=_jobs_key(),
            audit=getattr(app.state, "aio_audit", None),
        )
        from sieve.api import v1_jobs

        v1_jobs.register(app, found)
        app.state.aio_jobs = found
    return found


def submit(
    request: Request, principal: Any, kind: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Queue one job for a typed route, the way the kit's own `POST /v1/jobs` does.

    §3.4's seam included: the idempotency row names the job inside the insert's
    own transaction, so a replay answers this job and never queues a second.
    """
    store = jobs_for(request.app)
    claim = getattr(request.state, "idem", None)
    job = store.submit(
        principal,
        kind,
        dict(payload),
        before_queue=(
            None if claim is None else (lambda job_id: claim.set_job(job_id, db=store.connection))
        ),
    )
    return job.submitted()


def submit_pull(request: Request, source: str, *, force: bool = False) -> JSONResponse:
    """`POST /v1/sources/{name}/pull` with the kit on: a 202 and a job.

    The body is the kit's own `submitted()` answer -- `{"job": {"id", "status",
    "poll"}}` -- so a pull is polled exactly like every other job of §3.5.
    """
    from sieve.api import v1_jobs

    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise _refuse(401, "sign_in")
    return JSONResponse(
        status_code=202,
        content=submit(request, principal, v1_jobs.PULL, {"source": source, "force": bool(force)}),
    )


def start_jobs(app: Any) -> None:
    """Take `aio.lock`, apply §3.5's restart rule, start the pool.

    Only with the surface on: with it off there is no `/v1` to submit a job
    through, so an idle pool and its lock would be work nothing could ask for.
    A second process on the same data directory is refused by the lock; that is
    logged, not fatal, because the API it would take down has nothing to do
    with jobs.
    """
    if not agent_v1():
        return
    store = jobs_for(app)
    try:
        store.startup()
        store.start()
    except _kit().jobs.Locked as exc:
        _log.error("agent jobs are not running in this process: %s", exc)


def stop_jobs(app: Any) -> None:
    """A clean stop: running rows become `interrupted` and run again later."""
    store = getattr(app.state, "aio_jobs", None)
    if store is not None and store.locked:
        store.shutdown(grace=SHUTDOWN_GRACE)


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
    aio_db = _aio_db(config)
    # What the app is and where its kit file is, for the parts of the kit built
    # per app rather than once: `auth_config`, `limits_for` and `jobs_for`.
    app.state.aio_config = config
    audit_store = kit.audit.Audit(aio_db)
    app.state.aio_audit = audit_store
    kit.audit.mount(app, audit_store)
    idem_store = kit.idem.Idempotency(aio_db)
    _wrap_writes(app, idem_store)
    # §3.5's four `/v1/jobs` routes. Included *after* `_wrap_writes`, so the
    # kit's own submit route is not wrapped a second time by this app's
    # idempotency store -- `jobs.router` already wrapped it, in that same row.
    jobs_router = kit.jobs.router(
        jobs_for(app), auth_config(app), limits=limits_for(app), idem=idem_store
    )
    app.include_router(jobs_router)
    # The kit wrapped its own write routes, and the note this module reads to
    # mean "already under §3.4's rule" goes on them here: a route that says so
    # is never wrapped again, and every `/v1` write really is accounted for.
    for route in jobs_router.routes:
        if isinstance(route, APIRoute) and _is_v1_write(route):
            setattr(route.endpoint, WRAPPED, True)
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
            surfaces=["errors", "credentials", "idempotency", "jobs", "mcp"],
        )
    # O9b-3: POST /v1/mcp, served from the tool registry in `sieve/api/v1_tools.py`.
    from sieve.api import v1_tools

    v1_tools.mount(app)


def after_routes(app: Any) -> None:
    """The second half of `mount`, once `create_app` has added its /v1 catch-all.

    Nothing at all when `AGENT_V1` is off. On, the catch-all answers in the
    kit's words, and under `APP_ENV=dev` the conformance crash route is added
    ahead of it.
    """
    if not agent_v1():
        return
    _envelope_the_catch_all(app)
    if os.environ.get("APP_ENV", "").strip().lower() == "dev":
        _mount_crash(app)


def _envelope_the_catch_all(app: Any) -> None:
    """sieve's `/v1/{path}` catch-all, answering in the kit's words.

    The catch-all exists so a /v1 miss is JSON and never the SPA shell. With the
    kit on, its answer has to be §3.3's: the envelope with a `request_id`, and a
    405 (not 404) when the path is a route under another method.
    """
    routes = app.router.routes
    for index, route in enumerate(routes):
        if isinstance(route, APIRoute) and route.name == "v1_not_found":
            routes[index] = _rebuilt(route, _kit_miss(app))
            return


def _kit_miss(app: Any) -> Callable[..., Any]:
    from starlette.routing import Match

    async def v1_not_found(request: Request, path: str) -> None:
        kit = _kit()
        scope = dict(request.scope)
        allowed: set[str] = set()
        for route in _answering_routes(app):
            if getattr(route, "name", "") == "v1_not_found":
                continue
            match, _ = route.matches(scope)
            if match is Match.PARTIAL:
                allowed |= set(getattr(route, "methods", None) or ())
        if allowed:
            # Starlette's own exception: the kit's handler keeps its `Allow`
            # header and writes the §3.3 envelope around it.
            from starlette.exceptions import HTTPException

            raise HTTPException(405, headers={"Allow": ", ".join(sorted(allowed))})
        raise kit.errors.APIError(404, "not_found", f"no /v1/{path} endpoint")

    return v1_not_found


def _answering_routes(app: Any) -> list[Any]:
    """Every concrete route, walking into included routers (FastAPI 0.141)."""
    found: list[Any] = []

    def walk(routes: Any) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
            else:
                found.append(route)

    walk(app.router.routes)
    return found


#: The conformance suite's `--boom-path`: a route that raises, so §3.3's "a 500
#: carries the envelope and never the exception" can be checked over HTTP.
CRASH_PATH = "/v1/_crash"


def _mount_crash(app: Any) -> None:
    """`GET /v1/_crash`, under `APP_ENV=dev` only (never on the box)."""

    def crash() -> None:
        raise RuntimeError("conformance boom: this text must never reach the caller")

    app.add_api_route(CRASH_PATH, crash, methods=["GET"], include_in_schema=False)
    # Registered last; the /v1 catch-all would answer first, so move it ahead.
    routes = app.router.routes
    routes.insert(_first_catch_all(routes), routes.pop())


def _first_catch_all(routes: list[Any]) -> int:
    for index, route in enumerate(routes):
        if "{" in getattr(route, "path", "") and getattr(route, "path", "").startswith("/v1/{"):
            return index
    return len(routes)


def _wrap_writes(app: Any, store: Any) -> None:
    """§3.4 on every write route sieve already has.

    The routes are wrapped here rather than edited one by one: the kit's wrapper
    is what makes a key required on a run, replays an answered call, and refuses
    a key reused with a different body -- and a route added later gets it by
    being a write under `/v1`, which is the property that matters.

    FastAPI (0.141 on this box) no longer copies an included router's routes
    into `app.router.routes`: each `include_router` leaves one `_IncludedRouter`
    that reads the *module-level* router's routes lazily. Wrapping those in place
    would wrap them for every app built from the same modules -- including an
    app with `AGENT_V1` off, which must stay exactly as it was. So each included
    router is replaced, for this app only, by a copy whose write routes carry
    the wrapper. The wrapper also adds a `request` parameter to the endpoint's
    signature, which only a route *built* from the wrapper picks up, so the copy
    is built with `add_api_route` rather than patched. The copy also leaves out
    sieve's own `/v1/guide`: the kit mounts its guide at the same path.
    """
    routes = app.router.routes
    for index, entry in enumerate(list(routes)):
        original = getattr(entry, "original_router", None)
        if original is not None:
            copy = _copy_router(original, store)
            context = dataclasses.replace(entry.include_context, included_router=copy)
            routes[index] = type(entry)(original_router=copy, include_context=context)
        elif isinstance(entry, APIRoute) and _is_v1_write(entry):
            # An older FastAPI that flattened the routes: rebuild this one route.
            routes[index] = _rebuilt(entry, _wrapped(entry, store))


def _is_v1_write(route: APIRoute) -> bool:
    prefix = str(_kit().errors.PREFIX)
    return route.path.startswith(prefix) and bool((route.methods or set()) & WRITE_METHODS)


def _wrapped(route: APIRoute, store: Any) -> Callable[..., Any]:
    """The route's endpoint under the kit's idempotency wrapper (once)."""
    endpoint = route.endpoint
    if getattr(endpoint, WRAPPED, False):
        return endpoint
    required = route.path_format in RUN_ROUTES
    wrapped = store.idempotent(required=required)(endpoint)
    setattr(wrapped, WRAPPED, True)
    if required:
        _kit().limits.run_route(wrapped)
    return wrapped


def _route_kwargs(route: APIRoute) -> dict[str, Any]:
    """Everything `add_api_route` accepts that the route already carries."""
    import inspect

    from fastapi import APIRouter

    wanted = inspect.signature(APIRouter.add_api_route).parameters
    return {
        name: getattr(route, name)
        for name in wanted
        if name not in {"self", "path", "endpoint"} and hasattr(route, name)
    }


def _rebuilt(route: APIRoute, endpoint: Callable[..., Any]) -> APIRoute:
    from fastapi import APIRouter

    holder = APIRouter()
    holder.add_api_route(route.path, endpoint, **_route_kwargs(route))
    return holder.routes[-1]


def _copy_router(original: Any, store: Any) -> Any:
    """A per-app copy of `original`: write routes wrapped, sieve's guide dropped."""
    from fastapi import APIRouter

    copy = APIRouter()
    for route in original.routes:
        if getattr(route, "original_router", None) is not None:
            copy.routes.append(
                type(route)(
                    original_router=_copy_router(route.original_router, store),
                    include_context=route.include_context,
                )
            )
            continue
        if not isinstance(route, APIRoute):
            copy.routes.append(route)
            continue
        if route.path == "/v1/guide":
            continue
        endpoint = _wrapped(route, store) if _is_v1_write(route) else route.endpoint
        copy.add_api_route(route.path, endpoint, **_route_kwargs(route))
    return copy
