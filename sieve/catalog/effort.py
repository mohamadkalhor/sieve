"""Reasoning effort: which mode of a model a row is about.

Artificial Analysis publishes **one row per effort mode** -- 230 of its 644 on
2026-09-08 -- and every mode of a family is served at the same price per token.
`GPT-5.6 Sol` ships six rows at $4.00/1M input: max 51.3, xhigh 49.8, high
48.3, medium 46.0, low 40.8, non-reasoning 32.9. A gateway that serves
`gemini-3.8-flash-high`, `-medium` and `-low` as three ids must therefore reach
three *different* rows. Collapsing them onto one credits a low-effort call with
a high-effort score, which is the most expensive mistake this tool can make.

Two things are needed to keep them apart, and this module supplies both: the
mode a row is about, and the family it belongs to.

**Read the mode from the name, not the slug.** The slug is not reliable on its
own: AA's *bare* slug is the family's top mode, and which mode that is varies
by family -- `gpt-6-astra` is `(max)` while `gemini-3-8-flash` is `(high)`.
The name carries it in brackets and is unambiguous.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from sieve.contracts import Effort, EffortHow

#: The modes AA publishes, ordered from least effort to most. The order is the
#: point: `cheapest_clearing` walks up it, `best` takes the far end.
EFFORT_ORDER: tuple[str, ...] = (
    "non-reasoning",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

_RANK = {mode: index for index, mode in enumerate(EFFORT_ORDER)}

#: What a bracket may say for each mode, lowercased. "Max Effort" appears in
#: `Claude Opus 5 (Adaptive Reasoning, Max Effort)`, which is why a bracket is
#: split on commas and every part is examined rather than only the first.
_SPELLINGS: dict[str, str] = {
    "non-reasoning": "non-reasoning",
    "nonreasoning": "non-reasoning",
    "no reasoning": "non-reasoning",
    "minimal": "minimal",
    "minimal effort": "minimal",
    "low": "low",
    "low effort": "low",
    "medium": "medium",
    "medium effort": "medium",
    "high": "high",
    "high effort": "high",
    "xhigh": "xhigh",
    "x-high": "xhigh",
    "extra high": "xhigh",
    "max": "max",
    "max effort": "max",
    "maximum effort": "max",
}

_BRACKET = re.compile(r"\(([^)]*)\)")

#: The same modes as a trailing slug suffix, longest first so `-non-reasoning`
#: is tried before `-reasoning` could ever be.
_SLUG_SUFFIXES: tuple[str, ...] = tuple(
    sorted(("non-reasoning", "minimal", "low", "medium", "high", "xhigh"), key=len, reverse=True)
)


def effort_of(name: str) -> str | None:
    """The mode a published name declares, or None if it declares none.

    >>> effort_of("GPT-5.6 Sol (medium)")
    'medium'
    >>> effort_of("Claude Opus 5 (Adaptive Reasoning, Max Effort)")
    'max'
    >>> effort_of("Quasar 438B (max, based on GLM-5.2)")
    'max'
    >>> effort_of("Kimi K2 Thinking") is None
    True
    """
    best: str | None = None
    for bracket in _BRACKET.findall(name or ""):
        for part in bracket.split(","):
            mode = _SPELLINGS.get(part.strip().lower())
            # a bracket naming two modes takes the higher; none does today
            if mode is not None and (best is None or _RANK[mode] > _RANK[best]):
                best = mode
    return best


def family_of(model_id: str) -> str:
    """The id shared by every mode of one model.

    The bare slug is itself a mode -- the family's top one -- so it is its own
    family and the suffixed ones fold onto it.

    >>> family_of("openai/gpt-5-6-sol-medium")
    'openai/gpt-5-6-sol'
    >>> family_of("openai/gpt-5-6-sol")
    'openai/gpt-5-6-sol'
    """
    for suffix in _SLUG_SUFFIXES:
        tail = f"-{suffix}"
        if model_id.endswith(tail) and len(model_id) > len(tail):
            return model_id[: -len(tail)]
    return model_id


def effort_rank(mode: str | None) -> int:
    """Where a mode sits on the effort ladder. An unstated mode ranks last.

    A model that declares no mode is not a low-effort model; it is a model with
    one setting. Ranking it above every stated mode keeps `best` from
    preferring a mode row over a plain model that has no modes at all.
    """
    if mode is None:
        return len(EFFORT_ORDER)
    return _RANK.get(mode, len(EFFORT_ORDER))


#: what a router id may end in to name its own effort: the slug suffixes, and
#: `max`, which AA never writes as a suffix but a gateway does
_ID_SUFFIXES: tuple[str, ...] = (*_SLUG_SUFFIXES, "max")


def effort_in_id(local_id: str) -> str | None:
    """The effort a router id spells out at its end, or None.

    >>> effort_in_id("cx/gpt-6-astra-medium")
    'medium'
    >>> effort_in_id("gm/gemini-3.8-flash-max")
    'max'
    >>> effort_in_id("cx/gpt-6-astra") is None
    True
    """
    slug = local_id.rsplit("/", 1)[-1].lower()
    for suffix in _ID_SUFFIXES:
        tail = f"-{suffix}"
        if slug.endswith(tail) and len(slug) > len(tail):
            return suffix
    return None


def resolve(
    matched_id: str,
    local_ids: list[str],
    seat: Effort | None,
    modes: dict[str, str],
) -> tuple[str, Effort | None, EffortHow]:
    """Which catalogue row scores a model for a seat running at `seat`.

    `modes` maps effort -> catalogue id for every published effort of the
    matched model's family. The answer is `(scored_as, its effort, how)`; the
    model's own id never changes (EFFORT.md section 1), only the row read.

    >>> sol = {"low": "o/sol-low", "medium": "o/sol-medium", "max": "o/sol"}

    No effort on the seat: the matched row, as before.

    >>> resolve("o/sol", ["cx/sol"], None, sol)
    ('o/sol', 'max', 'any')

    A family with one setting has nothing to choose between.

    >>> resolve("k/kimi", ["cx/kimi"], "medium", {})
    ('k/kimi', None, 'one')

    A router id that names its effort says what it is.

    >>> resolve("o/sol-low", ["cx/sol-low"], "high", sol)
    ('o/sol-low', 'low', 'id')

    The seat's effort is published.

    >>> resolve("o/sol", ["cx/sol"], "medium", sol)
    ('o/sol-medium', 'medium', 'exact')

    It is not: the nearest below, else the nearest above.

    >>> resolve("o/sol", ["cx/sol"], "high", sol)
    ('o/sol-medium', 'medium', 'nearest_below')
    >>> resolve("o/sol", ["cx/sol"], "non-reasoning", sol)
    ('o/sol-low', 'low', 'nearest_above')
    """
    by_id = {row: mode for mode, row in modes.items()}
    own = cast("Effort | None", by_id.get(matched_id))
    if seat is None:
        return matched_id, own, "any"
    if len(modes) <= 1:
        return matched_id, own, "one"
    named = [effort_in_id(local_id) for local_id in local_ids]
    if named and all(mode is not None for mode in named):
        return matched_id, own or cast("Effort", named[0]), "id"
    if seat in modes:
        return modes[seat], seat, "exact"
    rank = _RANK[seat]
    below = [mode for mode in modes if _RANK.get(mode, -1) < rank]
    if below:
        mode = max(below, key=lambda m: _RANK[m])
        return modes[mode], cast("Effort", mode), "nearest_below"
    above = [mode for mode in modes if _RANK.get(mode, len(EFFORT_ORDER)) > rank]
    mode = min(above, key=lambda m: _RANK[m])
    return modes[mode], cast("Effort", mode), "nearest_above"


def family_modes(rows: list[tuple[str, str | None, str | None]]) -> dict[str, dict[str, str]]:
    """`family -> {effort: catalogue id}` from `(id, family, effort)` rows.

    A row with no family folds by `family_of`; a row with no effort is a
    model with one setting and adds no mode. Built once per ranking.

    >>> family_modes([("o/sol", "o/sol", "max"), ("o/sol-low", "o/sol", "low"),
    ...               ("k/kimi", None, None)])
    {'o/sol': {'max': 'o/sol', 'low': 'o/sol-low'}}
    """
    out: dict[str, dict[str, str]] = {}
    for model_id, family, mode in rows:
        if mode is None or mode not in _RANK:
            continue
        held = out.setdefault(family or family_of(model_id), {})
        # two rows claiming one mode: the one whose id is the family's own
        # spelling of it wins, so the answer does not depend on row order
        if mode not in held or model_id == family:
            held[mode] = model_id
    return out
