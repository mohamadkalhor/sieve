"""Pareto pruning: drop a model another reachable model beats on everything.

If B is at least as good as A on every axis the profile weights, and strictly
better on at least one, then no change of weights can ever put A above B. A is
dominated, and saying so ("dominated by B") is more useful than showing it at
position 40.

Pruning runs among *reachable* models only. A model you cannot call should not
be able to knock a model you can out of the list.
"""

from __future__ import annotations

Rows = dict[str, dict[str, float | None]]


def dominates(
    better: dict[str, float | None], worse: dict[str, float | None], axes: list[str]
) -> bool:
    """True when `better` is >= on every axis and > on at least one.

    An axis where either side is unmeasured is skipped: an absent measurement is
    not evidence of being worse, and treating it as such would prune models for
    the crime of being new.
    """
    strictly_better = False
    compared = 0
    for axis in axes:
        left, right = better.get(axis), worse.get(axis)
        if left is None or right is None:
            continue
        compared += 1
        if left < right:
            return False
        if left > right:
            strictly_better = True
    return compared > 0 and strictly_better


def pareto_prune(rows: Rows, weights: dict[str, float]) -> dict[str, str]:
    """`{model_id: dominated_by}` for every model another one strictly beats.

    Only weighted axes count: a model losing on an axis the profile ignores is
    not losing at all.
    """
    axes = [axis for axis, weight in weights.items() if weight > 0]
    if not axes or len(rows) < 2:
        return {}

    dominated: dict[str, str] = {}
    for model_id, values in rows.items():
        for other_id, other in rows.items():
            if other_id == model_id or other_id in dominated:
                continue
            if dominates(other, values, axes):
                dominated[model_id] = other_id
                break
    return dominated
