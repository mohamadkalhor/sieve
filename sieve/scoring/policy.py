"""When the top pick is allowed to change.

Without hysteresis a model list flaps: two models within noise of each other
trade places every hour, every trade rewrites a gateway config, and nobody
trusts the tool. So a challenger has to clear a *margin* in points on a 0-100
scale, an incumbent that has held the seat too long has to re-earn it, and an
incumbent that has stopped working loses it immediately.

Every evaluation writes a decision row -- including the ones where nothing
happened. "held: challenger +1.4 inside margin 3.0" is the sentence that stops
someone asking why the list did not move.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sieve.contracts import Chain, Decision, Profile, Rank, Ranking

#: scores are 0-1; `policy.margin` is stated in points, so compare in points.
POINTS = 100.0


def _points(value: float) -> float:
    return value * POINTS


def _signed(points: float) -> str:
    """`+4.2` / `-1.3` -- never `+-1.3`."""
    return f"{points:+.1f}"


def _ranked(ranking: Ranking) -> list[Rank]:
    return sorted(
        (r for r in ranking.ranks if r.position > 0 and not r.excluded_by and not r.dominated_by),
        key=lambda r: r.position,
    )


def _reachable(ranking: Ranking) -> list[Rank]:
    return [r for r in _ranked(ranking) if r.reachable]


def _chain_from(
    profile: Profile,
    ranking: Ranking,
    candidates: list[Rank],
    incumbent: Chain | None,
    now: datetime,
    primary: str,
) -> Chain:
    ordered = [primary] + [r.model_id for r in candidates if r.model_id != primary]
    cut = ordered[: max(1, profile.policy.chain)]
    local = {r.model_id: r.local_ids for r in candidates if r.model_id in cut}
    held_since = (
        incumbent.incumbent_since if incumbent is not None and incumbent.primary == primary else now
    )
    return Chain(
        profile=profile.name,
        computed_at=ranking.computed_at or now,
        primary=cut[0],
        fallbacks=cut[1:],
        local=local,
        incumbent=primary,
        incumbent_since=held_since,
    )


def _decision(profile: Profile, kind: str, reason: str, before: object, after: object) -> Decision:
    return Decision(
        id=uuid.uuid4().hex[:12],
        at=datetime.now(tz=None).astimezone(),
        profile=profile.name,
        kind=kind,  # type: ignore[arg-type]
        actor="",
        before=before,
        after=after,
        reason=reason,
    )


def tenure_days(incumbent: Chain | None, now: datetime) -> int | None:
    if incumbent is None or incumbent.incumbent_since is None:
        return None
    return (now - incumbent.incumbent_since).days


def decide(
    profile: Profile, incumbent: Chain | None, ranking: Ranking, now: datetime
) -> tuple[Chain | None, Decision | None]:
    """Apply the profile's policy. Returns the chain to keep and what was decided."""
    policy = profile.policy
    candidates = _reachable(ranking)

    if not candidates:
        reason = "hold: nothing reachable ranks for this profile"
        return incumbent, _decision(profile, "hold", reason, None, None)

    challenger = candidates[0]
    held = incumbent.primary if incumbent is not None else None

    # nobody holds the seat yet: the leader takes it, and that is a switch.
    if held is None:
        chain = _chain_from(profile, ranking, candidates, incumbent, now, challenger.model_id)
        reason = f"switch: {challenger.model_id} takes primary, no incumbent"
        return chain, _decision(profile, "switch", reason, None, challenger.model_id)

    incumbent_rank = next((r for r in _ranked(ranking) if r.model_id == held), None)

    # the incumbent has fallen out of the list entirely
    if incumbent_rank is None:
        chain = _chain_from(profile, ranking, candidates, incumbent, now, challenger.model_id)
        reason = (
            f"switch: {challenger.model_id} over {held}, "
            f"{held} no longer ranks (unreachable or excluded)"
        )
        return chain, _decision(profile, "switch", reason, held, challenger.model_id)

    # it is still the leader: nothing to decide
    if challenger.model_id == held:
        chain = _chain_from(profile, ranking, candidates, incumbent, now, held)
        runner = candidates[1] if len(candidates) > 1 else None
        if runner is not None:
            lead = _points(incumbent_rank.final - runner.final)
            reason = f"hold: {held} still leads by {_signed(lead)}"
        else:
            reason = f"hold: {held} is the only reachable model"
        return chain, _decision(profile, "hold", reason, held, held)

    gap = _points(challenger.final - incumbent_rank.final)

    # health first: a model that has stopped working loses the seat now
    if incumbent_rank.health < policy.suspend_below_health:
        chain = _chain_from(profile, ranking, candidates, incumbent, now, challenger.model_id)
        reason = (
            f"suspend: {held} health {incumbent_rank.health:.2f} < "
            f"{policy.suspend_below_health:.2f}, {challenger.model_id} takes primary"
        )
        return chain, _decision(profile, "suspend", reason, held, challenger.model_id)

    # then tenure: an old incumbent re-contests the seat with no margin
    tenure = tenure_days(incumbent, now)
    if tenure is not None and tenure > policy.max_tenure_days:
        chain = _chain_from(profile, ranking, candidates, incumbent, now, challenger.model_id)
        reason = (
            f"switch: tenure {tenure} d > {policy.max_tenure_days} d, margin waived, "
            f"{challenger.model_id} leads by {_signed(gap)}"
        )
        return chain, _decision(profile, "switch", reason, held, challenger.model_id)

    # then the margin
    if gap >= policy.margin:
        chain = _chain_from(profile, ranking, candidates, incumbent, now, challenger.model_id)
        reason = (
            f"switch: {challenger.model_id} over {held} by {_signed(gap)}, "
            f"margin {policy.margin:.1f}{_top_axis(challenger, incumbent_rank)}"
        )
        return chain, _decision(profile, "switch", reason, held, challenger.model_id)

    # nothing changes, and the chain stays exactly as it was
    reason = (
        f"hold: challenger {challenger.model_id} {_signed(gap)} inside margin {policy.margin:.1f}"
    )
    return incumbent, _decision(profile, "hold", reason, held, held)


def _top_axis(challenger: Rank, incumbent: Rank) -> str:
    """` (agentic_coding +0.31)` -- the axis carrying most of the gap."""
    held = {a.axis: a.contribution for a in incumbent.axes}
    gaps = [(a.axis, a.contribution - held.get(a.axis, 0.0)) for a in challenger.axes]
    gaps = [g for g in gaps if g[1] > 0]
    if not gaps:
        return ""
    axis, delta = max(gaps, key=lambda pair: pair[1])
    return f" ({axis} +{delta:.2f})"


def next_evaluation(profile: Profile, last: datetime) -> datetime:
    """When tenure alone would re-open the seat."""
    return last + timedelta(days=profile.policy.max_tenure_days)
