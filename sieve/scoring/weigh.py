"""Score a model against a profile.

    score(m,p) = sum over axes of W_a * axis_a(m)
    conf(m,p)  = sum over axes of W_a * coverage_a(m)

The rules are fixed by `tests/fixtures/rank_case.json`, which a pytest here and
a vitest in the web app both assert against, because the profile editor
re-ranks in the browser and must agree with the server to the last decimal.

An unmeasured axis contributes 0 to the score and 0 to its share of confidence.
That is not a silent zero: the loss is visible in `confidence`, and a model
under the profile's `min_confidence` is excluded by name rather than quietly
ranked low.
"""

from __future__ import annotations

from sieve.contracts import Price, Profile, Shape

AxesByModel = dict[str, dict[str, tuple[float | None, float]]]
Scored = dict[str, tuple[float, float, dict[str, float]]]


def weigh(profile: Profile, axes_by_model: AxesByModel) -> Scored:
    """`{model_id: (score, confidence, contributions)}`.

    Axes the profile does not weight are ignored entirely, even when the model
    has values for them.
    """
    weights = profile.weights
    out: Scored = {}

    for model_id, axes in axes_by_model.items():
        contributions: dict[str, float] = {}
        confidence = 0.0
        for axis, weight in weights.items():
            value, coverage = axes.get(axis, (None, 0.0))
            contributions[axis] = weight * (value if value is not None else 0.0)
            confidence += weight * (coverage if value is not None else 0.0)
        out[model_id] = (sum(contributions.values()), confidence, contributions)

    return out


def rank_order(scored: Scored) -> list[str]:
    """Best first; an exact tie is broken by model id, ascending.

    The tie-break has to be stated somewhere, or two runs over the same numbers
    could disagree and log a switch that means nothing.
    """
    return sorted(scored, key=lambda model_id: (-scored[model_id][0], model_id))


def cost_per_task(
    price: Price | None, shape: Shape, *, tokens_out: float | None = None
) -> float | None:
    """What one task on this model costs, in USD, at the profile's shape.

    None when the price or the shape is missing: an unknown cost must stay
    unknown, so the `cost` axis reports it unmeasured rather than free.

    `tokens_out`, when given, replaces the shape's declared output tokens with
    what this model **actually burned** on real calls. It is the only thing that
    can separate the effort modes of one model: every mode is served at the same
    price per token, so the rate is identical and the token count is the whole
    difference. See PLAN 2.1.
    """
    if price is None:
        return None

    if price.unit == "usd_per_1m_tokens":
        if shape.in_tokens is None and shape.out_tokens is None:
            return None
        cached_share = shape.cached or 0.0
        in_tokens = float(shape.in_tokens or 0)
        out_tokens = float(tokens_out if tokens_out is not None else (shape.out_tokens or 0))

        fresh_in = in_tokens * (1.0 - cached_share)
        cached_in = in_tokens * cached_share

        total = 0.0
        if price.input is not None:
            total += fresh_in * price.input / 1_000_000
            # no cached price published: cached tokens cost the full rate
            cached_rate = price.cached_input if price.cached_input is not None else price.input
            total += cached_in * cached_rate / 1_000_000
        elif cached_in and price.cached_input is not None:
            total += cached_in * price.cached_input / 1_000_000
        if price.output is not None:
            total += out_tokens * price.output / 1_000_000
        return total if total > 0 else None

    if price.per_unit is None:
        return None

    if price.unit == "usd_per_image":
        images = shape.images
        return images * price.per_unit if images else None
    if price.unit == "usd_per_second":
        seconds = shape.seconds
        return seconds * price.per_unit if seconds else None
    if price.unit == "usd_per_1m_chars":
        chars = shape.chars
        return (chars / 1_000_000) * price.per_unit if chars else None

    return None
