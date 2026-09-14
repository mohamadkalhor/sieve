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
    Profile,
    Rank,
    Ranking,
    TelemetryEvent,
)
from sieve.scoring import (
    TaskShape,
    apply_transform,
    cost_per_task,
    decide,
    explain,
    health,
    percentile,
    shipped,
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
        ship=body["policy"]["chain"],
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

    # m11 is the model with an unmeasured axis. The fixture's expected order
    # predates this rebuild and leaves it out, because a confidence floor used
    # to exclude it; nothing excludes it now, so it ranks where its score puts
    # it and the rest of the order is unchanged.
    assert [m for m in rank_order(scored) if m != "m11"] == expected["order"]


def test_an_unmeasured_axis_costs_confidence_and_excludes_nobody() -> None:
    """The model with a gap ranks low. It is not thrown out for having one.

    A confidence floor used to remove it by name, which is how a slider came to
    have no visible effect on models nobody had benchmarked yet: they were gone
    before the weights were applied.
    """
    case = _case()
    profile = _profile_from(case)
    scored = weigh(profile, _axes_by_model(case))

    score, confidence, contributions = scored["m11"]
    assert contributions["c"] == 0.0, "an unmeasured axis contributes nothing"
    assert confidence == pytest.approx(0.7, abs=1e-9), "and the loss shows up here"
    assert score == pytest.approx(case["expected"]["scores"]["m11"], abs=1e-6)

    order = rank_order(scored)
    assert "m11" in order, "a gap in the evidence is not a reason to disappear"
    assert order.index("m11") > order.index("m07"), "it ranks where its score puts it"


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
    """A ranking as the engine builds one: reachable models, best first."""
    best_first = sorted(ranks, key=lambda r: (-r.final, r.model_id))
    ordered = [
        r.model_copy(update={"position": i if r.reachable else 0})
        for i, r in enumerate(best_first, start=1)
    ]
    return Ranking(profile="coder", modality="llm", computed_at=NOW, snapshot="s1", ranks=ordered)


def _profile(ship: int = 4) -> Profile:
    return Profile(
        name="coder",
        modality="llm",
        purpose="agentic coding",
        weights={"a": 1.0},
        ship=ship,
    )


def _incumbent(*model_ids: str, days: int = 1) -> Chain:
    return Chain(
        profile="coder",
        computed_at=NOW - timedelta(days=days),
        primary=model_ids[0],
        fallbacks=list(model_ids[1:]),
        incumbent=model_ids[0],
        incumbent_since=NOW - timedelta(days=days),
    )


def test_the_list_is_the_top_ship_reachable_models_in_score_order() -> None:
    ranking = _ranking(*[_rank(f"m/{i}", 0.9 - i / 100) for i in range(8)])
    chain, decision = decide(_profile(ship=3), None, ranking, NOW)

    assert chain is not None
    assert [chain.primary, *chain.fallbacks] == ["m/0", "m/1", "m/2"]
    assert chain.local["m/0"] == ["gw/0"]
    assert decision is not None and decision.kind == "switch"


def test_a_tenth_of_a_point_moves_the_list_because_nothing_holds_it_back() -> None:
    """There is no margin any more. The score is the whole argument.

    A challenger used to have to clear three points on a hundred-point scale,
    which meant the list could disagree with the weights for weeks and the
    reason was invisible on the page.
    """
    ranking = _ranking(_rank("new/model", 0.801), _rank("old/model", 0.80))
    chain, decision = decide(_profile(ship=1), _incumbent("old/model"), ranking, NOW)

    assert chain is not None and chain.primary == "new/model"
    assert decision is not None and decision.kind == "switch"
    assert decision.reason.startswith("switch: the list changed, new/model leads")
    assert chain.incumbent_since == NOW, "a list that changed starts its tenure now"


def test_an_unchanged_list_is_a_hold_and_keeps_its_tenure() -> None:
    ranking = _ranking(_rank("old/model", 0.80), _rank("new/model", 0.70))
    incumbent = _incumbent("old/model", "new/model", days=9)
    chain, decision = decide(_profile(ship=2), incumbent, ranking, NOW)

    assert decision is not None and decision.kind == "hold"
    assert decision.reason == "hold: the list is unchanged, old/model still leads"
    assert chain is not None and [chain.primary, *chain.fallbacks] == ["old/model", "new/model"]
    assert chain.incumbent_since == incumbent.incumbent_since


def test_a_reordering_below_the_primary_is_still_a_change() -> None:
    """The fallbacks are part of the routing instruction, not decoration."""
    ranking = _ranking(_rank("a/one", 0.9), _rank("c/three", 0.8), _rank("b/two", 0.7))
    chain, decision = decide(
        _profile(ship=3), _incumbent("a/one", "b/two", "c/three"), ranking, NOW
    )

    assert decision is not None and decision.kind == "switch"
    assert chain is not None
    assert [chain.primary, *chain.fallbacks] == ["a/one", "c/three", "b/two"]


def test_a_model_that_stopped_working_falls_out_by_scoring_lower() -> None:
    """Health is multiplied into the score, so it needs no veto of its own."""
    sick = _rank("old/model", 0.80 * 0.4, health_value=0.4)
    ranking = _ranking(sick, _rank("new/model", 0.70))
    chain, decision = decide(_profile(ship=1), _incumbent("old/model"), ranking, NOW)

    assert chain is not None and chain.primary == "new/model"
    assert decision is not None and decision.kind == "switch"


def test_ship_is_a_ceiling_not_a_promise() -> None:
    ranking = _ranking(_rank("m/0", 0.9), _rank("m/1", 0.8))
    chain, _ = decide(_profile(ship=6), None, ranking, NOW)
    assert chain is not None and [chain.primary, *chain.fallbacks] == ["m/0", "m/1"]


def test_decide_is_deterministic() -> None:
    ranking = _ranking(_rank("new/model", 0.84), _rank("old/model", 0.80))
    first = decide(_profile(), _incumbent("old/model"), ranking, NOW)
    second = decide(_profile(), _incumbent("old/model"), ranking, NOW)
    assert first[0] == second[0]
    assert first[1] is not None and second[1] is not None
    assert first[1].reason == second[1].reason and first[1].kind == second[1].kind


def test_nothing_reachable_is_a_hold_not_a_crash() -> None:
    ranking = Ranking(
        profile="coder",
        modality="llm",
        computed_at=NOW,
        snapshot="s1",
        ranks=[_rank("m/1", 0.9, position=0).model_copy(update={"reachable": False})],
    )
    chain, decision = decide(_profile(), None, ranking, NOW)
    assert chain is None
    assert (
        decision is not None and decision.reason == "hold: nothing reachable ranks for this profile"
    )


def test_an_unreachable_model_never_ships_even_if_it_scores_best() -> None:
    ranking = _ranking(
        _rank("far/best", 0.99).model_copy(update={"reachable": False}),
        _rank("near/second", 0.5),
    )
    assert [r.model_id for r in shipped(ranking, 4)] == ["near/second"]


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


def test_cost_per_task_uses_the_modality_shape() -> None:
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
    shape = TaskShape(in_tokens=30000, out_tokens=4000, cached=0.5)
    # 15000 fresh at $3 + 15000 cached at $0.30 + 4000 out at $15, per 1M
    expected = (15000 * 3 + 15000 * 0.3 + 4000 * 15) / 1_000_000
    assert cost_per_task(price, shape) == pytest.approx(expected)

    assert cost_per_task(None, shape) is None, "an unknown price is not a free model"
    assert cost_per_task(price, TaskShape()) is None


def test_media_cost_per_task() -> None:
    from sieve.contracts import Price

    per_second = Price(
        model_id="a/b", source="aa_media", unit="usd_per_second", per_unit=0.35, observed_at=NOW
    )
    assert cost_per_task(per_second, TaskShape(seconds=8)) == pytest.approx(2.8)

    per_image = Price(
        model_id="a/b", source="aa_media", unit="usd_per_image", per_unit=0.03, observed_at=NOW
    )
    assert cost_per_task(per_image, TaskShape(images=4)) == pytest.approx(0.12)


# --------------------------------------------------------------------------- #
# the real field: nothing is taken out of it
# --------------------------------------------------------------------------- #


def _real_llm_rows(
    profile_name: str,
) -> tuple[dict[str, dict[str, tuple[float | None, float]]], Profile]:
    """Axis values and coverage for one profile over the recorded AA field."""
    import os

    os.environ.setdefault("ARTIFICIAL_ANALYSIS_API_KEY", "fixture")

    from sieve.axes import compute as axes_compute
    from sieve.axes import load as axes_load
    from sieve.contracts import ObsTable, SourceConfig
    from sieve.engine import add_cost_observations
    from sieve.http import FixturePlayer
    from sieve.profiles.load import load_profiles
    from sieve.sources import AALLMSource

    repo = Path(__file__).resolve().parents[1]
    at = datetime(2026, 9, 8, tzinfo=UTC)

    pull = AALLMSource().pull(
        SourceConfig(name="aa_llm", key_env="ARTIFICIAL_ANALYSIS_API_KEY"),
        FixturePlayer(repo / "tests" / "fixtures"),
    )
    obs = ObsTable(modality="llm")
    for observation in pull.observations:
        obs.add(observation)
    for price in pull.prices:
        obs.prices[price.model_id] = price

    profile = {p.name: p for p in load_profiles(repo / "profiles")}[profile_name]
    add_cost_observations(obs, profile, at)

    pool = obs.models()
    axes = axes_load.load_axes(repo / "data" / "axes", "llm")
    per_axis = {
        a.name: axes_compute.axis_values(a, obs, pool) for a in axes if a.name in profile.weights
    }
    rows = {m: {name: per_axis[name].get(m, (None, 0.0)) for name in per_axis} for m in pool}
    return rows, profile


def test_every_model_on_the_real_field_keeps_its_place() -> None:
    """Phase 1 pruned 60 real models down to a handful of incomparable ones.

    That was the single biggest reason a slider looked broken: a model dropped
    for being beaten on every axis could never come back, however the weights
    moved, and the page could not say why. Now the field is ranked whole.
    """
    rows, profile = _real_llm_rows("coder")
    scored = weigh(profile, rows)

    assert len(scored) == len(rows), "every model in the pool is scored"
    order = rank_order(scored)
    assert len(order) == len(rows), "and every scored model has a place in the order"
    values = [scored[m][0] for m in order]
    assert values == sorted(values, reverse=True), "best first, and nothing else"


def test_a_model_known_only_for_its_price_neither_prunes_nor_is_pruned() -> None:
    """The live bug, from both sides: a cheap unmeasured model used to delete
    a flagship from the list by "dominating" it on the one axis it had.

    Now neither is removed. The flagship leads because it scores higher, and
    the cheap one is on the list at the place its score earns -- which is the
    answer a person can argue with by moving a slider.
    """
    weights = {"reasoning": 0.4, "intelligence": 0.35, "cost": 0.25}
    profile = Profile(name="probe", modality="llm", purpose="the live bug", weights=weights, ship=4)
    rows: dict[str, dict[str, tuple[float | None, float]]] = {
        "flagship": {
            "reasoning": (0.95, 1.0),
            "intelligence": (0.99, 1.0),
            "cost": (0.36, 1.0),
        },
        "cheap_unknown": {
            "reasoning": (None, 0.0),
            "intelligence": (None, 0.0),
            "cost": (0.73, 1.0),
        },
    }
    order = rank_order(weigh(profile, rows))
    assert order == ["flagship", "cheap_unknown"]
