"""Scoring: the properties that must hold, then the worked examples.

The properties matter more than the examples. A ranking people act on has to
behave predictably when someone drags a slider, and "raising a weight never
demotes the model that is best on that axis" is the promise the profile editor
makes with every pixel of movement.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sieve.contracts import (
    AxisScore,
    Chain,
    Policy,
    Profile,
    Rank,
    Ranking,
    Shape,
    TelemetryEvent,
)
from sieve.scoring import (
    apply_transform,
    cost_per_task,
    decide,
    explain,
    health,
    pareto_prune,
    percentile,
    weigh,
)
from sieve.scoring.health import health_series
from sieve.scoring.weigh import rank_order

FIXTURE = Path(__file__).parent / "fixtures" / "rank_case.json"
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# percentile
# --------------------------------------------------------------------------- #


def test_percentile_is_mid_rank_and_spans_zero_to_one() -> None:
    assert percentile([1.0, 2.0, 3.0]) == [0.0, 0.5, 1.0]
    assert percentile([5.0]) == [0.5], "one measured model is neither best nor worst"
    assert percentile([]) == []


def test_a_tie_shares_a_rank() -> None:
    found = percentile([1.0, 2.0, 2.0, 3.0])
    assert found[1] == found[2], "tied values must not be ordered by accident"
    assert found[0] == 0.0 and found[3] == 1.0


@pytest.mark.parametrize(
    "transform",
    [lambda v: v * 7.0, lambda v: v + 100.0, lambda v: math.log(v), lambda v: v**3],
)
def test_percentile_is_invariant_under_monotone_transforms(transform: Any) -> None:
    """An Elo, a 0-1 accuracy and a 0-100 index must combine; only order counts."""
    values = [1.0, 2.5, 4.0, 9.0, 12.0]
    assert percentile(values) == percentile([transform(v) for v in values])


def test_none_takes_no_rank_and_shifts_nobody() -> None:
    without = percentile([1.0, 2.0, 3.0])
    with_gap = percentile([1.0, None, 2.0, 3.0])
    assert with_gap[1] is None
    assert [with_gap[0], with_gap[2], with_gap[3]] == without


def test_neg_log_makes_cheap_rank_high_and_refuses_impossible_values() -> None:
    cheap, dear = apply_transform(0.5, "neg_log"), apply_transform(50.0, "neg_log")
    assert cheap is not None and dear is not None and cheap > dear
    assert apply_transform(0.0, "neg_log") is None, "log of zero is unmeasured, not free"
    assert apply_transform(-1.0, "log") is None
    assert apply_transform(3.0, "invert") == -3.0


# --------------------------------------------------------------------------- #
# the shared fixture
# --------------------------------------------------------------------------- #


def _case() -> dict[str, Any]:
    body: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return body


def _profile_from(case: dict[str, Any]) -> Profile:
    body = case["profile"]
    return Profile(
        name=body["name"],
        modality=body["modality"],
        purpose=body["purpose"],
        weights=body["weights"],
        policy=Policy(**body["policy"]),
    )


def _axes_by_model(case: dict[str, Any]) -> dict[str, dict[str, tuple[float | None, float]]]:
    return {
        row["id"]: {axis: (body["value"], body["coverage"]) for axis, body in row["axes"].items()}
        for row in case["models"]
    }


def test_weigh_reproduces_the_shared_fixture_exactly() -> None:
    """The same file the web's weigh.ts asserts against, to 1e-6."""
    case = _case()
    profile = _profile_from(case)
    expected = case["expected"]

    scored = weigh(profile, _axes_by_model(case))

    for model_id, (score, confidence, contributions) in scored.items():
        assert score == pytest.approx(expected["scores"][model_id], abs=1e-6)
        assert confidence == pytest.approx(expected["confidence"][model_id], abs=1e-6)
        for axis, value in contributions.items():
            assert value == pytest.approx(expected["contributions"][model_id][axis], abs=1e-6)

    kept = {m: v for m, v in scored.items() if v[1] >= profile.policy.min_confidence}
    assert rank_order(kept) == expected["order"]


