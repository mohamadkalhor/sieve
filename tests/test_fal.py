"""PLAN §2.1b — fal supplies the price the media field has no other way to get.

Artificial Analysis measures 482 media models and prices none of them, so every
media `cost` axis was unmeasurable until this source existed. fal is the mirror
image: 1,494 models with prices and no quality score at all.

The prices are English prose written for a person reading a model card, so the
tests that matter here are about *refusing* to read one. A wrong price does not
merely mis-rank a model; it recommends the wrong one and bills for it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sieve.contracts import SourceConfig
from sieve.http import FixturePlayer, fixture_slug
from sieve.sources.fal import CATEGORIES, PAGE_SIZE, FalSource, model_id_of, parse_price

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def player() -> FixturePlayer:
    return FixturePlayer(FIXTURES)


# --------------------------------------------------------------------------- #
# reading a price, and refusing to
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("prose", "amount", "unit"),
    [
        ("Your request will cost **$0.08** per image.", 0.08, "usd_per_image"),
        ("Your request will cost $0.039 per image.", 0.039, "usd_per_image"),
        (
            "For every second of video you generated, you will be charged **$0.04**.",
            0.04,
            "usd_per_second",
        ),
        # a total for a stated duration becomes a rate
        ("For 5s video your request will cost **$0.25**.", 0.05, "usd_per_second"),
        ("For **10s** video your request will cost **$1.20**.", 0.12, "usd_per_second"),
        ("Requests cost **$0.10** per second.", 0.10, "usd_per_second"),
    ],
)
def test_the_forms_that_can_be_proven(prose: str, amount: float, unit: str) -> None:
    parsed = parse_price(prose)
    assert parsed is not None, prose
    assert parsed[0] == pytest.approx(amount)
    assert parsed[1] == unit


@pytest.mark.parametrize(
    "prose",
    [
        # tiered by resolution: there is no single rate, and taking the first
        # would bill 4K work at the 480p line
        (
            "Video costs **$0.0125** per second at **480p**, **$0.02** per second at "
            "**768p**, and **$0.04** per second at **1080p**."
        ),
        # a table of token prices, several rates, none of them "the" price
        (
            "Text tokens (per 1M): **$5.00** input, **$1.25** cached, **$10.00** output. "
            "Image tokens (per 1M): **$8.00** input."
        ),
        # a first-unit price plus a marginal one
        (
            "Your request will cost **$0.03** for the first megapixel of output, plus "
            "**$0.015** per extra megapixel."
        ),
        # billed per step, and the number of steps is not published
        "The cost of training depends on the number of steps you choose.",
        # says nothing about money at all
        "You will be charged based on the number of input and output tokens.",
        "",
    ],
)
def test_anything_ambiguous_is_left_blank(prose: str) -> None:
    assert parse_price(prose) is None, f"read a price it should have refused: {prose[:60]}"


def test_a_single_rate_repeated_is_still_one_rate() -> None:
    """Refusing tiers must not refuse a price simply for being mentioned twice."""
    prose = "Your request will cost **$0.08** per image. Each extra image is **$0.08** per image."
    parsed = parse_price(prose)
    assert parsed is not None
    assert parsed[0] == pytest.approx(0.08)
    assert parsed[1] == "usd_per_image"


# --------------------------------------------------------------------------- #
# ids
# --------------------------------------------------------------------------- #


def test_an_id_keeps_the_model_slug_and_drops_the_endpoint() -> None:
    """`fal-ai/nano-banana-2/edit` is a provider path, not a model name."""
    assert model_id_of({"id": "fal-ai/nano-banana-2/edit"}) == "fal-ai/nano-banana-2"
    assert model_id_of({"id": "fal-ai/kling-video/v3/standard"}) == "fal-ai/kling-video"
    assert model_id_of({"id": "single-segment"}) is None
    assert model_id_of({}) is None


def test_the_model_family_is_not_used_as_a_creator() -> None:
    """It is the model's own name, so it would give `nano-banana-2/nano-banana-2`."""
    entry = {"id": "fal-ai/nano-banana-2/edit", "modelFamily": "Nano Banana 2"}
    assert model_id_of(entry) == "fal-ai/nano-banana-2"


# --------------------------------------------------------------------------- #
# the pull
# --------------------------------------------------------------------------- #


def test_the_pull_stores_models_prices_and_an_honest_unparsed_count(
    player: FixturePlayer,
) -> None:
    result = FalSource().pull(SourceConfig(name="fal"), player)

    assert FalSource().needs_key is False, "no key, which is why this source is usable at all"
    assert result.models, "the recording carries models in a Sieve modality"
    assert result.prices, "and prices for some of them"
    assert result.observations == [], "fal measures no quality whatsoever"

    assert all(p.per_unit is not None and p.per_unit > 0 for p in result.prices)
    assert {p.unit for p in result.prices} <= {
        "usd_per_image",
        "usd_per_second",
        "usd_per_1m_tokens",
        "usd_per_1m_chars",
    }

    unparsed = [w for w in result.warnings if "prices unparsed" in w]
    assert unparsed, "the count of what could not be read is reported, never hidden"
    assert "worse than no price" in unparsed[0]


def test_a_category_with_no_modality_is_skipped_and_counted(player: FixturePlayer) -> None:
    """`video-to-video`, `image-to-3d` and friends have no Sieve modality.

    Forcing them into the nearest one would be a lie about what was measured, so
    they are counted in a warning instead -- visible, and not silently dropped.
    """
    result = FalSource().pull(SourceConfig(name="fal"), player)

    assert all(m.modality in CATEGORIES.values() for m in result.models)
    skipped = [w for w in result.warnings if "no Sieve modality" in w]
    assert skipped, "the skipped categories are named"
    for absent in ("video-to-video", "image-to-3d", "training"):
        assert absent in skipped[0]


def test_the_pull_asks_for_the_page_size_the_fixture_was_recorded_at(
    player: FixturePlayer,
) -> None:
    """The recording is one page, so the pull must stop after it."""
    FalSource().pull(SourceConfig(name="fal"), player)
    assert len(player.requested) == 1, f"paged more than the envelope asked for: {player.requested}"

    slug = fixture_slug("https://fal.ai/api/models", {"limit": str(PAGE_SIZE), "page": "1"})
    body = json.loads((FIXTURES / f"{slug}.json").read_text(encoding="utf-8"))
    assert body["pages"] == 1 and body["total"] == 1494, "1,494 models on 2026-09-08"


def test_a_bad_page_warns_and_keeps_what_it_had() -> None:
    """A source that half-fails must not look like a source that found nothing."""
    empty = FalSource().pull(SourceConfig(name="fal"), FixturePlayer(FIXTURES / "does-not-exist"))
    assert empty.models == [] and empty.prices == []
    assert any("HTTP" in w for w in empty.warnings)
    assert any("nothing stored" in w for w in empty.warnings)


def test_only_the_wanted_modalities_come_back(player: FixturePlayer) -> None:
    result = FalSource().pull(SourceConfig(name="fal", modalities=["text-to-image"]), player)
    assert {m.modality for m in result.models} == {"text-to-image"}
