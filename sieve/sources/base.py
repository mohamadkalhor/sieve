"""Sources: connectors that pull measurements.

The `Source` protocol and the config and result models live in
`sieve/contracts.py`; this module re-exports them so a third-party connector
imports one name, and holds what every connector needs: the unit table, a
safe float reader, and an `Observation` factory that stamps `pulled_at` once
per pull.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sieve.contracts import (
    Capability,
    Modality,
    ModelRef,
    Observation,
    Price,
    PullResult,
    RateLimit,
    Source,
    SourceConfig,
    Unit,
)

__all__ = [
    "Capability",
    "ModelRef",
    "Observation",
    "Price",
    "PullResult",
    "RateLimit",
    "Source",
    "SourceConfig",
    "as_float",
    "as_int",
    "make_observation",
    "slugify",
    "unit_for_field",
    "utcnow",
]

#: fields whose value is a 0-100 index rather than a 0-1 fraction.
_INDEX_SUFFIXES = ("_index",)

#: fields that are plainly not a score.
_EXPLICIT_UNITS: dict[str, Unit] = {
    "median_output_tokens_per_second": "tokens_per_s",
    "median_time_to_first_token_seconds": "seconds",
    "elo": "elo",
    "rank": "count",
    "appearances": "count",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def slugify(text: str) -> str:
    """`Moving camera` -> `moving_camera`, for a category turned into a field."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.strip().lower())).strip("_")


def as_float(value: Any) -> float | None:
    """A number, or None. Never a silent zero: a missing value stays missing."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def as_int(value: Any) -> int | None:
    found = as_float(value)
    return int(found) if found is not None else None


def unit_for_field(field: str, default: Unit = "fraction") -> Unit:
    """The unit a source field is measured in.

    Percentile normalisation makes the unit cosmetic for scoring, but it is what
    a person reads on the model page, so it has to be right.
    """
    if field in _EXPLICIT_UNITS:
        return _EXPLICIT_UNITS[field]
    if field.startswith("elo:"):
        return "elo"
    if any(field.endswith(suffix) for suffix in _INDEX_SUFFIXES):
        return "index_0_100"
    return default


def make_observation(
    *,
    model_id: str,
    modality: Modality,
    source: str,
    field: str,
    value: Any,
    unit: Unit | None = None,
    n: int | None = None,
    ci95: float | None = None,
    observed_at: datetime | None = None,
    pulled_at: datetime | None = None,
) -> Observation | None:
    """One observation, or None when the source did not measure it."""
    number = as_float(value)
    if number is None:
        return None
    stamp = pulled_at or utcnow()
    return Observation(
        model_id=model_id,
        modality=modality,
        source=source,
        field=field,
        value=number,
        unit=unit or unit_for_field(field),
        n=n,
        ci95=ci95,
        observed_at=observed_at or stamp,
        pulled_at=stamp,
    )


def collect(observations: Iterable[Observation | None]) -> list[Observation]:
    """Drop the misses. A field a source did not publish is simply absent."""
    return [o for o in observations if o is not None]
