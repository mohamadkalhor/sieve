"""The flip line: what would have to change for the answer to be different.

A ranking that only says "this one won" invites the question "by how much, and
would I still agree?". So position 1 carries one sentence naming the smallest
single weight change that would put position 2 on top:

    raise cost to 0.31 and z-ai/glm-5.3 leads

If no single weight can do it, the line says so -- which is itself the useful
answer, because it means the lead does not hinge on one opinion.
"""

from __future__ import annotations

from sieve.contracts import Profile, Rank, Ranking

#: below this the two are effectively the same model on this profile
EPSILON = 1e-9


def _contributions(rank: Rank) -> dict[str, float]:
    return {a.axis: a.contribution for a in rank.axes}


def _values(rank: Rank, weights: dict[str, float]) -> dict[str, float]:
    """Axis value per axis, recovered from the contribution and the weight."""
    out: dict[str, float] = {}
    for axis_score in rank.axes:
        weight = weights.get(axis_score.axis, 0.0)
        if axis_score.value is not None:
            out[axis_score.axis] = axis_score.value
        elif weight > 0:
            out[axis_score.axis] = axis_score.contribution / weight
        else:
            out[axis_score.axis] = 0.0
    return out


def flip_weight(
    weights: dict[str, float], leader: dict[str, float], runner: dict[str, float], axis: str
) -> float | None:
    """The weight `axis` would need for the runner-up to lead, others renormalised.

    Returns None when no value in 0..1 flips it, or when the axis carries the
    whole weight already (there is nothing left to renormalise).
    """
    held = weights.get(axis, 0.0)
    rest = 1.0 - held
    if rest <= EPSILON:
        return None

    leader_score = sum(weights[a] * leader.get(a, 0.0) for a in weights)
    runner_score = sum(weights[a] * runner.get(a, 0.0) for a in weights)

    leader_rest = leader_score - held * leader.get(axis, 0.0)
    runner_rest = runner_score - held * runner.get(axis, 0.0)

    on_axis = runner.get(axis, 0.0) - leader.get(axis, 0.0)
    off_axis = (runner_rest - leader_rest) / rest

    slope = on_axis - off_axis
    if abs(slope) < EPSILON:
        return None

    target = off_axis / (off_axis - on_axis) if abs(off_axis - on_axis) > EPSILON else None
    if target is None or not 0.0 <= target <= 1.0:
        return None

    # the runner-up has to be ahead *past* the crossing, not behind it
    probe = min(1.0, target + 1e-4) if slope > 0 else max(0.0, target - 1e-4)
    if (probe - target) * slope <= 0:
        return None
    return target


def explain(ranking: Ranking, profile: Profile) -> str | None:
    """One sentence for position 1, or None when there is nothing to compare."""
    ranked = sorted((r for r in ranking.ranks if r.position > 0), key=lambda r: r.position)
    if len(ranked) < 2:
        return None

    leader, runner = ranked[0], ranked[1]
    weights = {a: w for a, w in profile.weights.items() if w > 0}
    if not weights:
        return None

    leader_values = _values(leader, weights)
    runner_values = _values(runner, weights)

    best_axis: str | None = None
    best_target = 0.0
    best_move = float("inf")
    for axis in weights:
        target = flip_weight(weights, leader_values, runner_values, axis)
        if target is None:
            continue
        move = abs(target - weights[axis])
        if move < best_move:
            best_axis, best_target, best_move = axis, target, move

    gap = (leader.final - runner.final) * 100.0
    if best_axis is None:
        return (
            f"no single weight change puts {runner.model_id} ahead; "
            f"the lead is +{gap:.1f} across the board"
        )

    direction = "raise" if best_target > weights[best_axis] else "lower"
    return f"{direction} {best_axis} to {best_target:.2f} and {runner.model_id} leads"


def why(ranking: Ranking, profile: Profile) -> dict[str, object]:
    """The whole explanation as data, for the API and the profile editor."""
    ranked = sorted((r for r in ranking.ranks if r.position > 0), key=lambda r: r.position)
    if not ranked:
        return {"profile": profile.name, "explanation": "nothing ranked"}

    leader = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    contributions = _contributions(leader)
    carrying = max(contributions.items(), key=lambda pair: pair[1]) if contributions else None

    return {
        "profile": profile.name,
        "leader": leader.model_id,
        "runner_up": runner.model_id if runner else None,
        "gap_points": (leader.final - runner.final) * 100.0 if runner else None,
        "carried_by": carrying[0] if carrying else None,
        "confidence": leader.confidence,
        "flip": explain(ranking, profile),
        "excluded": [
            {"model_id": r.model_id, "reason": r.excluded_by or r.dominated_by}
            for r in ranking.ranks
            if r.excluded_by or r.dominated_by
        ],
    }
