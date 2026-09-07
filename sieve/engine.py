"""The run: pull -> catalog -> axes -> rankings -> chains -> decisions.

This module is the seam. It owns the order of operations (PLAN section 5) and
the constraint gate, and calls the pure functions of CONTRACTS section 3 by
name. Owner modules that have not landed yet are reported as warnings, never
as tracebacks, so every other part stays runnable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any, Protocol

from sieve.config import Config
from sieve.contracts import (
    Axis,
    AxisScore,
    Capability,
    Chain,
    Decision,
    EngineResult,
    Modality,
    ObsTable,
    Profile,
    Rank,
    Ranking,
    TargetResult,
)
from sieve.store import Store


class OwnerMissingError(NotImplementedError):
    """A module another owner is building has not landed yet.

    A NotImplementedError so `sieve <verb>` fails with "owned by A" from the
    real entry point rather than an import traceback.
    """

    def __init__(self, module: str, owner: str) -> None:
        super().__init__(f"{module} is owned by {owner} and has not landed yet")
        self.module = module
        self.owner = owner


def _optional(module: str, owner: str) -> Any:
    try:
        return import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name and not module.startswith(str(exc.name)):
            raise
        raise OwnerMissingError(module, owner) from exc


class _AxesModule(Protocol):
    def load_axes(self, directory: Any, modality: Modality) -> list[Axis]: ...


@dataclass
class Deps:
    """Lazily resolved owner modules, so the engine imports nothing at import time."""

    warnings: list[str] = field(default_factory=list)

    def _get(self, module: str, owner: str) -> Any | None:
        try:
            return _optional(module, owner)
        except OwnerMissingError as exc:
            self.warnings.append(str(exc))
            return None

    @property
    def axes_load(self) -> Any | None:
        return self._get("sieve.axes.load", "A")

    @property
    def axes_compute(self) -> Any | None:
        return self._get("sieve.axes.compute", "A")

    @property
    def pareto(self) -> Any | None:
        return self._get("sieve.scoring.pareto", "A")

    @property
    def weigh(self) -> Any | None:
        return self._get("sieve.scoring.weigh", "A")

    @property
    def health(self) -> Any | None:
        return self._get("sieve.scoring.health", "A")

    @property
    def policy(self) -> Any | None:
        return self._get("sieve.scoring.policy", "A")

    @property
    def explain(self) -> Any | None:
        return self._get("sieve.scoring.explain", "A")

    @property
    def profiles(self) -> Any | None:
        return self._get("sieve.profiles.load", "B")


# --------------------------------------------------------------------------- #
# constraints (PLAN section 4 `require`)
# --------------------------------------------------------------------------- #


def passes_constraints(
    profile: Profile,
    model_id: str,
    capability: Capability,
    axis_pct: dict[str, float],
    appearances: int | None,
) -> str | None:
    """None when the model is eligible, otherwise the constraint that excluded it."""
    require = profile.require
    if require.get("tools") and capability.tools is not True:
        return "tools"
    if require.get("reasoning") and capability.reasoning is not True:
        return "reasoning"
    if require.get("structured_output") and capability.structured_output is not True:
        return "structured_output"
    ctx_min = require.get("context_min")
    if ctx_min is not None and (
        capability.context_window is None or capability.context_window < int(ctx_min)
    ):
        return "context_min"
    wanted = require.get("input_modalities") or []
    if wanted and not set(wanted).issubset(set(capability.input_modalities)):
        return "input_modalities"
    floors = require.get("min_axis") or {}
    for axis_name, floor in floors.items():
        value = axis_pct.get(axis_name)
        if value is None or value < float(floor):
            return f"min_axis:{axis_name}"
    min_app = require.get("min_appearances")
    if min_app is not None and (appearances is None or appearances < int(min_app)):
        return "min_appearances"
    return None


def _appearances(obs: ObsTable, model_id: str) -> int | None:
    for bucket in (obs.latest.get(model_id) or {}).values():
        if bucket.field == "elo" and bucket.n is not None:
            return bucket.n
    return None


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #


def rank_profile(
    cfg: Config,
    store: Store,
    profile: Profile,
    *,
    deps: Deps,
    snapshot: str,
    at: datetime | None = None,
) -> Ranking:
    """One profile, scored end to end. Empty (with warnings) until A has landed."""
    at = at or datetime.now(UTC)
    obs = store.obs_table(profile.modality)
    local = store.local_ids()
    caps = {r.model_id: r.capability for r in store.reachable(unmatched=False) if r.model_id}

    axes_load, axes_compute, weigh_mod = deps.axes_load, deps.axes_compute, deps.weigh
    if axes_load is None or axes_compute is None or weigh_mod is None:
        return Ranking(
            profile=profile.name, modality=profile.modality, computed_at=at, snapshot=snapshot
        )

    axes: list[Axis] = axes_load.load_axes(cfg.axes_dir, profile.modality)
    wanted = {a.name for a in axes if a.name in profile.weights}
    pool = obs.models()

    # axis values, per axis, over the whole modality pool
    per_axis: dict[str, dict[str, tuple[float | None, float]]] = {}
    for axis in axes:
        if axis.name not in wanted:
            continue
        per_axis[axis.name] = axes_compute.axis_values(axis, obs, pool)

    axes_by_model: dict[str, dict[str, tuple[float | None, float]]] = {}
    for model_id in pool:
        axes_by_model[model_id] = {
            name: per_axis[name].get(model_id, (None, 0.0)) for name in per_axis
        }

    # constraints first
    excluded: dict[str, str] = {}
    for model_id in pool:
        pct = {n: v for n, (v, _c) in axes_by_model[model_id].items() if v is not None}
        reason = passes_constraints(
            profile,
            model_id,
            caps.get(model_id, Capability()),
            pct,
            _appearances(obs, model_id),
        )
        if reason:
            excluded[model_id] = reason

    # then Pareto pruning among reachable, eligible models
    dominated: dict[str, str] = {}
    pareto = deps.pareto
    if pareto is not None:
        rows = {
            m: {n: v for n, (v, _c) in axes_by_model[m].items()}
            for m in pool
            if m not in excluded and m in local
        }
        dominated = pareto.pareto_prune(rows, profile.weights)

    scored = weigh_mod.weigh(profile, axes_by_model)

    health_mod = deps.health
    health_by_model: dict[str, float] = {}
    if health_mod is not None:
        health_by_model = health_mod.health(store.telemetry(since=at - timedelta(days=7)), at)

    ranks: list[Rank] = []
    for model_id, result in scored.items():
        score, confidence, contributions = result
        health_value = health_by_model.get(model_id, 1.0)
        axis_scores = [
            AxisScore(
                axis=name,
                value=axes_by_model[model_id][name][0],
                coverage=axes_by_model[model_id][name][1],
                contribution=contributions.get(name, 0.0),
            )
            for name in sorted(axes_by_model[model_id])
        ]
        ranks.append(
            Rank(
                position=0,
                model_id=model_id,
                reachable=model_id in local,
                local_ids=local.get(model_id, []),
                score=score,
                confidence=confidence,
                health=health_value,
                final=score * health_value,
                axes=axis_scores,
                cost_per_task=None,
                dominated_by=dominated.get(model_id),
                excluded_by=excluded.get(model_id)
                or ("min_confidence" if confidence < profile.policy.min_confidence else None),
            )
        )

    eligible = [r for r in ranks if not r.excluded_by and not r.dominated_by]
    eligible.sort(key=lambda r: r.final, reverse=True)
    for position, rank in enumerate(eligible, start=1):
        rank.position = position

    ranking = Ranking(
        profile=profile.name,
        modality=profile.modality,
        computed_at=at,
        snapshot=snapshot,
        ranks=eligible + [r for r in ranks if r.excluded_by or r.dominated_by],
    )

    explain_mod = deps.explain
    if explain_mod is not None and eligible:
        eligible[0].flip = explain_mod.explain(ranking, profile)
    return ranking


def decide_chain(
    store: Store,
    profile: Profile,
    ranking: Ranking,
    *,
    deps: Deps,
    actor: str,
    at: datetime | None = None,
) -> tuple[Chain | None, Decision | None]:
    """Apply the profile's policy to the new ranking. Every call is a decision row."""
    at = at or datetime.now(UTC)
    incumbent = store.chain(profile.name)
    policy_mod = deps.policy
    if policy_mod is None:
        return incumbent, None
    chain, decision = policy_mod.decide(profile, incumbent, ranking, at)
    if decision is not None and not decision.actor:
        decision = decision.model_copy(update={"actor": actor})
    return chain, decision


