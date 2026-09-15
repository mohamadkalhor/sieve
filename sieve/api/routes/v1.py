"""`/v1` — the whole control surface of CONTRACTS section 6.

A route whose owner module has not landed yet answers 501 with the error
envelope and, under `shape`, the JSON schema of what it will return, so the
web can be built against it before the code behind it exists.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from sieve.api.auth import Token, actor_for, owner_of_request, require_read
from sieve.api.auth import require as require_scope
from sieve.api.sse import events
from sieve.axes import control as axis_control
from sieve.config import Config
from sieve.contracts import (
    Axis,
    BoardRow,
    Chain,
    Decision,
    HealthRow,
    Leaderboard,
    Modality,
    ModelRef,
    Outcome,
    Profile,
    Ranking,
    Reachable,
    TargetDiff,
    TargetResult,
    TelemetryEvent,
)
from sieve.engine import Deps as EngineDeps
from sieve.engine import OwnerMissingError, apply_targets, rank_profile
from sieve.profiles import control
from sieve.profiles.legacy import clean_profile
from sieve.scoring import leaderboard
from sieve.scoring.health import health as health_of
from sieve.scoring.health import health_series
from sieve.scoring.pulse import pulse as pulse_of
from sieve.store import Store

router = APIRouter(prefix="/v1")

Read = Annotated[Token | None, Depends(require_read())]
Auth = Annotated[str | None, Header()]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def config_of(request: Request) -> Config:
    """The app's config, built on demand when the app runs without a lifespan
    (an in-process ASGI call from the MCP bridge, or a sub-mounted app)."""
    cfg: Config | None = getattr(request.app.state, "config", None)
    if cfg is None:
        from sieve.api.app import build_config

        cfg = build_config()
        request.app.state.config = cfg
    return cfg


def store_of(request: Request) -> Store:
    store: Store | None = getattr(request.app.state, "store", None)
    if store is None:
        store = Store(config_of(request).db_path)
        request.app.state.store = store
    return store


def owner_of(request: Request) -> str | None:
    """Whose rows this call may see and write.

    Every route that touches a profile, connector, axis, multiplier or outcome
    passes this down. None means "the unowned rows", which is the whole store on
    a box where nobody has signed in -- so a single-user install behaves exactly
    as it did before several people were possible.
    """
    return owner_of_request(request)


def visible_or_404(found: Any, kind: str, name: str) -> JSONResponse | None:
    """404 for a row this caller cannot see.

    Deliberately not 403: telling somebody that `judge` exists but is not theirs
    is telling them what another person named their profile.
    """
    if found is None:
        return error(404, "not_found", f"no {kind} {name!r}")
    return None


def error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}, **extra}
    )


#: A profile name becomes a file name and a chain key, so it stays boring.
_PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.I)


def not_built(shape: type[BaseModel], owner: str, module: str) -> JSONResponse:
    """501 plus the contract shape this route will return once `module` lands."""
    return error(
        501,
        "not_built",
        f"{module} is owned by {owner} and has not landed yet",
        shape=shape.model_json_schema(),
    )


def page(items: list[Any], limit: int, cursor: str | None) -> dict[str, Any]:
    start = int(cursor) if cursor and cursor.isdigit() else 0
    window = items[start : start + limit]
    nxt = start + limit
    return {"items": window, "next_cursor": str(nxt) if nxt < len(items) else None}


def _profiles_module() -> Any:
    try:
        return import_module("sieve.profiles.load")
    except ModuleNotFoundError as exc:
        raise OwnerMissingError("sieve.profiles.load", "B") from exc


def load_profile(
    cfg: Config, name: str, store: Store | None = None, owner_id: str | None = None
) -> Profile | None:
    if store is not None:
        control.seed(store, cfg.profiles_dir, owner_id)
        return control.profile(store, name, owner_id)
    for profile in _profiles_module().load_profiles(cfg.profiles_dir):
        if profile.name == name:
            found: Profile = profile
            return found
    return None


def log_decision(
    store: Store, profile: str, kind: Any, actor: str, before: Any, after: Any, reason: str
) -> Decision:
    decision = Decision(
        id=uuid.uuid4().hex[:12],
        at=datetime.now(UTC),
        profile=profile,
        kind=kind,
        actor=actor,
        before=before,
        after=after,
        reason=reason,
    )
    store.add_decision(decision)
    events.publish("decision", {"profile": profile, "kind": kind, "reason": reason})
    return decision


# --------------------------------------------------------------------------- #
# catalog
# --------------------------------------------------------------------------- #


@router.get("/modalities")
def get_modalities(request: Request, _: Read = None) -> list[dict[str, Any]]:
    store = store_of(request)
    counts: dict[str, int] = {}
    for model in store.models():
        counts[model.modality] = counts.get(model.modality, 0) + 1
    return [
        {"modality": modality, "models": count, "observations": store.count_observations(modality)}  # type: ignore[arg-type]
        for modality, count in sorted(counts.items())
    ]


def _axes_store(request: Request) -> Store:
    cfg, store = config_of(request), store_of(request)
    control.seed(store, cfg.profiles_dir, owner_of(request))
    axis_control.seed(store, cfg.axes_dir)
    return store


def shared_axis_refused(store: Store, name: str, modality: Any, owner_id: str | None) -> Any:
    """403 when somebody who is not the owner tries to write the shared word.

    A builtin axis is the vocabulary everybody scores against. One person
    redefining "latency" for the whole box would silently move every other
    person's rankings, so a member who edits one is told no; what they may do
    instead -- keep their own axis of that name -- is what `axis_control.put`
    with their `owner_id` does, and the Axes page offers.
    """
    if owner_id is None:
        return None
    row = store.db.execute(
        "SELECT 1 FROM axes WHERE name=? AND owner_id IS NULL"
        + (" AND modality=?" if modality else ""),
        (name, modality) if modality else (name,),
    ).fetchone()
    if row is None:
        return None
    from sieve import owners

    who = owners.by_id(store, owner_id)
    if who is not None and who.role == "owner":
        return None
    return error(
        403,
        "shared_axis",
        f"axis {name!r} is shared; only the owner may change it",
    )


@router.get("/axes")
def get_axes(
    request: Request,
    modality: Modality | None = None,
    _: Read = None,
) -> list[dict[str, object]]:
    return axis_control.rows(_axes_store(request), modality, owner_of(request))


@router.get("/axes/{name}")
def get_axis(request: Request, name: str, modality: Modality | None = None, _: Read = None) -> Any:
    return axis_control.row(_axes_store(request), name, modality, owner_of(request)) or error(
        404, "not_found", f"no axis {name!r}"
    )


def _axis_problem(store: Store, value: Axis) -> JSONResponse | None:
    problems = axis_control.validate(value, store)
    if problems:
        return error(400, "bad_axis", "; ".join(problems))
    return None


@router.post("/axes", status_code=201)
def post_axis(
    request: Request,
    value: Axis,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    store = _axes_store(request)
    owner_id = owner_of(request)
    # Creating an axis of your own is not a write to the shared one, even when
    # it carries the same word: the shared row keeps its NULL owner and its
    # meaning, and this person scores against their own copy from here on.
    held = store.db.execute(
        "SELECT 1 FROM axes WHERE name=? AND modality=? AND IFNULL(owner_id,'')=IFNULL(?,'')",
        (value.name, value.modality, owner_id),
    ).fetchone()
    if held:
        return error(409, "exists", f"axis {value.name!r} already exists for {value.modality}")
    problem = _axis_problem(store, value)
    if problem:
        return problem
    axis_control.put(store, value, builtin=False, owner_id=owner_id)
    log_decision(
        store,
        value.name,
        "weights",
        token.name,
        None,
        value.model_dump(mode="json"),
        f"axis {value.name} created by {token.name}",
    )
    return axis_control.row(store, value.name, value.modality, owner_id)


@router.put("/axes/{name}")
def put_axis(
    request: Request,
    name: str,
    value: Axis,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    store = _axes_store(request)
    owner_id = owner_of(request)
    before = axis_control.row(store, name, value.modality, owner_id)
    if before is None:
        return error(404, "not_found", f"no axis {name!r} for {value.modality}")
    if value.name != name:
        return error(400, "bad_request", "the body's name must match the path")
    refused = shared_axis_refused(store, name, value.modality, owner_id)
    if refused:
        return refused
    problem = _axis_problem(store, value)
    if problem:
        return problem
    axis_control.put(store, value, builtin=False, owner_id=owner_id)
    after = axis_control.row(store, name, value.modality, owner_id)
    log_decision(
        store, name, "weights", token.name, before, after, f"axis {name} updated by {token.name}"
    )
    return after


@router.delete("/axes/{name}")
def delete_axis(
    request: Request,
    name: str,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
    modality: Modality | None = None,
    force: bool = False,
) -> Any:
    store = _axes_store(request)
    owner_id = owner_of(request)
    before = axis_control.row(store, name, modality, owner_id)
    if before is None:
        return error(404, "not_found", f"no axis {name!r}")
    held_modality = str(before["modality"])
    refused = shared_axis_refused(store, name, held_modality, owner_id)
    if refused:
        return refused
    users = axis_control.profiles_using(store, name, held_modality, owner_id)
    if users and not force:
        return error(409, "axis_in_use", f"used by {', '.join(users)}", profiles=users)
    axis_control.delete(store, name, modality=held_modality, force=force, owner_id=owner_id)
    reason = f"axis {name} deleted by {token.name}"
    if users:
        reason += "; profile weights set to 0"
    log_decision(store, name, "weights", token.name, before, None, reason)
    return {"deleted": name, "profiles_zeroed": users}


@router.get("/models")
def get_models(
    request: Request,
    modality: Modality | None = None,
    reachable: bool | None = None,
    q: str | None = None,
    limit: int = Query(default=100, le=1000),
    cursor: str | None = None,
    _: Read = None,
) -> dict[str, Any]:
    """One page of the catalogue.

    The filters and the page are applied first and only the page is priced.
    Pricing the whole modality to hand back five rows is what made
    `?modality=llm&limit=5` take over thirty seconds against 982 models and
    150,718 price rows, while the same call for music (23 models) answered in
    0.4 s and hid it.
    """
    store = store_of(request)
    local = store.local_ids(owner_of(request))
    models = store.models(modality)
    if q:
        needle = q.lower()
        models = [m for m in models if needle in m.id.lower() or needle in m.name.lower()]
    if reachable is not None:
        models = [m for m in models if (m.id in local) is reachable]
    window = page(models, limit, cursor)
    shown: list[ModelRef] = window["items"]
    prices: dict[str, dict[str, Any]] = {}
    for wanted in {m.modality for m in shown}:
        ids = [m.id for m in shown if m.modality == wanted]
        for model_id, price in store.latest_prices(wanted, only=ids).items():
            prices[model_id] = price.model_dump(mode="json")
    window["items"] = [
        {
            **m.model_dump(mode="json"),
            "reachable": m.id in local,
            "local_ids": local.get(m.id, []),
            "price": prices.get(m.id),
        }
        for m in shown
    ]
    return window


@router.get("/models/{model_id:path}")
def get_model(request: Request, model_id: str, _: Read = None) -> Any:
    store = store_of(request)
    model = store.model(model_id)
    if model is None:
        return error(404, "not_found", f"no model {model_id!r}")
    local = store.local_ids(owner_of(request))
    return {
        **model.model_dump(mode="json"),
        "reachable": model.id in local,
        "local_ids": local.get(model.id, []),
        "observations": [o.model_dump(mode="json") for o in store.observations_for(model_id)],
    }


# --------------------------------------------------------------------------- #
# profiles
# --------------------------------------------------------------------------- #


@router.get("/profiles")
def get_profiles(request: Request, modality: Modality | None = None, _: Read = None) -> Any:
    cfg = config_of(request)
    store = store_of(request)
    owner_id = owner_of(request)
    control.seed(store, cfg.profiles_dir, owner_id)
    profiles = control.profiles(store, owner_id)
    return [p for p in profiles if modality is None or p.modality == modality]


@router.get("/profiles/{name}")
def get_profile(request: Request, name: str, _: Read = None) -> Any:
    cfg = config_of(request)
    profile = load_profile(cfg, name, store_of(request), owner_of(request))
    return profile or error(404, "not_found", f"no profile {name!r}")


def _validate_profile_weights(
    cfg: Config, store: Store, profile: Profile, owner_id: str | None
) -> None:
    axis_control.seed(store, cfg.axes_dir)
    control.validate_weights(
        profile.weights,
        {axis.name for axis in axis_control.axes(store, profile.modality, owner_id)},
    )


def _save(
    cfg: Config, profile: Profile, store: Store | None = None, owner_id: str | None = None
) -> None:
    """Write a profile back: always to the store, to YAML only when it is the
    box's own.

    `profiles/*.yaml` is the shipped seed every new member is given a copy of.
    A member saving their `judge` there would rewrite what the next person is
    seeded with, so their edits stay in the store, which is where their profile
    lives anyway.
    """
    if store is not None:
        control.put_profile(store, profile, owner_id=owner_id)
    if owner_id is not None and store is not None:
        from sieve import owners

        who = owners.by_id(store, owner_id)
        if who is not None and who.role != "owner":
            return
    try:
        saver = import_module("sieve.profiles.save")
    except ModuleNotFoundError as exc:
        raise OwnerMissingError("sieve.profiles.save", "B") from exc
    saver.save_profile(cfg.profiles_dir, profile)


@router.put("/profiles/{name}")
def put_profile(
    request: Request,
    name: str,
    body: Annotated[dict[str, Any], Body()],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    """Replace a profile: its purpose, its weights, and how many it ships.

    A body written against the old shape -- a policy, a require block, a shape,
    a preferred effort mode -- is accepted, its retired keys are dropped, and
    the answer says which ones in `warnings`. Seed files and scripts written
    last month keep working, and their authors find out why the knob they sent
    did nothing.
    """
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    cleaned, warnings = clean_profile(body)
    try:
        profile = Profile.model_validate(cleaned)
    except ValidationError as exc:
        return error(400, "bad_request", str(exc.errors()[0].get("msg", exc)))
    if profile.name != name:
        return error(400, "bad_request", "the body's name must match the path")
    try:
        before = load_profile(cfg, name, store, owner_id)
        if before is None:
            return error(404, "not_found", f"no profile {name!r}; create it with POST /v1/profiles")
        _validate_profile_weights(cfg, store, profile, owner_id)
        _save(cfg, profile, store, owner_id)
    except ValueError as exc:
        return error(400, "bad_weights", str(exc))
    except OwnerMissingError:
        return not_built(Profile, "B", "sieve.profiles.save")
    log_decision(
        store,
        name,
        "weights",
        token.name,
        before.model_dump(mode="json") if before else None,
        profile.model_dump(mode="json"),
        f"profile {name} replaced by {token.name}",
    )
    return {**profile.model_dump(mode="json"), "warnings": warnings}


@router.patch("/profiles/{name}/weights")
def patch_weights(
    request: Request,
    name: str,
    weights: Annotated[dict[str, float], Body()],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    try:
        profile = load_profile(cfg, name, store, owner_id)
    except OwnerMissingError:
        return not_built(Profile, "B", "sieve.profiles.load")
    if profile is None:
        return error(404, "not_found", f"no profile {name!r}")
    updated = profile.model_copy(update={"weights": weights})
    try:
        _validate_profile_weights(cfg, store, updated, owner_id)
        _save(cfg, updated, store, owner_id)
    except ValueError as exc:
        return error(400, "bad_weights", str(exc))
    log_decision(
        store, name, "weights", token.name, profile.weights, weights, f"weights set by {token.name}"
    )
    return updated


@router.post("/profiles")
def post_profile(
    request: Request,
    body: Annotated[dict[str, Any], Body()],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    """A new profile, cloned from one that already works.

    Cloning rather than starting empty is the whole point: a profile is a set of
    weights that sum to 1 over axes that exist for its modality, plus
    constraints, a shape and a policy. Assembling that from nothing is an
    exercise in reading error messages; starting from the seat next to it and
    changing two numbers is how anybody actually makes one.
    """
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    name = str(body.get("name") or "").strip()
    source = str(body.get("from") or body.get("copy_from") or "").strip()

    if not name:
        return error(400, "bad_request", "a new profile needs a `name`")
    if not _PROFILE_NAME.match(name):
        return error(
            400,
            "bad_request",
            f"{name!r} is not a usable profile name: letters, digits, _ and - only",
        )
    if not source and not body.get("modality"):
        return error(400, "bad_request", "a new profile needs `from` or `modality`")

    control.seed(store, cfg.profiles_dir, owner_id)
    existing = {p.name for p in control.profiles(store, owner_id)}
    original = control.profile(store, source, owner_id)
    if original is None and body.get("modality"):
        original = next(
            (p for p in control.profiles(store, owner_id) if p.modality == body["modality"]), None
        )

    if name in existing:
        # Chains are keyed by name, so two profiles sharing one would overwrite
        # each other's chain row -- the collision phase 1 shipped and had to fix.
        return error(409, "exists", f"a profile named {name!r} already exists")
    if original is None:
        return error(404, "not_found", f"no profile {source!r} to clone")

    updated = original.model_copy(
        update={
            "name": name,
            "purpose": str(body.get("purpose") or f"cloned from {source}"),
        }
    )
    try:
        if "weights" in body:
            updated = Profile.model_validate({**updated.model_dump(), "weights": body["weights"]})
        _validate_profile_weights(cfg, store, updated, owner_id)
        _save(cfg, updated, store, owner_id)
    except ValueError as exc:
        return error(400, "bad_weights", str(exc))
    log_decision(
        store,
        name,
        "policy",
        token.name,
        None,
        updated.model_dump(mode="json"),
        f"created by {token.name}, cloned from {source}",
    )
    return updated


@router.get("/profiles/{name}/settings")
def get_profile_settings(request: Request, name: str, _: Read = None) -> Any:
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    control.seed(store, cfg.profiles_dir, owner_id)
    value = control.settings(store, name, owner_id)
    return value or error(404, "not_found", f"no profile {name!r}")


@router.put("/profiles/{name}/settings")
def put_profile_settings(
    request: Request,
    name: str,
    body: Annotated[dict[str, Any], Body()],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    control.seed(store, cfg.profiles_dir, owner_id)
    before = control.settings(store, name, owner_id)
    if before is None:
        return error(404, "not_found", f"no profile {name!r}")
    try:
        after, warnings = control.update_settings(before, body)
    except (ValidationError, ValueError, TypeError) as exc:
        return error(400, "bad_settings", str(exc))
    found = control.profile(store, name, owner_id)
    assert found is not None
    try:
        _validate_profile_weights(
            cfg,
            store,
            found.model_copy(update={"weights": dict(after.weights)}),
            owner_id,
        )
    except ValueError as exc:
        return error(400, "bad_weights", str(exc))
    control.put_settings(store, name, after, owner_id)
    log_decision(
        store,
        name,
        "policy",
        token.name,
        before.model_dump(mode="json"),
        after.model_dump(mode="json"),
        f"settings set by {token.name}",
    )
    return {**after.model_dump(mode="json"), "warnings": warnings}


@router.get("/cost-multipliers")
def get_cost_multipliers(request: Request, _: Read = None) -> dict[str, float]:
    """The multiplier per router prefix, for the prefixes reachable today.

    A prefix the last pull no longer served is left out: the stored number is
    kept (a configuration export still carries it), but a knob wired to nothing
    does not belong on a screen.
    """
    return control.live_multipliers(store_of(request), owner_of(request))


@router.delete("/cost-multipliers/{prefix:path}")
def delete_cost_multiplier(
    request: Request,
    prefix: str,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    if not store_of(request).delete_cost_multiplier(prefix, owner_of(request)):
        return error(404, "not_found", f"no cost multiplier {prefix!r}")
    return {"deleted": prefix, "actor": token.name}


@router.put("/cost-multipliers")
def put_cost_multipliers(
    request: Request,
    body: Annotated[dict[str, float], Body()],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    store = store_of(request)
    owner_id = owner_of(request)
    before = control.live_multipliers(store, owner_id)
    try:
        after = control.put_multipliers(store, body, owner_id)
    except ValueError as exc:
        return error(400, "bad_multiplier", str(exc))
    for profile in control.profiles(store, owner_id):
        log_decision(
            store,
            profile.name,
            "policy",
            token.name,
            before,
            after,
            f"default cost multipliers set by {token.name}",
        )
    return after


@router.post("/outcomes")
def post_outcome(
    request: Request, outcome: Outcome, token: Annotated[Token, Depends(require_scope("telemetry"))]
) -> Any:
    store = store_of(request)
    owner_id = owner_of(request)
    if control.profile(store, outcome.profile, owner_id) is None:
        return error(404, "not_found", f"no profile {outcome.profile!r}")
    control.add_outcome(store, outcome, owner_id)
    return {"accepted": 1}


@router.get("/profiles/{name}/experience")
def get_experience(request: Request, name: str, _: Read = None) -> Any:
    store = store_of(request)
    owner_id = owner_of(request)
    if control.profile(store, name, owner_id) is None:
        return error(404, "not_found", f"no profile {name!r}")
    return control.experience(store, name, None, owner_id)


#: how many models below the line the page may show under "show more"
NEXT_UP = 10


def listed(
    rank: Any,
    names: dict[str, str],
    caps: dict[str, Any] | None = None,
    needs: list[str] | None = None,
) -> dict[str, Any]:
    """One row of a list, as a page draws it: who, how it scores, what it can do."""
    from sieve.scoring.select import abilities, lacks

    capability = (caps or {}).get(rank.model_id)
    return {
        "id": rank.model_id,
        "name": names.get(rank.model_id) or rank.model_id,
        "local_ids": rank.local_ids,
        "score": rank.final,
        "abilities": abilities(capability),
        "lacks": lacks(capability, needs or []),
    }


@router.post("/profiles/{name}/preview")
def preview(
    request: Request, name: str, body: Annotated[dict[str, Any], Body()], _: Read = None
) -> Any:
    """What these settings would ship, without shipping it.

    The answer is the list itself -- as `sieve.scoring.select` makes it, with
    pins, removals, a manual list and needs applied -- the ten behind it, the
    removed rows (so they can be put back), and `pool`: every reachable model
    of this modality with its score and abilities, which is what a hand-made
    list is picked from. Meant to be called on every slider move: it reweighs
    the axis values the stored ranking already holds rather than rebuilding the
    observation table, and only a profile with no stored ranking at all pays
    for a full one.
    """
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    found = control.profile(store, name, owner_id)
    current = control.settings(store, name, owner_id)
    if found is None or current is None:
        return error(404, "not_found", f"no profile {name!r}")
    try:
        proposed, warnings = control.update_settings(current, body)
    except (ValidationError, ValueError, TypeError) as exc:
        return error(400, "bad_settings", str(exc))
    ranking = store.ranking(name, None, owner_id)
    if ranking is None:
        candidate = found.model_copy(update={"weights": dict(proposed.weights)})
        ranking = rank_profile(
            cfg, store, candidate, deps=EngineDeps(), snapshot=store.latest_snapshot() or "none"
        )
        store.put_ranking(ranking, owner_id)
    from sieve.scoring.select import behind, select

    ranking = control.rerank_cached(ranking, proposed.weights, proposed.prefix_weights)
    names = store.model_names(found.modality)
    caps = control.capability_map(store, found.modality)
    needs = [str(need) for need in proposed.needs]
    chosen = select(ranking.ranks, proposed, caps)
    pinned = set(proposed.pinned) if proposed.mode == "auto" else set()
    reachable = [r for r in ranking.ranks if r.position > 0]
    by_id = {r.model_id: r for r in reachable}

    def row(rank: Any) -> dict[str, Any]:
        return {**listed(rank, names, caps, needs), "pinned": rank.model_id in pinned}

    # a listed or pinned model that a need keeps out is still the person's
    # choice, so it is shown in place and marked, not silently dropped
    held = set(proposed.manual if proposed.mode == "manual" else proposed.pinned)
    return {
        "profile": name,
        "mode": proposed.mode,
        "ship": proposed.ship,
        "models": [row(r) for r in chosen.rows],
        "blocked": [row(r) for r in chosen.failed_needs if r.model_id in held],
        "next": [row(r) for r in behind(ranking.ranks, proposed, chosen, caps)[:NEXT_UP]],
        "removed": [row(by_id[m]) for m in proposed.removed if m in by_id],
        "missing": [{"id": m, "name": names.get(m) or m} for m in chosen.missing],
        "failed_needs": len({r.model_id for r in chosen.failed_needs}),
        "pool": [row(r) for r in reachable],
        "settings": proposed,
        "computed_at": ranking.computed_at,
        "warnings": warnings,
    }


@router.get("/profiles/{name}/history")
def get_profile_history(request: Request, name: str, _: Read = None) -> list[dict[str, Any]]:
    return [
        {"who": d.actor, "when": d.at, "what": d.reason, "before": d.before, "after": d.after}
        for d in store_of(request).decisions(profile=name)
    ]


@router.patch("/profiles/{name}")
def rename_profile(
    request: Request,
    name: str,
    body: Annotated[dict[str, str], Body()],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    """Change a profile's name, its purpose, or both.

    `purpose` alone is a real patch and does not need a `name` beside it: asking
    for the name back just to fix a sentence is how descriptions end up never
    being fixed.
    """
    store = store_of(request)
    owner_id = owner_of(request)
    new = str(body.get("name") or "").strip()
    purpose = body.get("purpose")
    if not new and purpose is None:
        return error(400, "bad_request", "give a new `name`, a new `purpose`, or both")

    if purpose is not None:
        before = control.profile(store, name, owner_id)
        if before is None:
            return error(404, "not_found", f"no profile {name!r}")
        rewritten = control.set_purpose(store, name, str(purpose), owner_id)
        log_decision(
            store,
            name,
            "policy",
            token.name,
            before.model_dump(mode="json"),
            rewritten.model_dump(mode="json"),
            f"purpose of {name} rewritten by {token.name}",
        )
        if not new:
            return rewritten

    if not _PROFILE_NAME.match(new):
        return error(400, "bad_request", "a valid new name is required")
    held = control.profile(store, name, owner_id)
    if held is None:
        return error(404, "not_found", f"no profile {name!r}")
    try:
        control.rename(store, name, new, owner_id)
    except ValueError:
        return error(409, "exists", f"a profile named {new!r} already exists")
    after = control.profile(store, new, owner_id)
    log_decision(
        store,
        new,
        "policy",
        token.name,
        held.model_dump(mode="json"),
        after.model_dump(mode="json") if after else None,
        f"renamed {name} to {new}",
    )
    return after


@router.delete("/profiles/{name}")
def delete_profile(
    request: Request,
    name: str,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
    force: bool = False,
) -> Any:
    store = store_of(request)
    owner_id = owner_of(request)
    if control.profile(store, name, owner_id) is None:
        return error(404, "not_found", f"no profile {name!r}")
    # A profile is "in use" when it was actually applied, not merely when a
    # write connector exists. The chain is the record of an apply: without one
    # no combo was ever seated under this name, so a profile made and thought
    # better of deletes cleanly. Asking every router what it holds is a network
    # call per connector on a delete, so the chain is the test.
    if not force and store.chain(name, owner_id) is not None:
        return error(
            409,
            "in_use",
            f"{name!r} has a live chain a write connector may still hold as a combo; use ?force=1",
        )
    control.delete(store, name, owner_id)
    return {"deleted": name, "actor": token.name}


@router.post("/profiles/{name}/apply")
def apply_profile(
    request: Request, name: str, token: Annotated[Token, Depends(require_scope("apply"))]
) -> Any:
    from sieve.connectors.loop import log_applied, ship

    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    found = control.profile(store, name, owner_id)
    selected = control.settings(store, name, owner_id)
    if found is None or selected is None:
        return error(404, "not_found", f"no profile {name!r}")
    found = control.tuned(found, selected)
    ranking = rank_profile(
        cfg, store, found, deps=EngineDeps(), snapshot=store.latest_snapshot() or "none"
    )
    chain = control.chain_for(store, name, ranking.ranks, None, owner_id)
    if chain is None:
        return error(409, "empty_list", "no reachable models remain")
    store.put_ranking(ranking, owner_id)
    store.put_chain(chain, owner_id)
    results = []
    combos: list[str] = []
    for connector in store.connectors(owner_id):
        if connector.write:
            outcome = ship(store, connector, [chain])
            results.append(outcome)
            combos.extend(str(c) for c in (outcome.detail.get("combos") or []))
            log_applied(store, connector, [chain], token.name)
    names = store.model_names(found.modality)
    return {
        "chain": chain,
        # what a person needs to read back: the combo this went out as, and
        # the list it now holds. A page that says "shipped" and cannot name
        # what it shipped is asking to be trusted.
        "combos": sorted(set(combos)),
        "models": [
            {"id": model_id, "name": names.get(model_id) or model_id}
            for model_id in [chain.primary, *chain.fallbacks]
        ],
        "shipped_at": chain.computed_at,
        "results": results,
    }


@router.post("/profiles/{name}/evaluate")
def evaluate(request: Request, name: str, authorization: Auth = None, _: Read = None) -> Any:
    """Dry run: ranking, chain and the decision that would be taken. Nothing stored."""
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    try:
        profile = load_profile(cfg, name, store, owner_id)
    except OwnerMissingError:
        return not_built(Ranking, "B", "sieve.profiles.load")
    if profile is None:
        return error(404, "not_found", f"no profile {name!r}")
    deps = EngineDeps()
    snapshot = store.latest_snapshot() or "none"
    ranking = rank_profile(cfg, store, profile, deps=deps, snapshot=snapshot)
    # the fourth SSE kind: a watching agent sees the list move, not only the
    # decision that followed it
    events.publish(
        "ranking",
        {
            "profile": name,
            "snapshot": ranking.snapshot,
            "ranked": len([r for r in ranking.ranks if r.position > 0]),
            "leader": next((r.model_id for r in ranking.ranks if r.position > 0), None),
        },
    )
    policy = deps.policy
    chain = decision = None
    if policy is not None:
        chain, decision = policy.decide(
            profile,
            store.chain(name, owner_id),
            ranking,
            datetime.now(UTC),
            control.capability_map(store, profile.modality) if profile.needs else None,
        )
        if decision is not None:
            decision = decision.model_copy(update={"actor": actor_for(request, authorization)})
    return {
        "ranking": ranking,
        "chain": chain,
        "decision": decision,
        "warnings": sorted(set(deps.warnings)),
    }


# --------------------------------------------------------------------------- #
# rankings, chains, recommendations
# --------------------------------------------------------------------------- #


def ranking_for(request: Request, profile: str) -> Ranking | JSONResponse:
    """The stored ranking, or one computed on the spot.

    A fresh install has pulled but not yet run `sieve plan --store`, and a
    gateway asking for a recommendation then should get an answer rather than a
    404 telling it to run a command it has never heard of.
    """
    store, cfg = store_of(request), config_of(request)
    owner_id = owner_of(request)
    stored = store.ranking(profile, None, owner_id)
    if stored is not None:
        return stored
    try:
        found = load_profile(cfg, profile, store, owner_id)
    except OwnerMissingError:
        return not_built(Ranking, "B", "sieve.profiles.load")
    if found is None:
        return error(404, "not_found", f"no profile {profile!r}")
    deps = EngineDeps()
    computed = rank_profile(
        cfg, store, found, deps=deps, snapshot=store.latest_snapshot() or "none"
    )
    if deps.warnings and not computed.ranks:
        return not_built(Ranking, "A", "sieve.scoring.weigh")
    return computed


@router.get("/rankings/{profile}")
def get_ranking(request: Request, profile: str, _: Read = None) -> Any:
    return ranking_for(request, profile)


@router.get("/chains/{profile}")
def get_chain(request: Request, profile: str, _: Read = None) -> Any:
    chain = store_of(request).chain(profile, owner_of(request))
    return chain or error(404, "not_found", f"no chain for {profile!r}; run sieve plan --store")


@router.get("/recommend")
def recommend(
    request: Request,
    profile: str,
    n: int = 3,
    reachable_only: bool = True,
    _: Read = None,
) -> Any:
    """The chain this profile shipped, in the order it shipped it.

    The same answer the gateway is routing on -- not a fresh opinion. A caller
    asking what to call and a router already holding a combo must agree, or the
    recommendation is describing a list nobody is using. Only a profile that
    has never shipped falls back to its ranking.
    """
    store = store_of(request)
    owner_id = owner_of(request)
    chain = store.chain(profile, owner_id)

    if chain is not None:
        ordered = [chain.primary, *chain.fallbacks][:n]
        return {
            "profile": profile,
            "models": [
                {
                    "id": model_id,
                    "local_ids": chain.local.get(model_id, []),
                    "final": None,
                    "confidence": None,
                }
                for model_id in ordered
            ],
            "computed_at": chain.computed_at,
        }

    found = ranking_for(request, profile)
    if isinstance(found, JSONResponse):
        return found

    models: list[dict[str, Any]] = []
    for rank in found.ranks:
        if rank.position == 0:
            continue
        if reachable_only and not rank.reachable:
            continue
        models.append(
            {
                "id": rank.model_id,
                "local_ids": rank.local_ids,
                "final": rank.final,
                "confidence": rank.confidence,
            }
        )
        if len(models) >= n:
            break
    return {"profile": profile, "models": models, "computed_at": found.computed_at}


@router.post("/apply")
def post_apply(
    request: Request,
    body: Annotated[dict[str, Any], Body()],
    token: Annotated[Token, Depends(require_scope("apply"))],
) -> Any:
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of(request)
    wanted = body.get("profiles") or []
    targets = body.get("targets") or None
    chains = [c for c in store.chains(owner_id) if not wanted or c.profile in wanted]
    if not chains:
        return error(404, "not_found", "no computed chains to apply; run sieve plan --store")
    results = apply_targets(
        cfg, chains, targets=targets, dry_run=False, actor=token.name, store=store
    )
    events.publish("apply", {"targets": [r.target for r in results], "actor": token.name})
    return results


# --------------------------------------------------------------------------- #
# telemetry, decisions, sources, inventory
# --------------------------------------------------------------------------- #


@router.post("/telemetry")
def post_telemetry(
    request: Request,
    body: list[TelemetryEvent],
    token: Annotated[Token, Depends(require_scope("telemetry"))],
) -> dict[str, int]:
    """Accept a batch of call outcomes. The caller is a gateway, not a person.

    A gateway knows its **own** ids and nothing else, so `model` may be either a
    canonical id or a local one and is resolved here. An id that resolves to
    nothing is still stored under the name it arrived with: dropping it would
    lose evidence, and it shows up on the Sources screen as an unmatched id for
    a person to alias.

    CONTRACTS section 4: telemetry is pruned to 30 days, and a write is the
    natural moment -- the table only grows when someone is writing to it.
    """
    store = store_of(request)

    canonical: dict[str, str] = {}
    for model_id, local_ids in store.local_ids(owner_of(request)).items():
        for local_id in local_ids:
            canonical[local_id] = model_id

    resolved = [
        event
        if event.model not in canonical
        else event.model_copy(update={"model": canonical[event.model]})
        for event in body
    ]
    accepted = store.add_telemetry(resolved)
    pruned = store.prune_telemetry()
    return {"accepted": accepted, "pruned": pruned}


@router.get("/health")
def get_health(
    request: Request,
    window: Literal["24h", "7d"] = "24h",
    reachable: bool = True,
    _: Read = None,
) -> list[HealthRow]:
    """What the gateway's own traffic says, per model. The Pulse screen reads this.

    `health` is the number the ranking multiplies by; the rest is why it is that
    number. `series` is one health value per day for the sparkline the Rankings
    screen was built with and never got -- a day with no calls is null, not 1.0,
    because a flat line of ones would claim a model was healthy on a day nobody
    tried it.

    Reachable models only by default: a model you cannot call has no traffic to
    report, and listing it with empty figures buries the ones that do.
    """
    store = store_of(request)
    now = datetime.now(UTC)
    hours = 24 if window == "24h" else 24 * 7

    events = store.telemetry(since=now - timedelta(days=max(7, hours // 24)))
    figures = pulse_of(events, now, hours=hours)
    health_now = health_of(events, now)
    series = health_series(events, now)
    local = store.local_ids(owner_of(request))

    names: set[str] = set(figures) | set(health_now)
    if reachable:
        names &= set(local)

    out: list[HealthRow] = []
    for model_id in sorted(names):
        out.append(
            HealthRow(
                model_id=model_id,
                local_ids=local.get(model_id, []),
                health=health_now.get(model_id, 1.0),
                series=series.get(model_id, []),
                window=window,
                **(figures.get(model_id) or {}),  # type: ignore[arg-type]
            )
        )
    return out


@router.get("/diff")
def get_diff(request: Request, _: Read = None) -> list[TargetDiff]:
    """What every configured target holds now, per profile.

    The Chains screen diffs against this rather than against the last `apply`
    decision. A decision says what Sieve believes it wrote; a target that has
    drifted underneath -- edited by hand, rolled back, written by something
    else -- shows no difference at all against that belief, which is exactly
    when a diff needs to be right.
    """
    from sieve import plugins

    cfg = config_of(request)
    store = store_of(request)
    profiles = _profiles_module().load_profiles(cfg.profiles_dir)
    chains = [c for c in (store.chain(p.name) for p in profiles) if c]
    out: list[TargetDiff] = []
    for name, target_cfg in sorted(cfg.targets.items()):
        try:
            target = plugins.load(plugins.TARGETS, target_cfg.kind)
        except (LookupError, ImportError) as exc:
            out.append(
                TargetDiff(target=name, kind=target_cfg.kind, supported=False, error=str(exc))
            )
            continue
        try:
            out.append(
                TargetDiff(
                    target=name,
                    kind=target_cfg.kind,
                    current=target.current(target_cfg),
                    planned=target.plan(target_cfg, chains),
                )
            )
        except NotImplementedError as exc:
            # a one-way target: it cannot say what the receiver holds, and an
            # empty dict here would read as "everything is a change"
            out.append(
                TargetDiff(target=name, kind=target_cfg.kind, supported=False, error=str(exc))
            )
        except Exception as exc:
            out.append(
                TargetDiff(
                    target=name,
                    kind=target_cfg.kind,
                    supported=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return out


@router.get("/leaderboard")
def get_leaderboard(
    request: Request,
    modality: Modality,
    metric: str | None = None,
    _: Read = None,
) -> Leaderboard:
    """The ranking for one modality, best first, deduplicated.

    The Field scatter needs a quality axis *and* a cost. Media models almost
    never have both -- 5 of 313 scored media models carry a price against 60 of
    60 LLMs -- so `scatter_ok` says whether that chart can answer anything here.
    Where it cannot, this ranking answers the question that can be answered:
    which of these is best.
    """
    store = store_of(request)
    # `board` only reads, so it is handed the shared view rather than a copy.
    view = store.cache.view(modality)
    computed = leaderboard.board(modality, view.obs, view.models, set(view.obs.prices), metric)
    return Leaderboard(
        modality=computed.modality,
        metric=computed.metric,
        metrics=list(leaderboard.metrics_for(modality)),
        rows=[
            BoardRow(
                model_id=r.model_id,
                name=r.name,
                creator=r.creator,
                value=r.value,
                merged=r.merged,
            )
            for r in computed.rows
        ],
        scored=computed.scored,
        priced=computed.priced,
        priced_share=computed.priced_share,
        scatter_ok=computed.scatter_ok,
        low=computed.low,
        high=computed.high,
        reason=computed.reason,
        sources=computed.sources,
    )


@router.get("/decisions")
def get_decisions(
    request: Request,
    profile: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, le=1000),
    _: Read = None,
) -> list[Decision]:
    return store_of(request).decisions(profile=profile, kind=kind, since=since, limit=limit)


@router.get("/status")
def get_status(request: Request, _: Read = None) -> dict[str, Any]:
    """When Sieve last looked, and how often it looks. Cheap enough for every page.

    Three times, because they answer different questions and collapse badly:

    - `pulled_at` -- the last pull of any source (a snapshot is written for
      every pull, including one that found nothing new).
    - `ran_at` -- the last decision the hourly loop recorded. `sieve run`
      writes one per profile on every run, a hold included, precisely so that
      "running and changing nothing" is visible and distinct from "not running".
    - `schedule` -- the cadence of `full`, read from the `schedules` table.
      It used to be `[schedule] pull` out of `sieve.toml`, which said "hourly"
      on a box whose timer had been overridden to 04:30 daily: a status line
      that is confidently wrong is worse than none.

    `runs` and `schedules` carry the rest: what is going now, what finished
    last, and when each of the four steps is next due.

    `/v1/sources` could not serve this: it takes `MAX(pulled_at)` over every
    observation of every source, which is seconds of scanning on a large store,
    and it does not move on a pull that added nothing.
    """
    cfg, store = config_of(request), store_of(request)
    scheduled = store.db.execute(
        "SELECT at FROM decisions WHERE actor = 'schedule' ORDER BY at DESC LIMIT 1"
    ).fetchone()
    any_decision = store.decisions(limit=1)
    ran_at = scheduled["at"] if scheduled else (any_decision[0].at if any_decision else None)
    pulled_at = store.latest_snapshot_at()
    # Pulse needs to tell "no calls in this window" from "no calls ever". Its
    # empty state used to say "nothing has reported a call yet" over a store
    # holding four thousand calls, because none of them were from the last day.
    calls = store.db.execute("SELECT COUNT(*) AS n, MAX(at) AS last FROM telemetry").fetchone()
    from sieve import runs as runs_module
    from sieve.api.auth import gate_identity

    schedules = runs_module.schedules_block(store)
    # Who gate says is in front of this request, or None. The web pages ask
    # here so they can stop showing a token box to somebody already signed in;
    # `/v1/me` (AMS-28) will answer the same question in more detail.
    signed_in = gate_identity(request)
    return {
        "pulled_at": pulled_at.isoformat() if pulled_at else None,
        "ran_at": ran_at.isoformat() if isinstance(ran_at, datetime) else ran_at,
        "schedule": runs_module.cadence_of(schedules),
        "runs": runs_module.status_block(store),
        "schedules": schedules,
        "sources_enabled": sum(1 for s in cfg.sources.values() if s.enabled),
        "telemetry_calls": calls["n"],
        "telemetry_at": calls["last"],
        "user": (
            {
                "name": signed_in.name,
                "scopes": sorted(signed_in.scopes),
                # gate's own roles collapse onto one scope set here, so this
                # says what the person may do rather than inventing a title.
                "role": "editor" if signed_in.allows("profiles:write") else "viewer",
            }
            if signed_in
            else None
        ),
    }


@router.get("/sources")
def get_sources(request: Request, _: Read = None) -> list[dict[str, Any]]:
    from sieve import plugins

    cfg, store = config_of(request), store_of(request)
    available = set(plugins.names(plugins.SOURCES))

    # One pass over each table rather than one query per source. A price is a
    # pull too: `openrouter` supplies prices and no observations, and counting
    # observations alone printed "0 observations, last pull never" beside a
    # source that had pulled forty-eight thousand prices an hour earlier.
    observed = {
        r["source"]: r
        for r in store.db.execute(
            "SELECT source, COUNT(*) AS rows, MAX(pulled_at) AS last FROM observations"
            " GROUP BY source"
        )
    }
    priced = {
        r["source"]: r
        for r in store.db.execute(
            "SELECT source, COUNT(*) AS rows, MAX(observed_at) AS last FROM prices GROUP BY source"
        )
    }

    rows: list[dict[str, Any]] = []
    for name, source_cfg in cfg.sources.items():
        obs, price = observed.get(name), priced.get(name)
        stamps = [s for s in (obs and obs["last"], price and price["last"]) if s]
        rows.append(
            {
                "name": name,
                "enabled": source_cfg.enabled,
                "registered": name in available,
                "needs_key": bool(source_cfg.key_env),
                "key_present": source_cfg.key() is not None,
                "rows": obs["rows"] if obs else 0,
                "prices": price["rows"] if price else 0,
                "last_price": price["last"] if price else None,
                # whichever table it last wrote to; `None` only if it never wrote to either
                "last_pull": max(stamps, key=datetime.fromisoformat) if stamps else None,
                "modalities": source_cfg.modalities,
            }
        )
    return rows


@router.get("/sources/{name}/fields")
def get_source_fields(request: Request, name: str, _: Read = None) -> Any:
    cfg, store = config_of(request), store_of(request)
    if name not in cfg.sources:
        return error(404, "not_found", f"no source {name!r}")
    return [
        {"field": row["field"], "rows": row["rows"]}
        for row in store.db.execute(
            "SELECT field,COUNT(*) AS rows FROM observations WHERE source=? "
            "GROUP BY field ORDER BY field",
            (name,),
        )
    ]


@router.post("/sources/{name}/pull")
def post_pull(
    request: Request,
    name: str,
    token: Annotated[Token, Depends(require_scope("apply"))],
    force: bool = False,
) -> Any:
    from sieve import plugins
    from sieve.http import client as http_client

    cfg, store = config_of(request), store_of(request)
    source_cfg = cfg.sources.get(name)
    if source_cfg is None:
        return error(404, "not_found", f"no source {name!r}")
    if not source_cfg.enabled and not force:
        return error(409, "source_disabled", f"source {name!r} is disabled; use force=true to pull")
    try:
        source = plugins.load(plugins.SOURCES, name)
    except (LookupError, ImportError) as exc:
        return error(501, "not_built", str(exc))
    job = uuid.uuid4().hex[:12]
    result = source.pull(source_cfg, http_client())
    snapshot = store.new_snapshot(source_rows=len(result.observations))
    store.upsert_models(result.models)
    added = store.add_observations(result.observations, snapshot=snapshot)
    intake = store.add_prices(result.prices)
    warnings = list(result.warnings)
    warnings += [
        f"price refused, that unit cannot describe that modality: {refusal}"
        for refusal in intake.refused
    ]
    log_decision(
        store, name, "pull", token.name, None, {"added": added}, f"pulled {name}: {added} new rows"
    )
    events.publish("pull", {"source": name, "added": added, "job": job})
    return {"job": job, "source": name, "added": added, "warnings": warnings}


@router.get("/inventory")
def get_inventory(
    request: Request,
    unmatched: bool | None = None,
    include_stale: bool = False,
    _: Read = None,
) -> list[Reachable]:
    """What the routers serve, as of each one's last successful pull.

    `include_stale=1` adds the rows a later pull stopped listing, each carrying
    `stale: true` and the `seen_at` of the last time it was really there.
    """
    return store_of(request).reachable(unmatched=unmatched, include_stale=include_stale)


@router.get("/aliases")
def get_aliases(request: Request, _: Read = None) -> list[dict[str, str]]:
    return store_of(request).alias_rows()


@router.delete("/aliases/{alias:path}")
def delete_alias(
    request: Request,
    alias: str,
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
    modality: Modality = "llm",
) -> Any:
    if not store_of(request).delete_alias(alias, modality):
        return error(404, "not_found", f"no alias {alias!r} for {modality}")
    return {"deleted": alias, "modality": modality, "actor": token.name}


@router.put("/aliases")
def put_alias(
    request: Request,
    body: Annotated[dict[str, str], Body()],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
) -> Any:
    store = store_of(request)
    alias, model_id = body.get("alias"), body.get("model_id")
    modality = body.get("modality", "llm")
    if not alias or not model_id:
        return error(400, "bad_request", "alias and model_id are required")
    if not store.db.execute(
        "SELECT 1 FROM models WHERE id=? AND modality=?", (model_id, modality)
    ).fetchone():
        return error(400, "unknown_model", f"no model {model_id!r} for modality {modality!r}")
    store.put_alias(alias, modality, model_id)  # type: ignore[arg-type]
    store.db.execute(
        "UPDATE reachable SET model_id=? WHERE local_id=? AND model_id IS NULL",
        (model_id, alias),
    )
    store.db.commit()
    return {"alias": alias, "model_id": model_id, "actor": token.name}


@router.get("/events")
async def get_events(_: Read = None) -> StreamingResponse:
    return StreamingResponse(
        events.stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


_CONTRACT_SHAPES = (ModelRef, Axis, Profile, Ranking, Chain, Decision, TargetResult)
