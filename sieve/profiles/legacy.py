"""Reading profiles written before a profile was only its weights.

A profile used to carry a policy, a shape, a `require` block, a preferred
effort mode and a list of targets; its settings carried a floor, a price
sensitivity, an experience weight, an auto-apply switch and per-axis bounds.
All of it is gone. What is not gone is the YAML on disk, the rows in a
database that has been running for months, and whatever scripts Mohamad wrote
against the old shape -- so every door that reads one of those keys accepts it,
drops it, and *says* it dropped it.

For one release. After that, a key nobody sends is a key nobody has to read.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Profile keys that no longer mean anything. `policy.chain` is read once, as
#: the old spelling of `ship`, before the policy goes.
RETIRED_PROFILE_KEYS: frozenset[str] = frozenset(
    {"require", "shape", "policy", "targets", "prefer_effort"}
)

#: Settings keys that no longer mean anything. `list_length` is read once, as
#: the old spelling of `ship`.
RETIRED_SETTINGS_KEYS: frozenset[str] = frozenset(
    {"floor_score", "price_sensitivity", "experience_weight", "auto_apply", "cost_multipliers"}
)

SHIP_MIN = 1
SHIP_MAX = 10


def clamp_ship(value: Any) -> int | None:
    """`ship` as a whole number in 1..10, or None when it is not a number."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(SHIP_MIN, min(SHIP_MAX, number))


def _ignored(key: str) -> str:
    return f"{key!r} is no longer part of a profile and was ignored"


def clean_profile(raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A profile document as it is written today, and what was dropped."""
    body = dict(raw)
    warnings: list[str] = []

    ship = clamp_ship(body.get("ship"))
    policy = body.get("policy")
    if ship is None and isinstance(policy, Mapping):
        ship = clamp_ship(policy.get("chain"))

    for key in sorted(RETIRED_PROFILE_KEYS & set(body)):
        body.pop(key, None)
        warnings.append(_ignored(key))

    if ship is not None:
        body["ship"] = ship
    else:
        body.pop("ship", None)
    return body, warnings


def clean_weights(raw: Any) -> dict[str, float | None]:
    """Weights as plain numbers, whichever spelling they arrived in.

    A weight used to be `{"value": 0.5, "min": 0, "max": 1, "locked": false}`.
    The bounds and the lock are gone -- a weight a slider cannot move is a
    weight nobody can read -- so only the number survives. `None` is kept as
    itself: it is how a cleared row says "take this axis off the profile".
    """
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, float | None] = {}
    for axis, value in raw.items():
        if value is None:
            out[str(axis)] = None
        elif isinstance(value, Mapping):
            held = value.get("value")
            out[str(axis)] = None if held is None else float(held)
        else:
            out[str(axis)] = float(value)
    return out


def clean_settings(raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A settings document (or a patch) as it is written today, and what was dropped."""
    body = dict(raw)
    warnings: list[str] = []

    ship = clamp_ship(body.get("ship"))
    if ship is None:
        ship = clamp_ship(body.get("list_length"))
    body.pop("list_length", None)

    for key in sorted(RETIRED_SETTINGS_KEYS & set(body)):
        body.pop(key, None)
        warnings.append(_ignored(key))

    if "weights" in body:
        body["weights"] = clean_weights(body["weights"])
    if ship is not None:
        body["ship"] = ship
    else:
        body.pop("ship", None)
    return body, warnings