def run(
    cfg: Config,
    *,
    profiles: list[Profile] | None = None,
    dry_run: bool = True,
    actor: str = "cli",
    store: Store | None = None,
) -> EngineResult:
    """Score every profile, decide its chain, and (unless dry) store and ship it."""
    at = datetime.now(UTC)
    owned = store or Store(cfg.db_path)
    deps = Deps()

    if profiles is None:
        loader = deps.profiles
        profiles = list(loader.load_profiles(cfg.profiles_dir)) if loader else []

    snapshot = owned.new_snapshot(source_rows=owned.count_observations())
    result = EngineResult(snapshot=snapshot, at=at, dry_run=dry_run)

    for profile in profiles:
        ranking = rank_profile(cfg, owned, profile, deps=deps, snapshot=snapshot, at=at)
        result.rankings.append(ranking)
        if not dry_run:
            owned.put_ranking(ranking)

        chain, decision = decide_chain(owned, profile, ranking, deps=deps, actor=actor, at=at)
        if chain is not None:
            result.chains.append(chain)
            if not dry_run:
                owned.put_chain(chain)
        if decision is not None:
            result.decisions.append(decision)
            if not dry_run:
                owned.add_decision(decision)

    result.warnings = sorted(set(deps.warnings))
    return result


def apply_targets(
    cfg: Config,
    chains: list[Chain],
    *,
    targets: list[str] | None = None,
    dry_run: bool = True,
    actor: str = "cli",
    store: Store | None = None,
) -> list[TargetResult]:
    """Write chains to the named targets. Nothing else in Sieve writes outward."""
    from sieve import plugins

    owned = store or Store(cfg.db_path)
    wanted = targets or list(cfg.targets)
    results: list[TargetResult] = []
    for name in wanted:
        target_cfg = cfg.targets.get(name)
        if target_cfg is None:
            results.append(TargetResult(target=name, error=f"no target named {name!r}"))
            continue
        try:
            target = plugins.load(plugins.TARGETS, target_cfg.kind)
        except (LookupError, ImportError) as exc:
            results.append(TargetResult(target=name, error=str(exc)))
            continue
        outcome = target.write(target_cfg, chains, dry_run)
        results.append(outcome)
        if not dry_run and outcome.error is None:
            for chain in chains:
                owned.add_decision(
                    Decision(
                        id=uuid.uuid4().hex[:12],
                        at=datetime.now(UTC),
                        profile=chain.profile,
                        kind="apply",
                        actor=actor,
                        before=None,
                        after={"primary": chain.primary, "fallbacks": chain.fallbacks},
                        reason=f"applied {chain.profile} to target {name}",
                        detail={"target": name},
                    )
                )
    return results
