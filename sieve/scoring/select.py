"""Which ranked rows a profile ships, once a person's hands are on the list.

The ranking is the weights' answer. This is the only place anything else gets
a say, and each say is visible on the row it touches:

- **auto**: pinned models first, in the order they were pinned; then the best
  of the rest by score, skipping removed ones, until the list is `ship` long.
  Pins count towards `ship`, and never push each other out.
- **manual**: exactly the listed models, in the listed order. The weights still
  score them, so the page can show the numbers, and decide nothing.
- **needs**, in both modes: a model ships only if it is *known* to do each
  thing asked. A model no source described fails a need exactly as a model
  that cannot do it -- an absence is not a yes -- and `lacks` says which.

Pure: no store, no clock. The preview, `apply` and the scheduled run all call
`select`, so the list on the page is the list that ships.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sieve.contracts import NEEDS, Capability, Need


def has(capability: Capability | None, need: str) -> bool | None:
    """Whether a model does `need`: True, False, or None when nobody said."""
    if capability is None:
        return None
    if need == "vision":
        seen = capability.input_modalities
        return ("image" in seen) if seen else None
    value = getattr(capability, need, None)
    return None if value is None else bool(value)


def abilities(capability: Capability | None) -> dict[str, bool | None]:
    """Every need, answered for one model, for a row to draw."""
    return {need: has(capability, need) for need in NEEDS}


def lacks(capability: Capability | None, needs: Iterable[Need | str]) -> list[str]:
    """The needs this model is not known to meet, in the order they were asked."""
    return [need for need in needs if has(capability, need) is not True]


@dataclass
class Selection:
    """The rows that ship, and every row the hand controls kept out."""

    rows: list[Any] = field(default_factory=list)
    #: ranked, reachable rows a need kept out (auto: only those that would
    #: otherwise have been in reach; manual and pinned: any)
    failed_needs: list[Any] = field(default_factory=list)
    #: listed or pinned ids that are not reachable right now
    missing: list[str] = field(default_factory=list)


def _reachable(ranked: Iterable[Any]) -> list[Any]:
    return sorted((r for r in ranked if r.position > 0), key=lambda r: r.position)


def select(
    ranked: Iterable[Any],
    settings: Any,
    capabilities: Mapping[str, Capability] | None = None,
) -> Selection:
    """The list `settings` ships out of `ranked`.

    `settings` is anything with `mode`, `ship`, `manual`, `pinned`, `removed`
    and `needs` -- a `Profile` or a `ProfileSettings`.
    """
    caps = capabilities or {}
    needs = list(getattr(settings, "needs", []) or [])
    ordered = _reachable(ranked)
    by_id = {r.model_id: r for r in ordered}
    out = Selection()

    def fits(row: Any) -> bool:
        if needs and lacks(caps.get(row.model_id), needs):
            out.failed_needs.append(row)
            return False
        return True

    if getattr(settings, "mode", "auto") == "manual":
        seen: set[str] = set()
        for model_id in getattr(settings, "manual", []) or []:
            if model_id in seen:
                continue
            seen.add(model_id)
            row = by_id.get(model_id)
            if row is None:
                out.missing.append(model_id)
            elif fits(row):
                out.rows.append(row)
        return out

    ship = max(1, int(getattr(settings, "ship", 1) or 1))
    removed = set(getattr(settings, "removed", []) or [])
    taken: set[str] = set()
    for model_id in getattr(settings, "pinned", []) or []:
        if model_id in taken:
            continue
        taken.add(model_id)
        row = by_id.get(model_id)
        if row is None:
            out.missing.append(model_id)
        elif fits(row):
            out.rows.append(row)
    for row in ordered:
        if len(out.rows) >= ship:
            break
        if row.model_id in taken or row.model_id in removed:
            continue
        if fits(row):
            out.rows.append(row)
    return out


def behind(
    ranked: Iterable[Any],
    settings: Any,
    selection: Selection,
    capabilities: Mapping[str, Capability] | None = None,
) -> list[Any]:
    """The reachable rows next in line: not shipping, not removed, meeting every need."""
    caps = capabilities or {}
    needs = list(getattr(settings, "needs", []) or [])
    shipping = {r.model_id for r in selection.rows}
    removed = set(getattr(settings, "removed", []) or [])
    return [
        r
        for r in _reachable(ranked)
        if r.model_id not in shipping
        and r.model_id not in removed
        and not (needs and lacks(caps.get(r.model_id), needs))
    ]
