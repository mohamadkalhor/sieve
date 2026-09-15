"""The run: pull -> catalog -> axes -> rankings -> chains -> decisions.

This module is the seam. It owns the order of operations (PLAN section 5) and
calls the pure functions of CONTRACTS section 3 by name. Owner modules that
have not landed yet are reported as warnings, never as tracebacks, so every
other part stays runnable.

**A profile is its weights.** The list a profile ships is the top `ship`
reachable models by weighted score, in score order, and nothing else takes a
model out of it: no Pareto pruning, no confidence floor, no score floor, no
requirements, no pins, no holds, no experience weighting. Every one of those
was a second opinion fighting the weights, and a weight you cannot see the
effect of is not a control.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any, Protocol

from sieve.config import Config
from sieve.contracts import (
    Axis,
    AxisScore,
    Chain,
    Decision,
    EngineResult,
    Modality,
    Observation,
    ObsTable,
    Profile,
    Rank,
    Ranking,
    TargetResult,
)
from sieve.scoring.weigh import TaskShape
from sieve.store import Store

#: How many rankings may be computed at once, and how long a caller waits for
#: a slot before being told to come back. The box has two cores; ten previews
#: arriving together used to run ten rankings, which is how both cores and the
#: memory cap went at once. Two run, the rest queue, and a caller that has
#: waited a minute is answered rather than left holding a request thread.
RANKING_SLOTS = max(1, int(os.environ.get("SIEVE_RANK_SLOTS") or 2))
RANKING_WAIT = float(os.environ.get("SIEVE_RANK_WAIT") or 60.0)

_slots = threading.BoundedSemaphore(RANKING_SLOTS)

#: What one task looks like, per modality, for the sole purpose of turning a
#: published price into a cost per task. It used to be `Profile.shape`, a
#: per-profile control nobody could tune without also re-tuning cost against
#: every other axis. These are the values the shipped profiles carried, now
#: fixed: cost stays an ordinary axis, and the arithmetic behind it is an
#: internal default rather than a knob.
SHAPES: dict[Modality, TaskShape] = {
    "llm": TaskShape(in_tokens=8000, out_tokens=2000),
    "text-to-image": TaskShape(images=1),
    "image-editing": TaskShape(images=1),
    "text-to-video": TaskShape(seconds=8),
    "image-to-video": TaskShape(seconds=8),
    "video-editing": TaskShape(seconds=8),
    "text-to-speech": TaskShape(chars=5000),
    "speech-to-text": TaskShape(seconds=600),
    "speech-to-speech": TaskShape(seconds=300),
    "music": TaskShape(seconds=30),
}

#: Every modality has one, so a new modality cannot silently price nothing.
DEFAULT_SHAPE = TaskShape()


def shape_for(modality: Modality) -> TaskShape:
    return SHAPES.get(modality, DEFAULT_SHAPE)


class RankingBusyError(RuntimeError):
    """Every ranking slot is taken and waiting for one timed out.

    The API answers this with 503 and a Retry-After: a queue this long is a
    box under load, and saying so is better than holding the connection until
    something times out and leaves the work running with nobody to hand it to.
    """

    def __init__(self, waited: float) -> None:
        super().__init__(
            f"every ranking slot was busy for {waited:.0f}s; the box is ranking as fast as it can"
        )
        self.waited = waited


@contextmanager
def ranking_slot(wait: float | None = None) -> Iterator[None]:
    """Hold one of the ranking slots, or raise `RankingBusyError`."""
    waited = RANKING_WAIT if wait is None else wait
    if not _slots.acquire(timeout=waited):
        raise RankingBusyError(waited)
    try:
        yield
    finally:
        _slots.release()


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
    def weigh(self) -> Any | None:
        return self._get("sieve.scoring.weigh", "A")

    @property
    def pulse(self) -> Any | None:
        return self._get("sieve.scoring.pulse", "A")

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
# cost
# --------------------------------------------------------------------------- #

#: the synthetic source and field a `cost` axis reads.
COST_SOURCE = "price"
COST_FIELD = "per_task"


def add_cost_observations(
    obs: ObsTable,
    profile: Profile,
    at: datetime,
    measured_tokens: dict[str, float] | None = None,
) -> tuple[dict[str, float], set[str]]:
    """Put `price:per_task` into the table so a cost axis can rank it.

    Cost is published as a rate -- dollars per million tokens, per image, per
    second -- and a rate cannot be ranked against a benchmark score. The shape
    of one task (`SHAPES`) turns it into a price per task, which can be. That
    shape is fixed per modality on purpose: it is arithmetic, not a preference.
    """
    from sieve.scoring.weigh import cost_per_task

    measured = measured_tokens or {}
    shape = shape_for(profile.modality)
    costs: dict[str, float] = {}
    from_telemetry: set[str] = set()
    # Every priced model, not every *observed* one. Iterating the observed set
    # was circular: a model a source prices but nobody benchmarks -- which is
    # most of what a gateway carries -- was not in the table yet, so it never
    # got a cost, so it never entered the table. A keyless `pull openrouter`
    # ranked nothing at all because of it.
    for model_id in sorted(obs.prices):
        price = obs.price(model_id)
        # A model whose own traffic has been measured is priced on the tokens
        # it really burns; everything else falls back to the fixed shape, which
        # is an assumption and is reported as one.
        tokens_out = measured.get(model_id)
        cost = cost_per_task(price, shape, tokens_out=tokens_out)
        if cost is None or cost <= 0:
            continue
        costs[model_id] = cost
        if tokens_out is not None:
            from_telemetry.add(model_id)
        obs.add(
            Observation(
                model_id=model_id,
                modality=profile.modality,
                source=COST_SOURCE,
                field=COST_FIELD,
                value=cost,
                unit="usd_per_task",
                observed_at=at,
                pulled_at=at,
            )
        )
    return costs, from_telemetry


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
    owner_id: str | None = None,
) -> Ranking:
    """One profile, scored end to end, holding one of the ranking slots.

    Empty (with warnings) until A has landed. Raises `RankingBusyError` if
    every slot stays taken for `RANKING_WAIT`.
    """
    with ranking_slot():
        return _rank_profile(
            cfg, store, profile, deps=deps, snapshot=snapshot, at=at, owner_id=owner_id
        )


def _cheapest_multipliers(
    store: Store,
    costs: dict[str, float],
    local: dict[str, list[str]],
    owner_id: str | None,
    overrides: dict[str, float] | None = None,
) -> dict[str, float]:
    """Apply the per-router price multipliers to the costs.

    A router-local prefix can carry a rate multiplier, and one canonical model
    may be reachable through several prefixes, so the cheapest reachable price
    is the one that is true for this box. The box's defaults hold for every
    seat; a profile's own `cost_multipliers` replace them prefix by prefix, for
    a seat that pays differently -- a subscription it barely pays for, say.
    """
    from sieve.profiles import control

    defaults = {**control.multipliers(store, owner_id), **(overrides or {})}
    if not defaults:
        return costs
    out = dict(costs)
    for model_id, amount in costs.items():
        factors = [defaults.get(i.split("/", 1)[0], 1.0) for i in local.get(model_id, [])]
        if factors:
            out[model_id] = amount * min(factors)
    return out


def _rank_profile(
    cfg: Config,
    store: Store,
    profile: Profile,
    *,
    deps: Deps,
    snapshot: str,
    at: datetime | None = None,
    owner_id: str | None = None,
) -> Ranking:
    at = at or datetime.now(UTC)
    # The observation table and the catalogue are the same for this snapshot
    # whoever asks, so they are built once and held (`store/cache.py`).
    # `working()` is this call's copy: the cost observations below are written
    # into it, and the shared one must not see them.
    view = store.cache.view(profile.modality, snapshot)
    obs = view.working()
    # PLAN 2.1: the rate per token is identical across a model's effort modes,
    # so only the tokens actually burned can tell them apart, and the gateway's
    # own traffic is the only place that number exists.
    measured_tokens = (
        deps.pulse.observed_tokens_out(store.telemetry(since=at - timedelta(days=7)), at)
        if deps.pulse is not None
        else {}
    )
    costs, costed_from_telemetry = add_cost_observations(obs, profile, at, measured_tokens)
    local = store.local_ids(owner_id)
    try:
        adjusted = _cheapest_multipliers(store, costs, local, owner_id, profile.cost_multipliers)
    except (RuntimeError, AttributeError):
        adjusted = costs
    for model_id, amount in adjusted.items():
        if amount == costs.get(model_id):
            continue
        costs[model_id] = amount
        bucket = obs.latest.get(model_id, {})
        key = obs.key(COST_SOURCE, COST_FIELD)
        if key in bucket:
            bucket[key] = bucket[key].model_copy(update={"value": amount})

    axes_load, axes_compute, weigh_mod = deps.axes_load, deps.axes_compute, deps.weigh
    if axes_load is None or axes_compute is None or weigh_mod is None:
        return Ranking(
            profile=profile.name, modality=profile.modality, computed_at=at, snapshot=snapshot
        )

    from sieve.axes import control as axis_control

    axis_control.seed(store, cfg.axes_dir)
    axes: list[Axis] = axis_control.axes(store, profile.modality, owner_id)
    wanted = {a.name for a in axes if a.name in profile.weights}
    # Every model with a measurement or a price, plus every model this box can
    # actually reach. A reachable model nobody has benchmarked scores 0 and
    # ranks last, which is the truth; leaving it out of the ranking entirely
    # would make "every reachable model has a position" a lie.
    pool = sorted(set(obs.models()) | set(local))

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
                cost_per_task=costs.get(model_id),
                cost_from=(
                    None
                    if model_id not in costs
                    else ("telemetry" if model_id in costed_from_telemetry else "shape")
                ),
            )
        )

    # The whole rule: reachable models, in score order. A model you cannot call
    # has no position, because it cannot be shipped; everything you can call
    # has one, because nothing else may take it out of the list.
    reachable = [r for r in ranks if r.reachable]
    reachable.sort(key=lambda r: (-r.final, r.model_id))
    for position, rank in enumerate(reachable, start=1):
        rank.position = position

    ranking = Ranking(
        profile=profile.name,
        modality=profile.modality,
        computed_at=at,
        snapshot=snapshot,
        ranks=reachable + [r for r in ranks if not r.reachable],
    )

    explain_mod = deps.explain
    if explain_mod is not None and reachable:
        reachable[0].flip = explain_mod.explain(ranking, profile)
    return ranking


def decide_chain(
    store: Store,
    profile: Profile,
    ranking: Ranking,
    *,
    deps: Deps,
    actor: str,
    at: datetime | None = None,
    owner_id: str | None = None,
) -> tuple[Chain | None, Decision | None]:
    """The list this ranking says to ship, and whether it changed."""
    at = at or datetime.now(UTC)
    incumbent = store.chain(profile.name, owner_id)
    policy_mod = deps.policy
    if policy_mod is None:
        return incumbent, None
    from sieve.profiles import control

    caps = control.capability_map(store, profile.modality) if profile.needs else None
    chain, decision = policy_mod.decide(profile, incumbent, ranking, at, caps)
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
    owner_id: str | None = None,
) -> EngineResult:
    """Score every profile, decide its chain, and (unless dry) store and ship it.

    Everything read and written here belongs to one person: their profiles,
    their reachable models, their chains. The daily run calls this once per
    user rather than once per box, so one ranking never mixes two people's
    routers.
    """
    at = datetime.now(UTC)
    owned = store or Store(cfg.db_path)
    deps = Deps()

    if profiles is None:
        loader = deps.profiles
        profiles = list(loader.load_profiles(cfg.profiles_dir, owned, owner_id)) if loader else []

    snapshot = owned.new_snapshot(source_rows=owned.count_observations())
    result = EngineResult(snapshot=snapshot, at=at, dry_run=dry_run)

    for profile in profiles:
        ranking = rank_profile(
            cfg, owned, profile, deps=deps, snapshot=snapshot, at=at, owner_id=owner_id
        )
        result.rankings.append(ranking)
        if not dry_run:
            owned.put_ranking(ranking, owner_id)

        chain, decision = decide_chain(
            owned, profile, ranking, deps=deps, actor=actor, at=at, owner_id=owner_id
        )
        if chain is not None:
            result.chains.append(chain)
            if not dry_run:
                owned.put_chain(chain, owner_id)
        if decision is not None:
            result.decisions.append(decision)
            if not dry_run:
                owned.add_decision(decision)

    result.warnings = sorted(set(deps.warnings))
    return result


def operates_box(store: Store, owner_id: str | None) -> bool:
    """Whether this seat may write the routers `sieve.toml` names.

    Those blocks describe the machine, not a person, so only the gate owner --
    or nobody-in-particular, on a box where no one has signed in -- writes
    them. A member ships to the connectors they added and nowhere else.
    """
    if owner_id is None:
        return True
    from sieve import owners

    who = owners.by_id(store, owner_id)
    return who is not None and who.role == "owner"


def apply_targets(
    cfg: Config,
    chains: list[Chain],
    *,
    targets: list[str] | None = None,
    dry_run: bool = True,
    actor: str = "cli",
    store: Store | None = None,
    owner_id: str | None = None,
) -> list[TargetResult]:
    """Write chains outward: every connector switched on for writing, then the
    `[targets.*]` blocks the config still names. Nothing else writes outward.

    A connector **shadows** a target of the same name. One gateway described in
    two places is still one gateway, and writing it twice is two round trips to
    say the same thing -- with the second one liable to disagree.
    """
    from sieve import plugins
    from sieve.connectors import log_applied, seed_from_toml, ship
    from sieve.connectors.base import ConnectorError

    owned = store or Store(cfg.db_path)
    seed_from_toml(cfg, owned, owner_id)
    # Only this person's connectors: a write goes out on somebody's token, and
    # `[targets.*]` from sieve.toml stays the box operator's business.
    by_name = {c.name: c for c in owned.connectors(owner_id)}
    from_config = list(cfg.targets) if operates_box(owned, owner_id) else []
    wanted = targets or [
        *(name for name, c in by_name.items() if c.write),
        *(name for name in from_config if name not in by_name),
    ]
    results: list[TargetResult] = []
    for name in wanted:
        connector = by_name.get(name)
        if connector is not None:
            if not connector.write:
                results.append(
                    TargetResult(
                        target=name, error=f"connector {name!r} is not switched on for writing"
                    )
                )
                continue
            try:
                outcome = ship(owned, connector, chains, dry_run=dry_run)
            except ConnectorError as exc:
                results.append(TargetResult(target=name, error=str(exc)))
                continue
            results.append(outcome)
            if not dry_run and outcome.error is None:
                log_applied(owned, connector, chains, actor)
            continue
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
