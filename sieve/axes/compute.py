"""Turn source fields into one axis value per model.

The whole point of an axis is that a profile never names a benchmark. It says
`agentic_coding: 0.45`, and this module decides what that means from the fields
the axis file lists and whatever the sources actually measured.

Two numbers come out per model: the `value` (0-1, percentile within the
modality's pool) and the `coverage` (the share of the axis's weight that was
really measured). Coverage is how a missing benchmark stays visible instead of
becoming a silent zero.
"""

from __future__ import annotations

from sieve.contracts import Axis, AxisField, ObsTable
from sieve.scoring.normalize import percentile_map

#: `missing: penalise` treats an absent field as this percentile -- poor, but
#: not bottom, and the coverage loss is reported either way.
PENALTY_PERCENTILE = 0.2


def field_is_live(field: AxisField, obs: ObsTable, pool: list[str]) -> bool:
    """A phase-2 field counts only once its source is actually publishing.

    That way an axis can name a benchmark before the connector exists, and picks
    it up the day the first observation lands -- which is the whole reason the
    axis files carry `phase`.
    """
    if field.phase <= 1:
        return True
    return any(obs.get(model_id, field.source, field.field) is not None for model_id in pool)


def field_values(field: AxisField, obs: ObsTable, pool: list[str]) -> dict[str, float | None]:
    """The raw value per model for one field, `None` where it was not measured."""
    out: dict[str, float | None] = {}
    for model_id in pool:
        observation = obs.get(model_id, field.source, field.field)
        if observation is None:
            out[model_id] = None
            continue
        if field.min_n is not None and (observation.n is None or observation.n < field.min_n):
            # too few samples to trust: unmeasured, not bad
            out[model_id] = None
            continue
        out[model_id] = observation.value
    return out


def axis_values(
    axis: Axis, obs: ObsTable, pool: list[str]
) -> dict[str, tuple[float | None, float]]:
    """`{model_id: (value, coverage)}` for one axis over one pool.

    `missing: renormalise` divides by the weight that was measured, so a model
    measured on half the fields is judged on that half. `missing: penalise`
    scores an absent field at the 20th percentile instead. Either way coverage
    falls, and a model below the axis's `min_coverage` reports `None` -- the
    axis is unmeasured for it, which is not the same as being bad at it.
    """
    live = [f for f in axis.fields if field_is_live(f, obs, pool)]
    total_weight = sum(f.weight for f in live)
    if not live or total_weight <= 0:
        return {model_id: (None, 0.0) for model_id in pool}

    ranked: list[tuple[AxisField, dict[str, float | None]]] = [
        (field, percentile_map(field_values(field, obs, pool), field.transform)) for field in live
    ]

    out: dict[str, tuple[float | None, float]] = {}
    for model_id in pool:
        weighted = 0.0
        measured_weight = 0.0
        for field, shares in ranked:
            share = shares.get(model_id)
            if share is not None:
                weighted += field.weight * share
                measured_weight += field.weight
            elif axis.missing == "penalise":
                weighted += field.weight * PENALTY_PERCENTILE

        coverage = measured_weight / total_weight
        if coverage < axis.min_coverage or measured_weight <= 0:
            out[model_id] = (None, coverage)
            continue

        divisor = measured_weight if axis.missing == "renormalise" else total_weight
        value = weighted / divisor
        if not axis.higher_is_better:
            value = 1.0 - value
        out[model_id] = (min(1.0, max(0.0, value)), coverage)

    return out


def all_axis_values(
    axes: list[Axis], obs: ObsTable, pool: list[str]
) -> dict[str, dict[str, tuple[float | None, float]]]:
    """`{model_id: {axis: (value, coverage)}}` -- the shape `weigh` consumes."""
    per_axis = {axis.name: axis_values(axis, obs, pool) for axis in axes}
    return {
        model_id: {name: values.get(model_id, (None, 0.0)) for name, values in per_axis.items()}
        for model_id in pool
    }
