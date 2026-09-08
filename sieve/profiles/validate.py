"""Validate a profile against the axes that actually exist.

`sieve check` fails on an unknown field or a weight set that does not sum to 1
(PLAN section 12), and every problem is reported as one plain sentence naming
the profile, so a person can fix the file without reading code.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sieve.catalog.effort import EFFORT_ORDER
from sieve.contracts import Modality, Profile

WEIGHT_TOLERANCE = 0.001

#: keys `require` accepts (PLAN section 4).
KNOWN_REQUIRE = frozenset(
    {
        "tools",
        "reasoning",
        "structured_output",
        "context_min",
        "input_modalities",
        "output_modalities",
        "min_axis",
        "min_appearances",
    }
)

INPUT_MODALITIES = frozenset({"text", "image", "audio", "video", "file"})


def validate_profile(
    profile: Profile, axis_names: set[tuple[str, str]] | None = None
) -> Iterator[str]:
    """Yield one sentence per problem. No problems means the profile is good.

    `axis_names` holds `(modality, axis)` pairs; pass None to skip the axis
    existence check (useful before the axis files have landed).
    """
    where = f"profile {profile.name!r}"

    if not profile.weights:
        yield f"{where} has no weights"
    total = sum(profile.weights.values())
    if profile.weights and abs(total - 1.0) > WEIGHT_TOLERANCE:
        yield f"{where}: weights sum to {total:.4f}, must be 1 +/- {WEIGHT_TOLERANCE}"

    for axis, weight in sorted(profile.weights.items()):
        if weight <= 0:
            yield f"{where}: weight for axis {axis!r} is {weight}, must be greater than 0"
        if axis_names is not None and (profile.modality, axis) not in axis_names:
            yield (
                f"{where}: unknown axis {axis!r} for modality {profile.modality!r} "
                f"-- add data/axes/{profile.modality}/{axis}.yaml or fix the name"
            )

    yield from _validate_effort(profile, where)
    yield from _validate_require(profile, where, axis_names)
    yield from _validate_shape(profile, where)
    yield from _validate_policy(profile, where)


def _validate_require(
    profile: Profile, where: str, axis_names: set[tuple[str, str]] | None
) -> Iterator[str]:
    for key, value in sorted(profile.require.items()):
        if key not in KNOWN_REQUIRE:
            yield (
                f"{where}: unknown constraint {key!r}; known constraints are "
                f"{', '.join(sorted(KNOWN_REQUIRE))}"
            )
            continue
        if key in ("tools", "reasoning", "structured_output") and not isinstance(value, bool):
            yield f"{where}: constraint {key!r} must be true or false, got {value!r}"
        if key in ("context_min", "min_appearances") and not isinstance(value, int):
            yield f"{where}: constraint {key!r} must be a whole number, got {value!r}"
        if key in ("input_modalities", "output_modalities"):
            yield from _validate_modality_list(where, key, value)
        if key == "min_axis":
            yield from _validate_min_axis(profile, where, value, axis_names)


def _validate_modality_list(where: str, key: str, value: Any) -> Iterator[str]:
    if not isinstance(value, list):
        yield f"{where}: constraint {key!r} must be a list, got {value!r}"
        return
    for item in value:
        if item not in INPUT_MODALITIES:
            yield (
                f"{where}: constraint {key!r} names {item!r}; known values are "
                f"{', '.join(sorted(INPUT_MODALITIES))}"
            )


def _validate_min_axis(
    profile: Profile, where: str, value: Any, axis_names: set[tuple[str, str]] | None
) -> Iterator[str]:
    if not isinstance(value, dict):
        yield f"{where}: constraint 'min_axis' must be a mapping of axis to floor"
        return
    for axis, floor in sorted(value.items()):
        if not isinstance(floor, int | float) or not 0.0 <= float(floor) <= 1.0:
            yield f"{where}: min_axis floor for {axis!r} must be between 0 and 1, got {floor!r}"
        if axis_names is not None and (profile.modality, axis) not in axis_names:
            yield f"{where}: min_axis names unknown axis {axis!r} for {profile.modality!r}"


def _validate_shape(profile: Profile, where: str) -> Iterator[str]:
    shape = profile.shape
    token_shaped: tuple[Modality, ...] = ("llm",)
    if profile.modality in token_shaped and shape.in_tokens is None and shape.out_tokens is None:
        yield (
            f"{where}: an llm profile needs a shape with `in` and `out` tokens, "
            "or cost cannot be turned into cost per task"
        )
    if shape.cached is not None and not 0.0 <= shape.cached <= 1.0:
        yield f"{where}: shape.cached is a share between 0 and 1, got {shape.cached}"
    for field in ("images", "seconds", "chars", "in_tokens", "out_tokens"):
        held = getattr(shape, field)
        if held is not None and held < 0:
            yield f"{where}: shape.{field} cannot be negative"


def _validate_policy(profile: Profile, where: str) -> Iterator[str]:
    policy = profile.policy
    if policy.margin < 0:
        yield f"{where}: policy.margin cannot be negative"
    if policy.chain < 1:
        yield f"{where}: policy.chain must be at least 1"
    if not 0.0 <= policy.min_confidence <= 1.0:
        yield f"{where}: policy.min_confidence must be between 0 and 1"
    if not 0.0 <= policy.suspend_below_health <= 1.0:
        yield f"{where}: policy.suspend_below_health must be between 0 and 1"
    if policy.max_tenure_days < 0:
        yield f"{where}: policy.max_tenure_days cannot be negative"


def _validate_effort(profile: Profile, where: str) -> Iterator[str]:
    """`prefer_effort` is a mode name or one of two words, never free text.

    A typo here is silent and expensive: an unrecognised value would fall
    through to the pinned-mode branch, find no mode of that name, and seat the
    lowest one -- so `prefer_effort: higest` would quietly buy the cheapest
    model in every family.
    """
    prefer = profile.prefer_effort
    if prefer is None:
        return

    allowed = {"best", "cheapest_clearing", *EFFORT_ORDER}
    if prefer not in allowed:
        yield (
            f"{where}: prefer_effort {prefer!r} is not a mode -- "
            f"use one of {', '.join(sorted(allowed))}"
        )
        return

    # Effort modes are published for language models. A media profile setting
    # one would be silently ignored, and silence is how a setting rots.
    if profile.modality != "llm":
        yield (
            f"{where}: prefer_effort is only meaningful for an llm profile, "
            f"and this one is {profile.modality!r} -- remove it"
        )
        return

    # `cheapest_clearing` with nothing to clear is a bug, not a setting: with no
    # floor, the lowest mode of every family always clears, so it seats the
    # bottom of the ladder every time and calls that a decision.
    if prefer == "cheapest_clearing":
        floors = profile.require.get("min_axis") or {}
        weighted_floor = any(axis in profile.weights for axis in floors)
        other_constraint = any(key != "min_axis" for key in profile.require)
        if not weighted_floor and not other_constraint:
            yield (
                f"{where}: prefer_effort is cheapest_clearing but the profile sets no "
                "floor to clear -- add a `require` constraint, or a `min_axis` on an "
                "axis it weights, or the lowest mode of every family always wins"
            )