def test_the_confidence_floor_excludes_the_unmeasured_model() -> None:
    case = _case()
    profile = _profile_from(case)
    scored = weigh(profile, _axes_by_model(case))

    _score, confidence, contributions = scored["m11"]
    assert contributions["c"] == 0.0, "an unmeasured axis contributes nothing"
    assert confidence == pytest.approx(0.7, abs=1e-9), "and the loss shows up here"
    assert confidence < profile.policy.min_confidence
    assert "m11" not in case["expected"]["order"]


def test_unweighted_axes_are_ignored_entirely() -> None:
    case = _case()
    profile = _profile_from(case)
    axes = _axes_by_model(case)
    scored = weigh(profile, axes)

    for row in axes.values():
        row["d"] = (0.0, 1.0)
        row["e"] = (0.0, 1.0)
        row["f"] = (0.0, 1.0)
    assert weigh(profile, axes) == scored


def test_an_exact_tie_breaks_by_model_id() -> None:
    case = _case()
    expected = case["expected"]
    assert expected["scores"]["m03"] == expected["scores"]["m05"]
    assert expected["order"].index("m03") < expected["order"].index("m05")


# --------------------------------------------------------------------------- #
# monotonicity
# --------------------------------------------------------------------------- #


def test_raising_a_weight_never_demotes_the_model_best_on_that_axis() -> None:
    """The promise every slider in the profile editor makes."""
    case = _case()
    axes = _axes_by_model(case)
    best_on_a = max(axes, key=lambda m: axes[m]["a"][0] or 0.0)

    previous = None
    for share in (0.1, 0.2, 0.4, 0.6, 0.8, 0.95):
        rest = (1.0 - share) / 2
        profile = _profile_from(case).model_copy(
            update={"weights": {"a": share, "b": rest, "c": rest}}
        )
        order = rank_order(weigh(profile, axes))
        position = order.index(best_on_a)
        if previous is not None:
            assert position <= previous, f"raising `a` demoted the best model on `a` at {share}"
        previous = position
    assert previous == 0, "at weight 0.95 on `a`, the best model on `a` must lead"


# --------------------------------------------------------------------------- #
# Pareto
# --------------------------------------------------------------------------- #


def test_pareto_prunes_only_what_is_strictly_beaten() -> None:
    weights = {"a": 0.5, "b": 0.5}
    rows: dict[str, dict[str, float | None]] = {
        "good": {"a": 0.9, "b": 0.9},
        "equal": {"a": 0.9, "b": 0.9},
        "worse_on_one": {"a": 0.9, "b": 0.5},
        "trade_off": {"a": 0.4, "b": 0.99},
    }
    dominated = pareto_prune(rows, weights)

    assert "equal" not in dominated, "a model equal on every axis is not dominated"
    assert dominated.get("worse_on_one") in ("good", "equal")
    assert "trade_off" not in dominated, "better on one axis is never dominated"


def test_pareto_skips_axes_nobody_measured() -> None:
    rows: dict[str, dict[str, float | None]] = {
        "measured": {"a": 0.9, "b": None},
        "new": {"a": 0.5, "b": None},
    }
    dominated = pareto_prune(rows, {"a": 0.5, "b": 0.5})
    assert dominated == {"new": "measured"}, "the comparison uses the axis that exists"

    unknown: dict[str, dict[str, float | None]] = {
        "one": {"a": None},
        "two": {"a": None},
    }
    assert pareto_prune(unknown, {"a": 1.0}) == {}, "no evidence prunes nothing"


def test_pareto_ignores_axes_the_profile_does_not_weight() -> None:
    rows: dict[str, dict[str, float | None]] = {
        "x": {"a": 0.9, "ignored": 0.1},
        "y": {"a": 0.5, "ignored": 0.99},
    }
    assert pareto_prune(rows, {"a": 1.0}) == {"y": "x"}


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #


