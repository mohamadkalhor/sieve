"""What ships, and whether that is news.

A profile is its weights. The list it ships is the top `ship` reachable models
by weighted score, in score order -- recomputed on every run, with nothing
standing between the score and the list.

There used to be hysteresis here: a challenger had to clear a margin in points,
an incumbent that had held the seat too long had to re-earn it, and a model
that had stopped working lost the seat immediately. All three were ways of
arguing with the weights after the fact, and between them they made it
impossible to tell what moving a slider would do. Health still counts -- it is
multiplied into the score itself, in the ranking -- so a model that has stopped
working falls out on its own, by scoring lower, which is the honest version of
the same rule.

Every evaluation still writes a decision row, the ones where nothing happened
included: "held: the list is unchanged" is the sentence that stops someone
asking why the gateway did not move.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime

from sieve.contracts import Capability, Chain, Decision, Profile, Rank, Ranking
from sieve.scoring.select import select


def shipped(ranking: Ranking, ship: int) -> list[Rank]:
    """The rows this ranking would ship: top `ship` reachable, in score order."""
    ranked = sorted((r for r in ranking.ranks if r.position > 0), key=lambda r: r.position)
    return ranked[: max(1, ship)]


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


def chain_from(
    profile: Profile,
    ranking: Ranking,
    rows: list[Rank],
    incumbent: Chain | None,
    now: datetime,
) -> Chain:
    """One chain out of the rows that ship, in the order they ship."""
    ids = [r.model_id for r in rows]
    held_since = (
        incumbent.incumbent_since
        if incumbent is not None and [incumbent.primary, *incumbent.fallbacks] == ids
        else now
    )
    return Chain(
        profile=profile.name,
        computed_at=ranking.computed_at or now,
        primary=ids[0],
        fallbacks=ids[1:],
        local={r.model_id: r.local_ids for r in rows},
        incumbent=ids[0],
        incumbent_since=held_since,
    )


def decide(
    profile: Profile,
    incumbent: Chain | None,
    ranking: Ranking,
    now: datetime,
    capabilities: Mapping[str, Capability] | None = None,
) -> tuple[Chain | None, Decision | None]:
    """The list to ship, and whether it differs from the one last shipped.

    The list is `select`'s: pins, removals, a manual list and capability needs
    are applied here exactly as the page previews them. Pass `capabilities`
    whenever the profile has needs, or every need reads as unknown.
    """
    rows = select(ranking.ranks, profile, capabilities).rows
    if not rows:
        reason = "hold: nothing reachable ranks for this profile"
        return incumbent, _decision(profile, "hold", reason, None, None)

    chain = chain_from(profile, ranking, rows, incumbent, now)
    after = [chain.primary, *chain.fallbacks]
    before = [incumbent.primary, *incumbent.fallbacks] if incumbent is not None else []

    if before == after:
        reason = f"hold: the list is unchanged, {after[0]} still leads"
        return chain, _decision(profile, "hold", reason, before, after)

    reason = f"switch: the list changed, {after[0]} leads{_carried(rows)}"
    return chain, _decision(profile, "switch", reason, before or None, after)


def _carried(rows: list[Rank]) -> str:
    """` (reasoning 0.31)` -- the axis carrying most of the leader's score."""
    if not rows or not rows[0].axes:
        return ""
    axis = max(rows[0].axes, key=lambda a: a.contribution)
    if axis.contribution <= 0:
        return ""
    return f" ({axis.axis} {axis.contribution:.2f})"
