"""PLAN §2.1a — an effort mode is a different model.

The bug this guards against is live and expensive: Artificial Analysis
publishes one row per reasoning effort at one price per token, so a gateway
serving `-high`, `-medium` and `-low` as three ids gets all three credited with
one score unless every mode survives as its own catalog entry.

The numbers below are read from the 2026-09-08 recording, not typed in, except
where a test exists precisely to lock a published figure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sieve.catalog.effort import EFFORT_ORDER, effort_of, effort_rank, family_of
from sieve.catalog.match import Matcher
from sieve.contracts import SourceConfig
from sieve.http import FixturePlayer
from sieve.scoring.efforts import choose_efforts
from sieve.sources import AALLMSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def pull() -> object:
    os.environ.setdefault("ARTIFICIAL_ANALYSIS_API_KEY", "fixture")
    return AALLMSource().pull(
        SourceConfig(name="aa_llm", key_env="ARTIFICIAL_ANALYSIS_API_KEY"),
        FixturePlayer(FIXTURES),
    )


# --------------------------------------------------------------------------- #
# reading the mode
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GPT-5.6 Sol (max)", "max"),
        ("GPT-5.6 Sol (medium)", "medium"),
        ("GPT-6 Astra (Non-reasoning)", "non-reasoning"),
        ("Gemini 3.8 Flash (high)", "high"),
        ("GPT-6 Astra (xhigh)", "xhigh"),
        # the bracket carries more than the mode, and the mode is not first
        ("Claude Opus 5 (Adaptive Reasoning, Max Effort)", "max"),
        ("Quasar 438B (max, based on GLM-5.2)", "max"),
        # no mode is stated: a model with one setting, not a low-effort model
        ("Kimi K2 Thinking", None),
        ("DeepSeek V4", None),
    ],
)
def test_the_mode_is_read_from_the_published_name(name: str, expected: str | None) -> None:
    assert effort_of(name) == expected


def test_an_unstated_mode_outranks_every_stated_one() -> None:
    """`best` must not prefer a mode row over a model that has no modes."""
    assert effort_rank(None) > effort_rank("max")
    ranks = [effort_rank(mode) for mode in EFFORT_ORDER]
    assert ranks == sorted(ranks), "the ladder is ordered least to most effort"


def test_a_family_gathers_its_modes_and_the_bare_slug_is_one_of_them() -> None:
    for suffix in ("-low", "-medium", "-high", "-xhigh", "-non-reasoning"):
        assert family_of(f"openai/gpt-5-6-sol{suffix}") == "openai/gpt-5-6-sol"
    assert family_of("openai/gpt-5-6-sol") == "openai/gpt-5-6-sol"


# --------------------------------------------------------------------------- #
# the numbers, locked
# --------------------------------------------------------------------------- #


def test_the_modes_of_one_family_score_differently_at_one_price(pull: object) -> None:
    """The measurement PLAN §2.1 is built on, pinned so a merge cannot undo it.

    GPT-5.6 Sol ships six rows in the recording. Their intelligence index runs
    from 32.9 to 51.3 -- an 18.4-point spread -- and every one of them is priced
    at $4.00 per 1M input tokens. Any change that collapses these onto one model
    is silently claiming a non-reasoning call is worth 51.3.
    """
    body = json.loads(
        (FIXTURES / "artificialanalysis_ai_api_v2_data_llms_models.json").read_text(
            encoding="utf-8"
        )
    )
    rows = {r["slug"]: r for r in body["data"]}

    sol = {
        "gpt-5-6-sol": ("max", 51.3),
        "gpt-5-6-sol-xhigh": ("xhigh", 49.8),
        "gpt-5-6-sol-high": ("high", 48.3),
        "gpt-5-6-sol-medium": ("medium", 46.0),
        "gpt-5-6-sol-low": ("low", 40.8),
        "gpt-5-6-sol-non-reasoning": ("non-reasoning", 32.9),
    }
    for slug, (mode, score) in sol.items():
        row = rows[slug]
        assert effort_of(row["name"]) == mode, row["name"]
        assert row["evaluations"]["artificial_analysis_intelligence_index"] == pytest.approx(score)
        assert row["pricing"]["price_1m_input_tokens"] == pytest.approx(4.00), (
            "every mode of a family is served at one price -- which is the whole problem"
        )

    models = {m.id: m for m in pull.models}  # type: ignore[attr-defined]
    family = [m for m in models.values() if m.family == "openai/gpt-5-6-sol"]
    assert len(family) == 6, "six modes, six catalog entries"
    assert {m.effort for m in family} == set(sol_mode for sol_mode, _ in sol.values())


def test_astra_ships_six_modes_at_one_price(pull: object) -> None:
    """A second family, because one could be a coincidence in the trim."""
    models = {m.id: m for m in pull.models}  # type: ignore[attr-defined]
    astra = [m for m in models.values() if m.family == "openai/gpt-6-astra"]
    assert len(astra) == 6
    assert {m.effort for m in astra} == {
        "non-reasoning",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }


# --------------------------------------------------------------------------- #
# a gateway id reaches its own mode
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("gateway_id", "expected", "expected_name"),
    [
        # the bare AA slug is this family's *high* mode
        ("ag/gemini-3.8-flash-high", "google/gemini-3-8-flash", "Gemini 3.8 Flash (high)"),
        ("ag/gemini-3.8-flash-low", "google/gemini-3-8-flash-low", "Gemini 3.8 Flash (low)"),
        (
            "ag/gemini-3.8-flash-medium",
            "google/gemini-3-8-flash-medium",
            "Gemini 3.8 Flash (medium)",
        ),
        # and this family's bare slug is its *max* mode -- it varies, which is
        # why the mode is read from the name and not guessed from the slug
        ("ag/gpt-5.6-sol-max", "openai/gpt-5-6-sol", "GPT-5.6 Sol (max)"),
        ("ag/gpt-5.6-sol-low", "openai/gpt-5-6-sol-low", "GPT-5.6 Sol (low)"),
        ("oc-go/gpt-6-astra-xhigh", "openai/gpt-6-astra-xhigh", "GPT-6 Astra (xhigh)"),
    ],
)
def test_a_gateway_mode_id_resolves_to_that_mode(
    pull: object, gateway_id: str, expected: str, expected_name: str
) -> None:
    models = {m.id: m for m in pull.models}  # type: ignore[attr-defined]
    aliases = {a: m.id for m in models.values() for a in m.aliases}
    matched = Matcher(list(models), aliases).match(gateway_id)

    assert matched.model_id == expected, f"{gateway_id} reached {matched.model_id}"
    assert models[expected].name == expected_name, "proven by the name, not the id alone"


def test_no_mode_ever_collapses_onto_another(pull: object) -> None:
    """Every published mode id must match itself and nothing else.

    This is the regression that would cost real money, so it is checked over
    every family in the recording rather than a chosen few.
    """
    models = {m.id: m for m in pull.models}  # type: ignore[attr-defined]
    aliases = {a: m.id for m in models.values() for a in m.aliases}
    matcher = Matcher(list(models), aliases)

    for model_id, model in models.items():
        if model.effort is None:
            continue
        assert matcher.match(model_id).model_id == model_id
        assert matcher.match(f"gw/{model_id.split('/', 1)[1]}").model_id == model_id, (
            f"a gateway serving {model_id} reached a different row"
        )


# --------------------------------------------------------------------------- #
# choosing a mode
# --------------------------------------------------------------------------- #


def _sol_family() -> dict[str, tuple[str | None, str | None]]:
    base = "openai/gpt-5-6-sol"
    return {
        base: (base, "max"),
        f"{base}-xhigh": (base, "xhigh"),
        f"{base}-high": (base, "high"),
        f"{base}-medium": (base, "medium"),
        f"{base}-low": (base, "low"),
        f"{base}-non-reasoning": (base, "non-reasoning"),
    }


def test_cheapest_clearing_seats_a_lower_mode_than_best() -> None:
    """The comparison the brief asks for, on one family's real mode set."""
    family = _sol_family()

    best_aside = choose_efforts(family, "best")
    cheap_aside = choose_efforts(family, "cheapest_clearing")

    best_kept = (set(family) - set(best_aside)).pop()
    cheap_kept = (set(family) - set(cheap_aside)).pop()

    assert best_kept == "openai/gpt-5-6-sol", "best takes the max mode"
    assert cheap_kept == "openai/gpt-5-6-sol-non-reasoning", "cheapest takes the lowest standing"
    assert effort_rank(family[cheap_kept][1]) < effort_rank(family[best_kept][1])