def _events(model: str, *, ok: int, errors: int = 0, limited: int = 0) -> list[TelemetryEvent]:
    out = [TelemetryEvent(model=model, ok=True, status=200, at=NOW) for _ in range(ok)]
    out += [TelemetryEvent(model=model, ok=False, status=500, at=NOW) for _ in range(errors)]
    out += [TelemetryEvent(model=model, ok=False, status=429, at=NOW) for _ in range(limited)]
    return out


def test_health_is_one_without_evidence_and_falls_with_errors() -> None:
    assert health([], NOW) == {}, "no telemetry is not bad telemetry"

    assert health(_events("a/b", ok=10), NOW)["a/b"] == 1.0
    assert health(_events("a/b", ok=8, errors=2), NOW)["a/b"] == pytest.approx(0.8)
    # a 429 counts half: it is the gateway's queue, not the model failing, so
    # being throttled must never score worse than failing outright
    assert health(_events("a/b", ok=8, limited=2), NOW)["a/b"] == pytest.approx(0.9)
    assert (
        health(_events("a/b", ok=8, limited=2), NOW)["a/b"]
        > health(_events("a/b", ok=8, errors=2), NOW)["a/b"]
    )
    assert health(_events("a/b", ok=0, errors=10), NOW)["a/b"] == 0.0


def test_health_only_looks_at_the_last_day() -> None:
    stale = [TelemetryEvent(model="a/b", ok=False, status=500, at=NOW - timedelta(days=3))]
    assert health(stale, NOW) == {}


def test_a_quiet_day_in_the_series_is_none_not_perfect() -> None:
    events = [TelemetryEvent(model="a/b", ok=True, at=NOW - timedelta(days=1))]
    series = health_series(events, NOW, days=7)["a/b"]
    assert len(series) == 7
    assert series.count(None) == 6, "a day nobody called is unknown, not healthy"


# --------------------------------------------------------------------------- #
# hysteresis
# --------------------------------------------------------------------------- #


def _rank(model_id: str, final: float, *, health_value: float = 1.0, position: int = 1) -> Rank:
    return Rank(
        position=position,
        model_id=model_id,
        reachable=True,
        local_ids=[f"gw/{model_id.split('/')[-1]}"],
        score=final,
        confidence=1.0,
        health=health_value,
        final=final,
        axes=[AxisScore(axis="a", value=final, coverage=1.0, contribution=final)],
    )


def _ranking(*ranks: Rank) -> Ranking:
    best_first = sorted(ranks, key=lambda r: (-r.final, r.model_id))
    ordered = [r.model_copy(update={"position": i}) for i, r in enumerate(best_first, start=1)]
    return Ranking(profile="coder", modality="llm", computed_at=NOW, snapshot="s1", ranks=ordered)


def _profile(**policy: Any) -> Profile:
    return Profile(
        name="coder",
        modality="llm",
        purpose="agentic coding",
        weights={"a": 1.0},
        policy=Policy(**{"margin": 3.0, "max_tenure_days": 14, **policy}),
    )


def _incumbent(model_id: str, *, days: int = 1) -> Chain:
    return Chain(
        profile="coder",
        computed_at=NOW - timedelta(days=days),
        primary=model_id,
        fallbacks=[],
        incumbent=model_id,
        incumbent_since=NOW - timedelta(days=days),
    )


def test_plus_one_point_holds_at_margin_three() -> None:
    ranking = _ranking(_rank("new/model", 0.81), _rank("old/model", 0.80))
    chain, decision = decide(_profile(), _incumbent("old/model"), ranking, NOW)

    assert decision is not None and decision.kind == "hold"
    assert decision.reason == "hold: challenger new/model +1.0 inside margin 3.0"
    assert chain is not None and chain.primary == "old/model", "a hold changes nothing"


