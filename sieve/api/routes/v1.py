"""`/v1` — the whole control surface of CONTRACTS section 6.

A route whose owner module has not landed yet answers 501 with the error
envelope and, under `shape`, the JSON schema of what it will return, so the
web can be built against it before the code behind it exists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from importlib import import_module
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from sieve.api.auth import Token, actor_for, require, require_read
from sieve.api.sse import events
from sieve.config import Config
from sieve.contracts import (
    Axis,
    Chain,
    Decision,
    Modality,
    ModelRef,
    Profile,
    Ranking,
    Reachable,
    TargetResult,
    TelemetryEvent,
)
from sieve.engine import Deps as EngineDeps
from sieve.engine import OwnerMissingError, apply_targets, rank_profile
from sieve.store import Store

router = APIRouter(prefix="/v1")

Read = Annotated[Token | None, Depends(require_read())]
Auth = Annotated[str | None, Header()]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def store_of(request: Request) -> Store:
    store: Store = request.app.state.store
    return store


def config_of(request: Request) -> Config:
    cfg: Config = request.app.state.config
    return cfg


def error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}, **extra}
    )


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


def load_profile(cfg: Config, name: str) -> Profile | None:
    for profile in _profiles_module().load_profiles(cfg.profiles_dir):
        if profile.name == name:
            return profile
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


@router.get("/axes")
def get_axes(
    request: Request,
    modality: Modality | None = None,
    _: Read = None,
) -> Any:
    cfg = config_of(request)
    try:
        axes_load = import_module("sieve.axes.load")
    except ModuleNotFoundError:
        return not_built(Axis, "A", "sieve.axes.load")
    axes = list(axes_load.load_all_axes(cfg.axes_dir))
    return [a for a in axes if modality is None or a.modality == modality]


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
    store = store_of(request)
    local = store.local_ids()
    models = store.models(modality)
    if q:
        needle = q.lower()
        models = [m for m in models if needle in m.id.lower() or needle in m.name.lower()]
    if reachable is not None:
        models = [m for m in models if (m.id in local) is reachable]
    prices: dict[str, dict[str, Any]] = {}
    for wanted in {m.modality for m in models}:
        for model_id, price in store.latest_prices(wanted).items():
            prices[model_id] = price.model_dump(mode="json")
    rows = [
        {
            **m.model_dump(mode="json"),
            "reachable": m.id in local,
            "local_ids": local.get(m.id, []),
            "price": prices.get(m.id),
        }
        for m in models
    ]
    return page(rows, limit, cursor)


@router.get("/models/{model_id:path}")
def get_model(request: Request, model_id: str, _: Read = None) -> Any:
    store = store_of(request)
    for model in store.models():
        if model.id == model_id:
            local = store.local_ids()
            return {
                **model.model_dump(mode="json"),
                "reachable": model.id in local,
                "local_ids": local.get(model.id, []),
                "observations": [
                    o.model_dump(mode="json") for o in store.observations_for(model_id)
                ],
            }
    return error(404, "not_found", f"no model {model_id!r}")


# --------------------------------------------------------------------------- #
# profiles
# --------------------------------------------------------------------------- #


@router.get("/profiles")
def get_profiles(request: Request, modality: Modality | None = None, _: Read = None) -> Any:
    cfg = config_of(request)
    try:
        profiles = list(_profiles_module().load_profiles(cfg.profiles_dir))
    except OwnerMissingError:
        return not_built(Profile, "B", "sieve.profiles.load")
    return [p for p in profiles if modality is None or p.modality == modality]


@router.get("/profiles/{name}")
def get_profile(request: Request, name: str, _: Read = None) -> Any:
    cfg = config_of(request)
    try:
        profile = load_profile(cfg, name)
    except OwnerMissingError:
        return not_built(Profile, "B", "sieve.profiles.load")
    return profile or error(404, "not_found", f"no profile {name!r}")


def _save(cfg: Config, profile: Profile) -> None:
    try:
        saver = import_module("sieve.profiles.save")
    except ModuleNotFoundError as exc:
        raise OwnerMissingError("sieve.profiles.save", "B") from exc
    saver.save_profile(cfg.profiles_dir, profile)


@router.put("/profiles/{name}")
def put_profile(
    request: Request,
    name: str,
    profile: Profile,
    token: Annotated[Token, Depends(require("profiles:write"))],
) -> Any:
    cfg, store = config_of(request), store_of(request)
    if profile.name != name:
        return error(400, "bad_request", "the body's name must match the path")
    try:
        before = load_profile(cfg, name)
        _save(cfg, profile)
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
    return profile


@router.patch("/profiles/{name}/weights")
def patch_weights(
    request: Request,
    name: str,
    weights: Annotated[dict[str, float], Body()],
    token: Annotated[Token, Depends(require("profiles:write"))],
) -> Any:
    cfg, store = config_of(request), store_of(request)
    try:
        profile = load_profile(cfg, name)
    except OwnerMissingError:
        return not_built(Profile, "B", "sieve.profiles.load")
    if profile is None:
        return error(404, "not_found", f"no profile {name!r}")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        return error(400, "bad_weights", f"weights must sum to 1 +/- 0.001, got {total:.4f}")
    updated = profile.model_copy(update={"weights": weights})
    _save(cfg, updated)
    log_decision(
        store, name, "weights", token.name, profile.weights, weights, f"weights set by {token.name}"
    )
    return updated


@router.patch("/profiles/{name}/policy")
def patch_policy(
    request: Request,
    name: str,
    policy: Annotated[dict[str, Any], Body()],
    token: Annotated[Token, Depends(require("profiles:write"))],
) -> Any:
    cfg, store = config_of(request), store_of(request)
    try:
        profile = load_profile(cfg, name)
    except OwnerMissingError:
        return not_built(Profile, "B", "sieve.profiles.load")
    if profile is None:
        return error(404, "not_found", f"no profile {name!r}")
    merged = profile.policy.model_copy(update=policy)
    updated = profile.model_copy(update={"policy": merged})
    _save(cfg, updated)
    log_decision(
        store,
        name,
        "policy",
        token.name,
        profile.policy.model_dump(mode="json"),
        merged.model_dump(mode="json"),
        f"policy set by {token.name}",
    )
    return updated


@router.post("/profiles/{name}/evaluate")
def evaluate(request: Request, name: str, authorization: Auth = None, _: Read = None) -> Any:
    """Dry run: ranking, chain and the decision that would be taken. Nothing stored."""
    cfg, store = config_of(request), store_of(request)
    try:
        profile = load_profile(cfg, name)
    except OwnerMissingError:
        return not_built(Ranking, "B", "sieve.profiles.load")
    if profile is None:
        return error(404, "not_found", f"no profile {name!r}")
    deps = EngineDeps()
    snapshot = store.latest_snapshot() or "none"
    ranking = rank_profile(cfg, store, profile, deps=deps, snapshot=snapshot)
    policy = deps.policy
    chain = decision = None
    if policy is not None:
        chain, decision = policy.decide(profile, store.chain(name), ranking, datetime.now(UTC))
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


@router.get("/rankings/{profile}")
def get_ranking(request: Request, profile: str, _: Read = None) -> Any:
    store = store_of(request)
    stored = store.ranking(profile)
    if stored is not None:
        return stored
    cfg = config_of(request)
    try:
        found = load_profile(cfg, profile)
    except OwnerMissingError:
        return not_built(Ranking, "B", "sieve.profiles.load")
    if found is None:
        return error(404, "not_found", f"no profile {profile!r}")
    deps = EngineDeps()
    ranking = rank_profile(cfg, store, found, deps=deps, snapshot=store.latest_snapshot() or "none")
    if deps.warnings and not ranking.ranks:
        return not_built(Ranking, "A", "sieve.scoring.weigh")
    return ranking


@router.get("/chains/{profile}")
def get_chain(request: Request, profile: str, _: Read = None) -> Any:
    chain = store_of(request).chain(profile)
    return chain or error(404, "not_found", f"no chain for {profile!r}; run sieve plan --store")


@router.get("/recommend")
def recommend(
    request: Request,
    profile: str,
    n: int = 3,
    reachable_only: bool = True,
    _: Read = None,
) -> Any:
    store = store_of(request)
    chain = store.chain(profile)
    ranking = store.ranking(profile)
    if chain is None and ranking is None:
        return error(404, "not_found", f"nothing computed for {profile!r} yet")
    models: list[dict[str, Any]] = []
    if ranking is not None:
        for rank in ranking.ranks:
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
    elif chain is not None:
        for model_id in [chain.primary, *chain.fallbacks][:n]:
            models.append(
                {
                    "id": model_id,
                    "local_ids": chain.local.get(model_id, []),
                    "final": None,
                    "confidence": None,
                }
            )
    return {
        "profile": profile,
        "models": models,
        "computed_at": (ranking.computed_at if ranking else chain.computed_at),  # type: ignore[union-attr]
    }


@router.post("/apply")
def post_apply(
    request: Request,
    body: Annotated[dict[str, Any], Body()],
    token: Annotated[Token, Depends(require("apply"))],
) -> Any:
    cfg, store = config_of(request), store_of(request)
    wanted = body.get("profiles") or []
    targets = body.get("targets") or None
    chains = [c for c in store.chains() if not wanted or c.profile in wanted]
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
    token: Annotated[Token, Depends(require("telemetry"))],
) -> dict[str, int]:
    return {"accepted": store_of(request).add_telemetry(body)}


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


@router.get("/sources")
def get_sources(request: Request, _: Read = None) -> list[dict[str, Any]]:
    from sieve import plugins

    cfg, store = config_of(request), store_of(request)
    available = set(plugins.names(plugins.SOURCES))
    rows: list[dict[str, Any]] = []
    for name, source_cfg in cfg.sources.items():
        row = store.db.execute(
            "SELECT COUNT(*) AS rows, MAX(pulled_at) AS last FROM observations WHERE source=?",
            (name,),
        ).fetchone()
        rows.append(
            {
                "name": name,
                "enabled": source_cfg.enabled,
                "registered": name in available,
                "needs_key": bool(source_cfg.key_env),
                "key_present": source_cfg.key() is not None,
                "rows": row["rows"],
                "last_pull": row["last"],
                "modalities": source_cfg.modalities,
            }
        )
    return rows


@router.post("/sources/{name}/pull")
def post_pull(
    request: Request,
    name: str,
    token: Annotated[Token, Depends(require("apply"))],
) -> Any:
    from sieve import plugins
    from sieve.http import client as http_client

    cfg, store = config_of(request), store_of(request)
    source_cfg = cfg.sources.get(name)
    if source_cfg is None:
        return error(404, "not_found", f"no source {name!r}")
    try:
        source = plugins.load(plugins.SOURCES, name)
    except (LookupError, ImportError) as exc:
        return error(501, "not_built", str(exc))
    job = uuid.uuid4().hex[:12]
    result = source.pull(source_cfg, http_client())
    snapshot = store.new_snapshot(source_rows=len(result.observations))
    store.upsert_models(result.models)
    added = store.add_observations(result.observations, snapshot=snapshot)
    store.add_prices(result.prices)
    log_decision(
        store, name, "pull", token.name, None, {"added": added}, f"pulled {name}: {added} new rows"
    )
    events.publish("pull", {"source": name, "added": added, "job": job})
    return {"job": job, "source": name, "added": added, "warnings": result.warnings}


@router.get("/inventory")
def get_inventory(
    request: Request, unmatched: bool | None = None, _: Read = None
) -> list[Reachable]:
    return store_of(request).reachable(unmatched=unmatched)


@router.put("/aliases")
def put_alias(
    request: Request,
    body: Annotated[dict[str, str], Body()],
    token: Annotated[Token, Depends(require("profiles:write"))],
) -> Any:
    store = store_of(request)
    alias, model_id = body.get("alias"), body.get("model_id")
    modality = body.get("modality", "llm")
    if not alias or not model_id:
        return error(400, "bad_request", "alias and model_id are required")
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
