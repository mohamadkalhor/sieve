"""Validate a profile against the axes that actually exist.

`sieve check` fails on an unknown axis or a weight set that does not sum to 1
(PLAN section 12), and every problem is reported as one plain sentence naming
the profile, so a person can fix the file without reading code.

There is much less to be wrong about than there used to be. A profile is its
weights and how many models it ships; the constraints, the shape, the policy
and the preferred effort mode -- and every rule about how they had to agree
with each other -- are gone.
"""

from __future__ import annotations

from collections.abc import Iterator

from sieve.contracts import SHIP_MIN, Profile

WEIGHT_TOLERANCE = 0.001


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

    if profile.ship < SHIP_MIN:
        yield f"{where}: ship must be at least {SHIP_MIN}, got {profile.ship}"
    if profile.mode == "manual" and not profile.manual:
        yield f"{where}: manual mode with an empty list ships nothing"