def test_plus_four_points_switches() -> None:
    ranking = _ranking(_rank("new/model", 0.84), _rank("old/model", 0.80))
    chain, decision = decide(_profile(), _incumbent("old/model"), ranking, NOW)

    assert decision is not None and decision.kind == "switch"
    assert decision.reason.startswith("switch: new/model over old/model by +4.0, margin 3.0")
    assert chain is not None and chain.primary == "new/model"
    assert chain.incumbent_since == NOW, "a new primary starts its tenure now"


def test_a_fifteen_day_incumbent_loses_to_a_tenth_of_a_point() -> None:
    ranking = _ranking(_rank("new/model", 0.801), _rank("old/model", 0.80))
    chain, decision = decide(_profile(), _incumbent("old/model", days=15), ranking, NOW)

    assert decision is not None and decision.kind == "switch"
    assert decision.reason.startswith("switch: tenure 15 d > 14 d, margin waived")
    assert chain is not None and chain.primary == "new/model"


def test_health_below_the_threshold_suspends_the_incumbent() -> None:
    ranking = _ranking(_rank("old/model", 0.80 * 0.6, health_value=0.6), _rank("new/model", 0.70))
    chain, decision = decide(
        _profile(suspend_below_health=0.75), _incumbent("old/model"), ranking, NOW
    )

    assert decision is not None and decision.kind == "suspend"
    assert "health 0.60 < 0.75" in decision.reason
    assert chain is not None and chain.primary == "new/model"


def test_a_hold_returns_the_incumbent_chain_untouched() -> None:
    incumbent = _incumbent("old/model", days=2)
    ranking = _ranking(_rank("new/model", 0.815), _rank("old/model", 0.80))
    chain, decision = decide(_profile(), incumbent, ranking, NOW)

    assert decision is not None and decision.kind == "hold"
    assert chain is incumbent, "a hold must not rewrite the chain, not even identically"


def test_decide_is_deterministic() -> None:
    ranking = _ranking(_rank("new/model", 0.84), _rank("old/model", 0.80))
    first = decide(_profile(), _incumbent("old/model"), ranking, NOW)
    second = decide(_profile(), _incumbent("old/model"), ranking, NOW)
    assert first[0] == second[0]
    assert first[1] is not None and second[1] is not None
    assert first[1].reason == second[1].reason and first[1].kind == second[1].kind


def test_the_chain_is_cut_to_the_policy_length() -> None:
    ranking = _ranking(*[_rank(f"m/{i}", 0.9 - i / 100) for i in range(8)])
    chain, _ = decide(_profile(chain=3), None, ranking, NOW)
    assert chain is not None
    assert [chain.primary, *chain.fallbacks] == ["m/0", "m/1", "m/2"]
    assert chain.local["m/0"] == ["gw/0"]


def test_nothing_reachable_is_a_hold_not_a_crash() -> None:
    ranking = Ranking(
        profile="coder",
        modality="llm",
        computed_at=NOW,
        snapshot="s1",
        ranks=[_rank("m/1", 0.9).model_copy(update={"reachable": False})],
    )
    chain, decision = decide(_profile(), None, ranking, NOW)
    assert chain is None
    assert (
        decision is not None and decision.reason == "hold: nothing reachable ranks for this profile"
    )


# --------------------------------------------------------------------------- #
# explain
# --------------------------------------------------------------------------- #


