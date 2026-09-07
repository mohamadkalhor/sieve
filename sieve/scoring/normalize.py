"""Percentile normalisation and the field transforms.

Percentile is what lets an Elo, a 0-1 accuracy and a 0-100 index combine in one
axis: only the order survives, so the scale a source happens to publish in stops
mattering. It is mid-rank, so tied models get the same percentile and neither is
pushed above the other by accident.

A value a source did not publish is `None` all the way through. It is never
turned into a zero, which would rank the model as *worst* on that field rather
than *unmeasured* -- exactly the silent zero the design forbids.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from sieve.contracts import Transform


def apply_transform(value: float | None, transform: Transform) -> float | None:
    """Put a field on a scale where higher is better, before ranking.

    `neg_log` is for cost and latency: the gap between $0.10 and $1.00 matters
    far more than the gap between $10 and $11, and negating makes cheap rank high.
    """
    if value is None:
        return None
    if transform == "identity":
        return value
    if transform == "invert":
        return -value
    if transform in ("log", "neg_log"):
        if value <= 0:
            # log of zero or less has no meaning; report it unmeasured rather
            # than clamping, so the coverage loss is visible.
            return None
        return math.log(value) if transform == "log" else -math.log(value)
    return value


def percentile(values: Sequence[float | None]) -> list[float | None]:
    """Mid-rank percentiles in 0..1, `None` preserved in place.

    The best measured value is 1.0 and the worst is 0.0; ties share a rank. A
    `None` takes no part: it neither occupies a rank nor shifts anyone else's,
    which is the property A's tests assert.
    """
    measured = [(index, value) for index, value in enumerate(values) if value is not None]
    out: list[float | None] = [None] * len(values)
    if not measured:
        return out

    count = len(measured)
    if count == 1:
        out[measured[0][0]] = 0.5  # one measured model is neither best nor worst
        return out

    ordered = sorted(measured, key=lambda pair: pair[1])
    position = 0
    while position < count:
        end = position
        while end + 1 < count and ordered[end + 1][1] == ordered[position][1]:
            end += 1
        # ranks are 1-based; a tie takes the average of the ranks it spans
        mid_rank = (position + 1 + end + 1) / 2
        share = (mid_rank - 1) / (count - 1)
        for index, _ in ordered[position : end + 1]:
            out[index] = share
        position = end + 1
    return out


def percentile_map(
    values: dict[str, float | None], transform: Transform = "identity"
) -> dict[str, float | None]:
    """`percentile` over a mapping, with the transform applied first."""
    keys = list(values)
    transformed = [apply_transform(values[key], transform) for key in keys]
    return dict(zip(keys, percentile(transformed), strict=True))
