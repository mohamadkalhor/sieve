"""deepinfra — the second price source, and the one that publishes floats.

fal writes its prices in English and this one writes them as numbers, so the
tests here are not about reading prose. They are about the two things that can
still go wrong with a machine-readable price: **the unit** (every rate is in
cents, and reading one as dollars overstates a model by a hundred) and **the
modality** (deepinfra's `text-to-image` covers both text-to-image and
image-editing, and nothing in the response separates them).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sieve.contracts import SourceConfig
from sieve.http import FixturePlayer, fixture_slug
from sieve.sources.deepinfra import (
    CERTAIN,
    SPANNING,
    URL,
    DeepInfraSource,
    model_id_of,
    price_of,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def player() -> FixturePlayer:
    return FixturePlayer(FIXTURES)


def _rows() -> list[dict[str, Any]]:
    name = fixture_slug(URL)
    rows: list[dict[str, Any]] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return rows


# --------------------------------------------------------------------------- #
# the unit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("pricing", "amount", "unit"),
    [
        # 3 cents an image, not 3 dollars
        ({"cents_per_image_unit": 3.0}, 0.03, "usd_per_image"),
        ({"cents_per_output_sec": 5.0}, 0.05, "usd_per_second"),
        # the two spellings speech-to-text uses for one thing
        ({"cents_per_sec": 0.05}, 0.0005, "usd_per_second"),
        ({"cents_per_input_sec": 0.005}, 0.00005, "usd_per_second"),
        # cents per character -> dollars per million characters
        ({"cents_per_input_chars": 0.002}, 20.0, "usd_per_1m_chars"),
        ({"cents_per_input_token": 0.003}, 30.0, "usd_per_1m_tokens"),
    ],
)
def test_every_rate_is_in_cents(pricing: dict[str, Any], amount: float, unit: str) -> None:
    found = price_of(pricing)
    assert found is not None
    assert found[0] == pytest.approx(amount)
    assert found[1] == unit


def test_the_cents_scale_agrees_with_deepinfras_own_published_prices() -> None:
    """gemma-2-9b-it is $0.03/1M in and $0.06/1M out on deepinfra's price page.

    That is the check that the x10,000 is right rather than plausible: a scale
    error here is invisible, because every model moves together and the ranking
    still looks sensible.
    """
    found = price_of({"cents_per_input_token": 3e-06})
    assert found is not None
    assert found[0] == pytest.approx(0.03)
    assert found[1] == "usd_per_1m_tokens"


def test_a_zero_rate_is_not_a_free_model() -> None:
    """Three text-to-speech models publish `cents_per_input_chars: 0.0`.

    Stored, that number puts them at the top of every cost ranking on the
    strength of a field nobody filled in. It is an absence, and absences are
    reported as absences.
    """
    assert price_of({"cents_per_input_chars": 0.0}) is None
    assert price_of({}) is None

    result = DeepInfraSource().pull(SourceConfig(name="deepinfra"), FixturePlayer(FIXTURES))
    zeroed = [w for w in result.warnings if "no rate above zero" in w]
    assert zeroed, "and the count of them is reported"


def test_a_frame_is_not_a_second() -> None:
    """`cents_per_frame_unit` needs a frame rate, and the response has none."""
    assert price_of({"cents_per_frame_unit": 0.20833}) is None

    result = DeepInfraSource().pull(SourceConfig(name="deepinfra"), FixturePlayer(FIXTURES))
    framed = [w for w in result.warnings if "per frame" in w]
    assert framed, "skipped and named, not guessed at"


def test_the_prose_fields_are_never_read() -> None:
    """`pricing.short` says `$0.0500 / second (720p)` and is ignored.

    Parsing English when a float is offered is how a parser earns a wrong price
    it did not need. The float for that model is `cents_per_frame_unit`, which
    this source refuses -- so if `short` were being read, the model would be
    priced and it is not.
    """
    framed = [
        row
        for row in _rows()
        if (row.get("pricing") or {}).get("short")
        and (row.get("pricing") or {}).get("cents_per_frame_unit")
    ]
    assert framed, "the recording contains a model whose only float is per frame"

    result = DeepInfraSource().pull(SourceConfig(name="deepinfra"), FixturePlayer(FIXTURES))
    priced = {p.model_id for p in result.prices}
    for row in framed:
        assert model_id_of(row) not in priced


# --------------------------------------------------------------------------- #
# the modality
# --------------------------------------------------------------------------- #


def test_a_category_that_spans_two_modalities_is_offered_as_a_claim() -> None:
    """PLAN 2.2. `text-to-image` covers `Wan2.6-Image-Edit` and `FLUX-1-dev`.

    Nothing in the response says which is which, so the row is offered under
    both and kept only where the catalogue already holds that id in that
    modality -- which means something that *does* separate them measured it.
    """
    assert SPANNING["text-to-image"] == ("text-to-image", "image-editing")
    assert SPANNING["text-to-video"] == ("text-to-video", "image-to-video")

    result = DeepInfraSource().pull(SourceConfig(name="deepinfra"), FixturePlayer(FIXTURES))
    spanning = {m for group in SPANNING.values() for m in group}
    for model in result.models:
        if model.modality in spanning:
            assert model.id in result.provisional, f"{model.id} claims {model.modality}"
        else:
            assert model.id not in result.provisional


def test_a_certain_category_is_not_a_claim() -> None:
    """text-to-speech, speech-to-text and music name exactly one modality."""
    assert set(CERTAIN.values()) == {"text-to-speech", "speech-to-text", "music"}

    result = DeepInfraSource().pull(SourceConfig(name="deepinfra"), FixturePlayer(FIXTURES))
    certain = [m for m in result.models if m.modality in CERTAIN.values()]
    assert certain, "the recording carries them"
    assert all(m.id not in result.provisional for m in certain)


def test_a_claim_is_confirmed_in_that_modality_and_not_another() -> None:
    """The check the docstring always described and the code did not do.

    `confirm_provisional` compared ids alone, so an id the catalogue held as
    text-to-image confirmed a claim that it was image-editing -- which is the
    one thing that function exists to prevent.
    """
    from datetime import UTC, datetime

    from sieve.cli import confirm_provisional
    from sieve.contracts import ModelRef, PullResult
    from sieve.store import Store

    store = Store(":memory:")
    store.upsert_models(
        [
            ModelRef(
                id="acme/one",
                modality="text-to-image",
                name="One",
                creator="acme",
            )
        ]
    )

    def claim(modality: str) -> PullResult:
        return PullResult(
            source="deepinfra",
            models=[ModelRef(id="acme/one", modality=modality, name="One", creator="acme")],  # type: ignore[arg-type]
            provisional={"acme/one"},
        )

    kept, dropped = confirm_provisional(claim("text-to-image"), store)
    assert dropped == 0 and [m.id for m in kept.models] == ["acme/one"]

    kept, dropped = confirm_provisional(claim("image-editing"), store)
    assert dropped == 1 and kept.models == [], "the catalogue does not know it as image-editing"

    assert datetime.now(UTC)  # the import is used, and the clock is not mocked here


# --------------------------------------------------------------------------- #
# the pull
# --------------------------------------------------------------------------- #


def test_the_pull_prices_media_and_measures_nothing() -> None:
    result = DeepInfraSource().pull(SourceConfig(name="deepinfra"), FixturePlayer(FIXTURES))

    assert DeepInfraSource().needs_key is False, "no key, which is why it is usable at all"
    assert result.models and result.prices
    assert result.observations == [], "deepinfra measures no quality whatsoever"
    assert all(p.source == "deepinfra" for p in result.prices)
    assert all(
        (p.per_unit is not None and p.per_unit > 0) or (p.input is not None and p.input > 0)
        for p in result.prices
    ), "a price row with no rate on it is not a price"

    kinds = {str(row.get("type")) for row in _rows()}
    assert "text-generation" in kinds, "the recording keeps non-media rows on purpose"
    skipped = [w for w in result.warnings if "no Sieve modality" in w]
    assert skipped and "text-generation" in skipped[0]


def test_an_id_is_folded_the_way_the_catalogue_spells_it() -> None:
    # `canonical_id` folds a dot and a dash the same way, so `wan2.6` and
    # `wan2-6` cannot sit in the catalogue as two models that never meet
    assert model_id_of({"model_name": "Wan-AI/Wan2.6-Image-Edit"}) == "wan-ai/wan2-6-image-edit"
    assert model_id_of({"model_name": "openai/whisper-base"}) == "openai/whisper-base"
    assert model_id_of({"model_name": "no-slash-here"}) is None


def test_a_bad_response_stores_nothing_and_says_so() -> None:
    class Down:
        def get(self, url: str, **_: object) -> object:
            from sieve.contracts import HttpResponse

            return HttpResponse(url=url, status=503, headers={}, body=None)

        def rate_limit(self) -> object:
            from sieve.contracts import RateLimit

            return RateLimit()

    result = DeepInfraSource().pull(SourceConfig(name="deepinfra"), Down())  # type: ignore[arg-type]
    assert result.ok is False
    assert result.prices == [] and result.models == []
    assert any("503" in w for w in result.warnings)


def test_the_two_sources_are_kept_apart_rather_than_merged(player: FixturePlayer) -> None:
    """A price is one vendor charging to run one model, not the price of it.

    fal and deepinfra host some of the same models at different rates. Both rows
    are kept, distinguished by `source`, and nothing averages them.
    """
    from sieve.sources.fal import FalSource
    from sieve.store import Store

    store = Store(":memory:")
    for source in (FalSource(), DeepInfraSource()):
        result = source.pull(SourceConfig(name=source.name), player)
        store.upsert_models(result.models)
        store.add_prices(result.prices)

    rows = store.db.execute(
        "SELECT model_id, COUNT(DISTINCT source) AS sources FROM prices"
        " GROUP BY model_id, modality HAVING sources > 1"
    ).fetchall()
    assert rows, "the two catalogues overlap on at least one model"
    for row in rows:
        both = store.db.execute(
            "SELECT source FROM prices WHERE model_id=?", (row["model_id"],)
        ).fetchall()
        assert {r["source"] for r in both} == {"fal", "deepinfra"}