def test_the_flip_line_is_exact_on_a_two_model_case() -> None:
    """Leader wins on `a`, runner-up wins on `cost`: raising cost must flip it."""
    profile = Profile(
        name="coder",
        modality="llm",
        purpose="agentic coding",
        weights={"a": 0.8, "cost": 0.2},
    )
    leader = Rank(
        position=1,
        model_id="expensive/model",
        reachable=True,
        score=0.8,
        confidence=1.0,
        health=1.0,
        final=0.8,
        axes=[
            AxisScore(axis="a", value=1.0, coverage=1.0, contribution=0.8),
            AxisScore(axis="cost", value=0.0, coverage=1.0, contribution=0.0),
        ],
    )
    runner = Rank(
        position=2,
        model_id="cheap/model",
        reachable=True,
        score=0.2,
        confidence=1.0,
        health=1.0,
        final=0.2,
        axes=[
            AxisScore(axis="a", value=0.0, coverage=1.0, contribution=0.0),
            AxisScore(axis="cost", value=1.0, coverage=1.0, contribution=0.2),
        ],
    )
    ranking = Ranking(
        profile="coder", modality="llm", computed_at=NOW, snapshot="s1", ranks=[leader, runner]
    )

    line = explain(ranking, profile)
    assert line is not None

    # Leader wins entirely on `a`, runner-up entirely on `cost`, so either
    # weight reaching 0.50 flips it -- both are one move of 0.30, and either
    # answer is correct. Assert the rule instead of picking a winner: the line
    # names a weighted axis, a target in 0..1, and the runner-up.
    found = re.fullmatch(r"(raise|lower) (a|cost) to (0\.\d\d) and cheap/model leads", line)
    assert found, line
    axis, target = found.group(2), float(found.group(3))
    assert target == pytest.approx(0.5, abs=0.005)

    # and at that weight the runner-up really does lead
    flipped = {axis: target, ("cost" if axis == "a" else "a"): 1.0 - target}
    moved = profile.model_copy(update={"weights": flipped})
    scored = weigh(
        moved,
        {
            "expensive/model": {"a": (1.0, 1.0), "cost": (0.0, 1.0)},
            "cheap/model": {"a": (0.0, 1.0), "cost": (1.0, 1.0)},
        },
    )
    assert scored["cheap/model"][0] >= scored["expensive/model"][0]


def test_explain_says_so_when_no_single_weight_can_flip_it() -> None:
    profile = Profile(name="coder", modality="llm", purpose="x", weights={"a": 0.5, "b": 0.5})
    leader = _rank("best/model", 0.9)
    leader = leader.model_copy(
        update={
            "axes": [
                AxisScore(axis="a", value=0.9, coverage=1.0, contribution=0.45),
                AxisScore(axis="b", value=0.9, coverage=1.0, contribution=0.45),
            ]
        }
    )
    runner = _rank("worse/model", 0.2, position=2)
    runner = runner.model_copy(
        update={
            "axes": [
                AxisScore(axis="a", value=0.2, coverage=1.0, contribution=0.1),
                AxisScore(axis="b", value=0.2, coverage=1.0, contribution=0.1),
            ]
        }
    )
    ranking = Ranking(
        profile="coder", modality="llm", computed_at=NOW, snapshot="s1", ranks=[leader, runner]
    )

    line = explain(ranking, profile)
    assert line is not None and line.startswith("no single weight change puts worse/model ahead")


# --------------------------------------------------------------------------- #
# cost per task
# --------------------------------------------------------------------------- #


def test_cost_per_task_uses_the_profile_shape() -> None:
    from sieve.contracts import Price

    price = Price(
        model_id="a/b",
        source="openrouter",
        unit="usd_per_1m_tokens",
        input=3.0,
        output=15.0,
        cached_input=0.3,
        observed_at=NOW,
    )
    shape = Shape.model_validate({"in": 30000, "out": 4000, "cached": 0.5})
    # 15000 fresh at $3 + 15000 cached at $0.30 + 4000 out at $15, per 1M
    expected = (15000 * 3 + 15000 * 0.3 + 4000 * 15) / 1_000_000
    assert cost_per_task(price, shape) == pytest.approx(expected)

    assert cost_per_task(None, shape) is None, "an unknown price is not a free model"
    assert cost_per_task(price, Shape()) is None


def test_media_cost_per_task() -> None:
    from sieve.contracts import Price

    per_second = Price(
        model_id="a/b", source="aa_media", unit="usd_per_second", per_unit=0.35, observed_at=NOW
    )
    assert cost_per_task(per_second, Shape(seconds=8)) == pytest.approx(2.8)

    per_image = Price(
        model_id="a/b", source="aa_media", unit="usd_per_image", per_unit=0.03, observed_at=NOW
    )
    assert cost_per_task(per_image, Shape(images=4)) == pytest.approx(0.12)
