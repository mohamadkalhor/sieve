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


def test_every_mode_of_a_family_ranks_on_its_own_merits() -> None:
    """The modes used to be thinned out before scoring, by `prefer_effort`.

    One family would put six rows in the pool and a preference picked one of
    them -- "best", or the cheapest that cleared a floor -- before the weights
    were applied. That is a second opinion about the same question the weights
    answer, so it is gone: every mode is a row, each is scored, and a profile
    that cares about cost seats the cheap mode by scoring it higher.
    """
    from sieve.contracts import Profile
    from sieve.scoring import weigh
    from sieve.scoring.weigh import rank_order

    base = "openai/gpt-5-6-sol"
    modes = {
        base: 0.95,
        f"{base}-high": 0.90,
        f"{base}-low": 0.70,
    }
    rows: dict[str, dict[str, tuple[float | None, float]]] = {
        model_id: {"intelligence": (value, 1.0), "cost": (1.0 - value, 1.0)}
        for model_id, value in modes.items()
    }

    clever = Profile(name="clever", modality="llm", purpose="t", weights={"intelligence": 1.0})
    thrifty = Profile(name="thrifty", modality="llm", purpose="t", weights={"cost": 1.0})

    assert rank_order(weigh(clever, rows))[0] == base
    assert rank_order(weigh(thrifty, rows))[0] == f"{base}-low"
    assert len(rank_order(weigh(clever, rows))) == 3, "no mode is set aside"


def test_a_fold_rewrites_the_family_pointer_too() -> None:
    """A merge renames an id, and `family` is an id.

    Artificial Analysis publishes `openai/gpt-5-6-luna` beside its five
    `-low`, `-medium` ... mode rows, every one of them carrying
    `family="openai/gpt-5-6-luna"`. The base row then folds onto OpenRouter's
    `openai/gpt-5.6-luna` -- and before this, the family pointer went on naming
    an id that no longer existed. Nothing could find the family's base row, so
    every mode inherited nothing from it and stayed excluded by `require`.

    Measured on the recordings: 9 of 33 families, and 4 of the 10 that publish
    more than one mode.
    """
    from sieve.catalog.registry import merge_pull
    from sieve.contracts import ModelRef, PullResult

    def mode(model_id: str, effort: str | None) -> ModelRef:
        return ModelRef(
            id=model_id,
            modality="llm",
            name=f"Luna ({effort})" if effort else "Luna",
            creator="openai",
            effort=effort,
            family="openai/gpt-5-6-luna",
        )

    pull = PullResult(
        source="aa_llm",
        models=[
            mode("openai/gpt-5-6-luna", "max"),
            mode("openai/gpt-5-6-luna-high", "high"),
            mode("openai/gpt-5-6-luna-low", "low"),
        ],
    )

    folded, rewrite = merge_pull(pull, ["openai/gpt-5.6-luna"])
    assert rewrite == {"openai/gpt-5-6-luna": "openai/gpt-5.6-luna"}

    by_id = {m.id: m for m in folded.models}
    assert set(by_id) == {
        "openai/gpt-5.6-luna",
        "openai/gpt-5-6-luna-high",
        "openai/gpt-5-6-luna-low",
    }
    # every row now points at a family key that names a row that exists
    for model in folded.models:
        assert model.family == "openai/gpt-5.6-luna"
    assert by_id["openai/gpt-5.6-luna"].family in by_id

    # and the folded row keeps the id it arrived under, as an alias
    assert "openai/gpt-5-6-luna" in by_id["openai/gpt-5.6-luna"].aliases
