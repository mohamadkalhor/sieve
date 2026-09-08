"""Choosing between the effort modes of one model.

A family like `openai/gpt-5-6-sol` reaches the ranking as six rows, one per
mode, all at the same price per token. Left alone they compete with each other
and a chain fills up with six settings of one model, which is not a fallback
chain at all. `Profile.prefer_effort` says which one to seat:

- `best` — the highest mode. What you want when quality is the whole point.
- `cheapest_clearing` — the *lowest* mode that still clears every floor the
  profile sets. The behaviour this tool exists for: stop paying for max effort
  when medium already passes.
- a mode name (`medium`, `low`, ...) — pin it, and fall back to the nearest
  lower mode the family actually publishes rather than silently seating a
  higher one.
- unset — leave every mode in as its own row, which is what phase 1 did.

Only *eligible* rows are considered. A mode set aside by a constraint or the
confidence floor has already failed, and picking it because it is cheaper would
be picking a model the profile just rejected.
"""

from __future__ import annotations

from collections.abc import Mapping

from sieve.catalog.effort import effort_rank

#: Why a mode was set aside, written into the rank so a person can read it.
REASON = "effort:{kept} preferred for {family}"


def choose_efforts(
    candidates: Mapping[str, tuple[str | None, str | None]],
    prefer: str | None,
) -> dict[str, str]:
    """`{model_id: reason}` for every mode this profile does not want.

    `candidates` maps an eligible model id to its `(family, effort)`. A model
    with no family, or the only one in its family, is never set aside: there is
    nothing to choose between.
    """
    if not prefer:
        return {}

    by_family: dict[str, list[tuple[str, str | None]]] = {}
    for model_id, (family, effort) in candidates.items():
        if family:
            by_family.setdefault(family, []).append((model_id, effort))

    out: dict[str, str] = {}
    for family, members in by_family.items():
        if len(members) < 2:
            continue
        keep = _pick(members, prefer)
        if keep is None:
            continue
        kept_effort = next(e for m, e in members if m == keep)
        for model_id, _effort in members:
            if model_id != keep:
                out[model_id] = REASON.format(kept=kept_effort or "unstated", family=family)
    return out


def _pick(members: list[tuple[str, str | None]], prefer: str | None) -> str | None:
    """Which member of one family to seat. Ties break on the id, as elsewhere."""
    ordered = sorted(members, key=lambda pair: (effort_rank(pair[1]), pair[0]))

    if prefer == "best":
        return ordered[-1][0]
    if prefer == "cheapest_clearing":
        # every member here already cleared the floors, so the cheapest
        # clearing mode is simply the lowest one still standing
        return ordered[0][0]

    exact = [model_id for model_id, effort in ordered if effort == prefer]
    if exact:
        return exact[0]

    # a pinned mode this family does not publish: take the nearest lower one,
    # never a higher one, so pinning `medium` can never cost max-effort money
    target = effort_rank(prefer)
    lower = [pair for pair in ordered if effort_rank(pair[1]) <= target]
    return (lower[-1] if lower else ordered[0])[0]
