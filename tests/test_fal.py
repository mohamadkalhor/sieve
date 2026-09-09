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
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sieve.contracts import Price, SourceConfig
from sieve.http import FixturePlayer, fixture_slug
from sieve.sources.fal import (
    CATEGORIES,
    PAGE_SIZE,
    PROVISIONAL,
    FalSource,
    model_id_of,
    parse_price,
)

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
        # unlabelled alternatives: audio on or off is not something prose can
        # choose between, and picking the cheaper would bill the wrong one
        (
            "For every second of video you generated, you will be charged **$0.112** "
            "(audio off) or **$0.168** (audio on)."
        ),
        # one price for two resolutions, and a second rate for a third: nothing
        # here says which tier the first number belongs to
        (
            "For every second of video you generate you will be charged **$0.10** without "
            "audio or **$0.15** with audio for 720p or 1080p. At 4k, you will be charged "
            "**$0.30** per second without audio, or **$0.35** with."
        ),
        # two currencies for one number, and one of them is wrong by 100x
        "Your request will cost **$0.0024 cents per step.**",
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


def test_a_tiered_price_is_read_as_tiers_and_the_cheapest_is_named() -> None:
    """A tiered price is not unparseable -- it is several prices.

    Refusing the whole string dropped the model from the catalogue, which loses
    more than it protects. The cheapest is stored *with its tier named*, so the
    number is true and visibly incomplete; taking the first and calling it "the"
    price would bill 4K work at the 480p line.
    """
    from sieve.sources.fal import parse_rates

    rates = parse_rates(
        "Video costs **$0.0125** per second at **480p**, **$0.02** per second at "
        "**768p**, and **$0.04** per second at **1080p**."
    )
    assert [r.tier for r in rates] == ["480p", "768p", "1080p"]
    assert [r.amount for r in rates] == pytest.approx([0.0125, 0.02, 0.04])
    assert all(r.unit == "usd_per_second" for r in rates)

    cheapest = parse_price(
        "Video costs **$0.0125** per second at **480p**, **$0.04** per second at **1080p**."
    )
    assert cheapest is not None and cheapest[0] == pytest.approx(0.0125)


