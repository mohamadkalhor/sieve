"""Regenerate `rank_case.json`.

The values are hand-set; only the expected numbers are computed, so the fixture
can never drift from its own arithmetic. Run:

    uv run python tests/fixtures/make_rank_case.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("rank_case.json")

WEIGHTS = {"a": 0.4, "b": 0.3, "c": 0.3}
MIN_CONFIDENCE = 0.75

# id -> axis -> (value, coverage). d, e and f are unweighted on purpose: a
# correct implementation must ignore them entirely.
MODELS: dict[str, dict[str, tuple[float | None, float]]] = {
    "m01": {
        "a": (0.92, 1.0),
        "b": (0.81, 1.0),
        "c": (0.55, 1.0),
        "d": (0.10, 1.0),
        "e": (0.20, 1.0),
        "f": (0.30, 1.0),
    },
    "m02": {
        "a": (0.88, 1.0),
        "b": (0.90, 1.0),
        "c": (0.60, 1.0),
        "d": (0.90, 1.0),
        "e": (0.15, 1.0),
        "f": (0.45, 1.0),
    },
    "m03": {
        "a": (0.95, 1.0),
        "b": (0.70, 1.0),
        "c": (0.40, 1.0),
        "d": (0.55, 1.0),
        "e": (0.60, 1.0),
        "f": (0.05, 1.0),
    },
    "m04": {
        "a": (0.60, 1.0),
        "b": (0.95, 1.0),
        "c": (0.90, 1.0),
        "d": (0.35, 1.0),
        "e": (0.85, 1.0),
        "f": (0.65, 1.0),
    },
    # m05 ties m03 exactly at 0.710: the tie-break is model id, ascending.
    "m05": {
        "a": (0.68, 1.0),
        "b": (0.72, 1.0),
        "c": (0.74, 1.0),
        "d": (0.25, 1.0),
        "e": (0.45, 1.0),
        "f": (0.75, 1.0),
    },
    "m06": {
        "a": (0.80, 1.0),
        "b": (0.60, 1.0),
        "c": (0.60, 1.0),
        "d": (0.70, 1.0),
        "e": (0.30, 1.0),
        "f": (0.20, 1.0),
    },
    "m07": {
        "a": (0.50, 1.0),
        "b": (0.50, 1.0),
        "c": (0.95, 1.0),
        "d": (0.40, 1.0),
        "e": (0.95, 1.0),
        "f": (0.50, 1.0),
    },
    # best on a by a distance, and still last: a weight of 0.4 is not a veto.
    "m08": {
        "a": (0.99, 1.0),
        "b": (0.40, 1.0),
        "c": (0.30, 1.0),
        "d": (0.05, 1.0),
        "e": (0.10, 1.0),
        "f": (0.90, 1.0),
    },
    "m09": {
        "a": (0.30, 1.0),
        "b": (0.99, 1.0),
        "c": (0.99, 1.0),
        "d": (0.60, 1.0),
        "e": (0.70, 1.0),
        "f": (0.35, 1.0),
    },
    "m10": {
        "a": (0.65, 1.0),
        "b": (0.75, 1.0),
        "c": (0.85, 1.0),
        "d": (0.50, 1.0),
        "e": (0.55, 1.0),
        "f": (0.60, 1.0),
    },
    # c unmeasured: it contributes nothing to the score and costs 0.30 of the
    # confidence, which drops m11 under the floor. Never a silent zero -- the
    # loss is visible in `confidence` and in `excluded_by`.
    "m11": {
        "a": (0.90, 1.0),
        "b": (0.90, 1.0),
        "c": (None, 0.0),
        "d": (0.80, 1.0),
        "e": (0.40, 1.0),
        "f": (0.55, 1.0),
    },
    # half of b's weight was measured: the value still counts, confidence dips.
    "m12": {
        "a": (0.85, 1.0),
        "b": (0.80, 0.5),
        "c": (0.78, 1.0),
        "d": (0.45, 1.0),
        "e": (0.50, 1.0),
        "f": (0.40, 1.0),
    },
}


def score(axes: dict[str, tuple[float | None, float]]) -> tuple[float, float, dict[str, float]]:
    contributions = {axis: weight * (axes[axis][0] or 0.0) for axis, weight in WEIGHTS.items()}
    confidence = sum(weight * axes[axis][1] for axis, weight in WEIGHTS.items())
    return sum(contributions.values()), confidence, contributions


def build() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    scores: dict[str, float] = {}
    confidences: dict[str, float] = {}
    contributions: dict[str, dict[str, float]] = {}

    for model_id, axes in MODELS.items():
        value, confidence, parts = score(axes)
        scores[model_id] = round(value, 10)
        confidences[model_id] = round(confidence, 10)
        contributions[model_id] = {k: round(v, 10) for k, v in parts.items()}
        rows.append(
            {
                "id": model_id,
                "axes": {
                    axis: {"value": v, "coverage": c} for axis, (v, c) in sorted(axes.items())
                },
            }
        )

    excluded = {m: "min_confidence" for m, c in confidences.items() if c < MIN_CONFIDENCE}
    ranked = sorted(
        (m for m in MODELS if m not in excluded),
        key=lambda m: (-scores[m], m),
    )

    return {
        "$comment": (
            "Shared fixture for sieve/scoring/weigh.py and web/src/lib/rank/weigh.ts. "
            "Both must reproduce `expected` exactly, to 1e-6. Regenerate with "
            "tests/fixtures/make_rank_case.py; never hand-edit the expected block."
        ),
        "rules": {
            "score": "sum over weighted axes of weight * value; an unmeasured axis contributes 0",
            "confidence": "sum over weighted axes of weight * coverage",
            "unweighted_axes": "axes absent from `weights` are ignored entirely (d, e, f here)",
            "unmeasured_axis": (
                "value null contributes 0 to the score and 0 to that axis's share of "
                "confidence -- the loss must be visible in `confidence`, never silent"
            ),
            "min_confidence": (
                "a model whose confidence is below policy.min_confidence is excluded with "
                "excluded_by = 'min_confidence' and keeps position 0"
            ),
            "tie_break": "equal scores rank by model id ascending (m03 before m05)",
        },
        "profile": {
            "name": "rank_case",
            "modality": "llm",
            "purpose": "the shared scoring fixture",
            "weights": WEIGHTS,
            "policy": {"min_confidence": MIN_CONFIDENCE, "chain": 5},
        },
        "models": rows,
        "expected": {
            "order": ranked,
            "scores": scores,
            "confidence": confidences,
            "contributions": contributions,
            "excluded": excluded,
        },
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