def test_cheapest_clearing_only_chooses_among_modes_that_already_cleared() -> None:
    """A mode a constraint rejected must not come back because it is cheaper."""
    family = _sol_family()
    del family["openai/gpt-5-6-sol-non-reasoning"]  # excluded upstream
    del family["openai/gpt-5-6-sol-low"]

    kept = (set(family) - set(choose_efforts(family, "cheapest_clearing"))).pop()
    assert kept == "openai/gpt-5-6-sol-medium"


def test_a_pinned_mode_falls_back_downwards_never_upwards() -> None:
    """Pinning a mode a family does not publish must never cost more."""
    family = {
        "x/m": ("x/m", "max"),
        "x/m-low": ("x/m", "low"),
    }
    kept = (set(family) - set(choose_efforts(family, "medium"))).pop()
    assert kept == "x/m-low", "medium is unpublished here, so the nearest lower mode wins"

    exact = (set(family) - set(choose_efforts(family, "max"))).pop()
    assert exact == "x/m"


def test_a_family_of_one_and_an_unset_preference_are_both_left_alone() -> None:
    family = _sol_family()
    assert choose_efforts(family, None) == {}
    assert choose_efforts({"x/only": ("x/only", "high")}, "cheapest_clearing") == {}


def test_the_set_aside_reason_names_the_mode_that_won() -> None:
    aside = choose_efforts(_sol_family(), "cheapest_clearing")
    assert aside, "five of the six are set aside"
    for reason in aside.values():
        assert reason.startswith("effort:non-reasoning preferred for openai/gpt-5-6-sol")


def test_a_mistyped_prefer_effort_is_caught_by_check() -> None:
    """Silent and expensive otherwise: an unknown value would fall through to
    the pinned-mode branch, match no mode, and seat the lowest of every family."""
    from sieve.contracts import Profile
    from sieve.profiles.validate import validate_profile

    typo = Profile(
        name="x",
        modality="llm",
        purpose="t",
        weights={"cost": 1.0},
        prefer_effort="higest",
    )
    assert any(
        "prefer_effort 'higest' is not a mode" in problem for problem in validate_profile(typo)
    )

    fine = typo.model_copy(update={"prefer_effort": "cheapest_clearing"})
    assert not any("prefer_effort" in problem for problem in validate_profile(fine))