def test_an_unlabelled_second_rate_is_still_refused() -> None:
    """Only tiers that can be named are trustworthy.

    Two amounts against one unit with nothing saying which is which could be
    anything -- a discount, a typo, a second product. Named tiers are evidence;
    an unlabelled pair is not.
    """
    from sieve.sources.fal import parse_rates

    assert parse_rates("Requests cost **$0.10** per second, or **$0.20** per second.") == []


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
    """`fal-ai/nano-banana-2/edit` is a provider path, not a model name.

    This test used to assert that `fal-ai/kling-video/v3/standard` became
    `fal-ai/kling-video`, which was the bug written down as the rule: it puts
    five different Kling models on one id. `v3/standard` names the model and
    only an endpoint may be dropped -- see the two tests at the end of this file.
    """
    assert model_id_of({"id": "fal-ai/nano-banana-2/edit"}) == "fal-ai/nano-banana-2"
    assert model_id_of({"id": "fal-ai/kling-video/v3/standard"}) == "fal-ai/kling-video-v3-standard"
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

    # a flat rate carries per_unit; a token price carries input/output instead
    assert all(
        (p.per_unit is not None and p.per_unit > 0) or (p.input is not None or p.output is not None)
        for p in result.prices
    )
    assert {p.unit for p in result.prices} <= {
        "usd_per_image",
        "usd_per_second",
        "usd_per_compute_second",
        "usd_per_1m_tokens",
        "usd_per_1m_chars",
        "usd_per_megapixel",
        "usd_per_request",
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

    # PROVISIONAL adds `music`: a text-to-audio row is offered as music and
    # kept only if something that separates music from sound effects knows it
    allowed = {*CATEGORIES.values(), *PROVISIONAL.values()}
    assert all(m.modality in allowed for m in result.models)
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


# --------------------------------------------------------------------------- #
# PLAN 2.3 -- reading a price with several numbers in it
# --------------------------------------------------------------------------- #


def test_a_restatement_is_not_a_second_rate() -> None:
    """ "For $1.00, you can run this model 12 times" is the price, times twelve.

    78 models in the live catalogue say only this beyond their rate, and every
    one of them was refused for having "two prices". It is one price and a piece
    of marketing arithmetic.
    """
    parsed = parse_price(
        "Your request will cost **$0.08** per image. For **$1.00**, you can run this "
        "model **12** times."
    )
    assert parsed == pytest.approx((0.08, "usd_per_image"), rel=0, abs=0) or parsed == (
        0.08,
        "usd_per_image",
    )


def test_every_tier_is_kept_not_only_the_cheapest() -> None:
    """PLAN 2.3: record every tier. A 1080p job must not be billed at 480p."""
    from sieve.sources.fal import parse_rates

    rates = parse_rates(
        "Video costs **$0.05** per second at **480p**, **$0.06** per second at **768p**, "
        "**$0.13** per second at **2K** and **$0.16** per second at **4K**."
    )
    assert [r.tier for r in rates] == ["480p", "768p", "2k", "4k"]
    assert [r.amount for r in rates] == pytest.approx([0.05, 0.06, 0.13, 0.16])


def test_a_tier_written_before_its_rate_still_belongs_to_it() -> None:
    """The nearest tier is not always the right one.

    In "At an output resolution of 480p, every second costs $0.05, and at 720p,
    every second costs $0.07" the tier *nearest* the first amount is 720p, four
    words past it. Reading tiers and amounts in the order they are written gets
    both right; taking the nearest gives 720p two different prices.
    """
    from sieve.sources.fal import parse_rates

    rates = parse_rates(
        "At an output resolution of **480p**, every second costs **$0.05**, and at "
        "**720p**, every second costs **$0.07**."
    )
    assert [r.tier for r in rates] == ["480p", "720p"]
    assert [r.amount for r in rates] == pytest.approx([0.05, 0.07])


def test_a_promotional_rate_does_not_replace_the_one_being_charged() -> None:
    """fal publishes the rate after the discount ends in the same sentence."""
    from sieve.sources.fal import parse_rates

    rates = parse_rates(
        "Video costs **$0.0125** per second at **480p**, **$0.02** per second at "
        "**768p**, and **$0.04** per second at **1080p**. Note: these are promotional "
        "launch rates, **75%** off for a limited time. The discount ends "
        "**September 14**, after which **480p** is **$0.05**/second."
    )
    assert [r.amount for r in rates] == pytest.approx([0.0125, 0.02, 0.04])


def test_input_and_output_token_rates_are_one_price_with_two_sides() -> None:
    """PLAN 2.3 names this shape explicitly. It used to be refused outright."""
    from sieve.sources.fal import parse_rates

    rates = parse_rates(
        "Text tokens (per 1M): **$5.00** input, **$1.25** cached, **$10.00** output. "
        "Image tokens (per 1M): **$8.00** input, **$2.00** cached, **$30.00** output."
    )
    assert {(r.tier, r.role, r.amount) for r in rates} == {
        ("text tokens", "input", 5.0),
        ("text tokens", "cached", 1.25),
        ("text tokens", "output", 10.0),
        ("image tokens", "input", 8.0),
        ("image tokens", "cached", 2.0),
        ("image tokens", "output", 30.0),
    }


def test_compute_seconds_are_not_output_seconds() -> None:
    """PLAN 2.3's trap, and the reason it is a unit of its own.

    "$0.00111 per compute second" bills hardware time, which varies with the
    job. "$0.025 per second of generated video" bills output length. Comparing
    them makes an upscaler look cheaper than a video model on a number that
    means something else, so they never share a unit -- and a compute-second
    price is deliberately not costable against any shape.
    """
    from sieve.contracts import Shape
    from sieve.scoring.weigh import cost_per_task
    from sieve.sources.fal import parse_rates

    compute = parse_rates("Your request will cost **$0.00111** per compute second")
    assert [r.unit for r in compute] == ["usd_per_compute_second"]

    output = parse_rates("Requests cost **$0.025** per second.")
    assert [r.unit for r in output] == ["usd_per_second"]

    priced = Price(
        model_id="x",
        source="fal",
        unit="usd_per_compute_second",
        per_unit=0.00111,
        observed_at=datetime.now(UTC),
    )
    assert cost_per_task(priced, Shape(seconds=5)) is None, (
        "hardware time must not be costed against the length of the output"
    )


def test_a_minute_is_sixty_seconds_and_a_thousand_chars_is_not_a_million() -> None:
    """Two conversions that are arithmetic, not judgement -- and one was wrong.

    `per 1,000 characters` was stored under the per-million unit at the
    published number, which understated every such model by a factor of a
    thousand.
    """
    assert parse_price("Your request will cost **$0.6** per minute.") == pytest.approx(
        (0.01, "usd_per_second")
    )
    assert parse_price("Charged per 1,000 characters at **$0.30**.") == (
        300.0,
        "usd_per_1m_chars",
    )


def test_the_pull_stores_a_row_per_tier(player: FixturePlayer) -> None:
    """Every tier reaches the store, and the store can hold them.

    The uniqueness on `prices` used to be (model, source, modality, observed_at),
    so the second and third tiers of one pull collided with the first and
    `INSERT OR IGNORE` dropped them without a word.
    """
    from sieve.store import Store

    result = FalSource().pull(SourceConfig(name="fal"), player)
    tiered = [p for p in result.prices if p.tier and p.tier not in ("standard",)]
    assert tiered, "the recording contains resolution-tiered models"

    by_model: dict[tuple[str, str | None], set[str | None]] = {}
    for price in result.prices:
        by_model.setdefault((price.model_id, price.modality), set()).add(price.tier)
    several = {k: v for k, v in by_model.items() if len(v) > 1}
    assert several, "at least one model publishes more than one tier"

    store = Store(":memory:")
    store.upsert_models(result.models)
    intake = store.add_prices(result.prices)
    assert intake.added == len(result.prices), "every tier survived the write"
    assert intake.refused == [], "and none of them was refused as an impossible unit"

    # and a second identical pull adds nothing, rather than duplicating
    assert store.add_prices(result.prices).added == 0


def test_the_default_tier_is_the_cheapest_and_it_is_named(player: FixturePlayer) -> None:
    """A ranking needs one price, so the choice is declared and visible."""
    from sieve.store import Store

    result = FalSource().pull(SourceConfig(name="fal"), player)
    store = Store(":memory:")
    store.upsert_models(result.models)
    store.add_prices(result.prices)

    for modality in {m.modality for m in result.models}:
        latest = store.latest_prices(modality)
        for model_id, price in latest.items():
            mine = [
                p
                for p in result.prices
                if p.model_id == model_id and p.modality == modality and p.per_unit is not None
            ]
            if len(mine) < 2:
                continue
            cheapest = min(p.per_unit or 0.0 for p in mine)
            assert price.per_unit == pytest.approx(cheapest)
            assert price.tier, "and the tier it came from is on the row"


def test_a_variant_is_part_of_the_model_id() -> None:
    """Veo 3.1, Veo 3.1 Fast and Veo3.1 Lite are three models at three prices.

    Taking only the first path segment after the vendor collapsed all three onto
    `fal-ai/veo3-1`, which then folded onto the catalogue's `veo-3-1-fast` and
    put the **lite** model's $0.03 a second on the row for a model that costs
    $0.15. Five endpoints of Kling collapsed the same way, and FLUX schnell onto
    FLUX dev. That is a wrong recommendation rather than a missing one.
    """
    assert model_id_of({"id": "fal-ai/veo3.1/image-to-video"}) == "fal-ai/veo3-1"
    assert model_id_of({"id": "fal-ai/veo3.1/fast/image-to-video"}) == "fal-ai/veo3-1-fast"
    assert model_id_of({"id": "fal-ai/veo3.1/lite/image-to-video"}) == "fal-ai/veo3-1-lite"
    assert model_id_of({"id": "fal-ai/flux/schnell"}) == "fal-ai/flux-schnell"
    assert model_id_of({"id": "fal-ai/flux/dev"}) == "fal-ai/flux-dev"
    assert (
        model_id_of({"id": "fal-ai/kling-video/v3/standard/image-to-video"})
        == "fal-ai/kling-video-v3-standard"
    )


def test_one_model_asked_two_questions_is_still_one_model() -> None:
    """The other half of the same rule, and the reason it is not simply "keep
    every segment": an endpoint names what the model was asked to do, and the
    modality already records that."""
    assert (
        model_id_of({"id": "minimax/h3-max/text-to-video"})
        == model_id_of({"id": "minimax/h3-max/image-to-video"})
        == model_id_of({"id": "minimax/h3-max/reference-to-video"})
        == "minimax/h3-max"
    )
    assert (
        model_id_of({"id": "fal-ai/kling-video/v3/pro/text-to-video"})
        == model_id_of({"id": "fal-ai/kling-video/v3/pro/image-to-video"})
        == "fal-ai/kling-video-v3-pro"
    )
    # `edit` is an endpoint too, and the one that is not spelled `x-to-y`
    assert model_id_of({"id": "fal-ai/nano-banana-2/edit"}) == "fal-ai/nano-banana-2"
    assert model_id_of({"id": "openai/gpt-image-2/edit"}) == "openai/gpt-image-2"


def test_no_two_different_models_share_a_fal_id_in_the_recording() -> None:
    """Asserted over the whole recording, because the failure is a collision and
    a collision is only visible across the set."""
    import json as _json

    raw = _json.loads(
        (FIXTURES / "fal_ai_api_models__limit_200_page_1.json").read_text(encoding="utf-8")
    )
    by_id: dict[str, set[str]] = {}
    for entry in raw.get("body", raw).get("items", []):
        model_id = model_id_of(entry)
        if model_id:
            by_id.setdefault(model_id, set()).add(str(entry.get("title") or ""))

    # what remains is one model under several endpoint names, which is correct
    allowed = {
        "minimax/h3-max",
        "minimax/h3-max-turbo",
        "fal-ai/kling-video-v3-pro",
        "fal-ai/hunyuan-3d-v3-1-pro",
        "sonilo/v1-1",
    }
    collided = {k for k, titles in by_id.items() if len(titles) > 1} - allowed
    assert not collided, f"different models sharing one id: {sorted(collided)}"
