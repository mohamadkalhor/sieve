"""Artificial Analysis prices media on its leaderboards, and this reads them.

Every other source here answers a documented endpoint. This one reads a page,
which is a weaker contract, so the tests are mostly about the two ways that goes
wrong: the shape moving without anyone noticing, and a number being read in the
wrong unit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sieve.contracts import HttpResponse, RateLimit, SourceConfig
from sieve.http import FixturePlayer, fixture_slug
from sieve.sources.aa_media_prices import (
    BOARDS,
    SKIP_OVERRIDE,
    AAMediaPricesSource,
    ShapeMovedError,
    flight,
    model_of,
    price_of,
    rows_of,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def player() -> FixturePlayer:
    return FixturePlayer(FIXTURES)


def _page(board: Any) -> str:
    raw = json.loads((FIXTURES / f"{fixture_slug(board.url)}.json").read_text(encoding="utf-8"))
    body: str = raw["body"]
    return body


# --------------------------------------------------------------------------- #
# the unit
# --------------------------------------------------------------------------- #


def test_each_board_is_read_in_the_unit_it_publishes() -> None:
    """Four boards, four units, and none of them is per-something-else.

    A thousand images is a thousand images and a minute is sixty seconds, so the
    conversions are arithmetic. What must never happen is a per-image number
    landing under a per-second unit, because `cost_per_task` would then read it
    against the wrong `Shape` field and silently produce a cost.
    """
    by_modality = {b.modality: b for b in BOARDS}

    def rate(modality: Any, row: dict[str, Any]) -> tuple[float, float | None]:
        board = by_modality[modality]
        found = price_of(row, board)
        assert found is not None, modality
        return found

    image = by_modality["text-to-image"]
    assert rate("text-to-image", {image.key: 211})[0] == pytest.approx(0.211)
    assert image.unit == "usd_per_image"

    video = by_modality["text-to-video"]
    assert rate("text-to-video", {video.key: 12})[0] == pytest.approx(0.2)
    assert video.unit == "usd_per_second"

    speech = by_modality["text-to-speech"]
    assert rate("text-to-speech", {speech.key: 50})[0] == pytest.approx(50.0)
    assert speech.unit == "usd_per_1m_chars"

    stt = by_modality["speech-to-text"]
    # $1.67 per 1,000 minutes is $1.67 / 60,000 seconds
    assert rate("speech-to-text", {stt.key: 1.67})[0] == pytest.approx(1.67 / 60_000)

    sts = by_modality["speech-to-speech"]
    both = rate("speech-to-speech", {sts.key: 1.152, str(sts.out_key): 4.608})
    assert both[0] == pytest.approx(1.152 / 3600)
    assert both[1] == pytest.approx(4.608 / 3600)


def test_a_cross_modality_comparison_is_impossible_by_construction() -> None:
    """Not forbidden by convention -- unreachable.

    An image price and a video price carry different units, and `cost_per_task`
    reads `shape.images` for one and `shape.seconds` for the other. A profile
    that declares only images gets no cost for a video model at all, rather than
    a number that happens to be smaller.
    """
    from datetime import UTC, datetime

    from sieve.contracts import Price, Shape
    from sieve.scoring.weigh import cost_per_task

    def priced(unit: str, rate: float) -> Price:
        return Price(
            model_id="x",
            source="aa_media_prices",
            unit=unit,  # type: ignore[arg-type]
            per_unit=rate,
            observed_at=datetime.now(UTC),
        )

    images_only = Shape(images=4)
    assert cost_per_task(priced("usd_per_image", 0.211), images_only) == pytest.approx(0.844)
    assert cost_per_task(priced("usd_per_second", 0.2), images_only) is None


def test_a_zero_and_a_missing_price_are_both_absences() -> None:
    board = BOARDS[0]
    assert price_of({board.key: 0}, board) is None
    assert price_of({}, board) is None


def test_a_number_the_row_says_is_not_payable_is_not_a_price() -> None:
    """`priceDisplayOverride: "no_api"` means the model has no public API.

    Whatever number sits in the price field, nobody can pay it.
    """
    board = BOARDS[0]
    assert "no_api" in SKIP_OVERRIDE
    assert price_of({board.key: 211, "priceDisplayOverride": "no_api"}, board) is None
    assert price_of({board.key: 211, "priceDisplayOverride": None}, board) is not None

    result = AAMediaPricesSource().pull(
        SourceConfig(name="aa_media_prices"), FixturePlayer(FIXTURES)
    )
    skipped = [w for w in result.warnings if "not a price a caller can pay" in w]
    assert skipped, "and the count is reported rather than the row vanishing"


# --------------------------------------------------------------------------- #
# the shape
# --------------------------------------------------------------------------- #


def test_the_rows_are_decoded_not_pattern_matched() -> None:
    """Every row comes back from `json.JSONDecoder`, so a row is a row or it is
    nothing. A regex over the numbers would happily read a chart axis."""
    board = BOARDS[0]
    rows = rows_of(flight(_page(board)), board.key)
    assert rows and all(isinstance(r, dict) for r in rows)
    assert all(board.key in r or isinstance(r.get("values"), dict) for r in rows)


def test_the_same_rows_published_under_four_prop_names_are_all_found() -> None:
    """The speech boards publish one set of rows once per chart --
    `stsIndexHostModels`, `costPerHourOfInputAudioHostModels`,
    `pricingHostModels`, `tauChartModels` -- each a different subset.

    An earlier version of this parser anchored on `hostModels` and found four
    arrays of RSC placeholder strings instead. Anchoring on the price key finds
    every chart's copy and cannot go stale when one is renamed.
    """
    sts = next(b for b in BOARDS if b.modality == "speech-to-speech")
    page = _page(sts)
    assert page.count("HostModels") + page.count("ChartModels") > 1
    rows = rows_of(flight(page), sts.key)
    ids = {found[0] for r in rows if (found := model_of(r))}
    assert len(ids) > 10, "one chart's subset would be far fewer"


def test_a_page_whose_shape_has_moved_fails_loudly() -> None:
    """The whole risk of reading a page. Silence here would be an absence
    rendering as a fact, which is the failure this phase keeps finding."""
    with pytest.raises(ShapeMovedError):
        rows_of("<html>nothing like a leaderboard</html>", "pricePer1kImages")

    class Moved:
        def get(self, url: str, **_: object) -> HttpResponse:
            return HttpResponse(url=url, status=200, headers={}, body="<html>ok</html>")

        def rate_limit(self) -> RateLimit:
            return RateLimit()

    result = AAMediaPricesSource().pull(SourceConfig(name="aa_media_prices"), Moved())
    assert result.ok is False, "the run has to go red"
    assert result.prices == []
    assert any("no longer parses" in w for w in result.warnings)


def test_a_board_that_answers_badly_does_not_take_the_others_down() -> None:
    class Down:
        def get(self, url: str, **_: object) -> HttpResponse:
            return HttpResponse(url=url, status=503, headers={}, body=None)

        def rate_limit(self) -> RateLimit:
            return RateLimit()

    result = AAMediaPricesSource().pull(SourceConfig(name="aa_media_prices"), Down())
    assert result.ok is False
    assert len([w for w in result.warnings if "503" in w]) == len(BOARDS)


# --------------------------------------------------------------------------- #
# the join
# --------------------------------------------------------------------------- #


def test_the_join_is_a_uuid_on_every_board() -> None:
    """The reason a measurement source beats a marketplace.

    The row id is the same uuid the v2 API returns, so a price lands on the
    right model with no name matching at all -- on the image and video boards
    directly, and on the speech boards through the nested `model.id`, because
    the row's own id there identifies a *hosting* of the model.
    """
    for board in BOARDS:
        rows = rows_of(flight(_page(board)), board.key)
        found = [model_of(r) for r in rows]
        ids = {f[0] for f in found if f}
        assert ids, f"{board.modality} found no ids"
        assert all(len(i) == 36 and i.count("-") == 4 for i in ids), (
            f"{board.modality} ids are not uuids: {sorted(ids)[:3]}"
        )


def test_the_pull_prices_every_board(player: FixturePlayer) -> None:
    result = AAMediaPricesSource().pull(SourceConfig(name="aa_media_prices"), player)

    assert AAMediaPricesSource().needs_key is False
    assert result.ok is True
    assert result.prices
    assert {p.modality for p in result.prices} == {b.modality for b in BOARDS}
    assert all(p.source == "aa_media_prices" for p in result.prices)
    assert all(
        (p.per_unit is not None and p.per_unit > 0) or (p.input is not None and p.input > 0)
        for p in result.prices
    )
    # one price per (model, modality), not one per chart the row appeared on
    keys = [(p.model_id, p.modality) for p in result.prices]
    assert len(keys) == len(set(keys))


def test_the_prices_join_the_catalogue_by_uuid(player: FixturePlayer) -> None:
    """The end that matters: a price reaching a model something else scored.

    `aa_media` stores AA's uuid as an alias, so `merge_pull` folds this source's
    rows onto the canonical ids without a single fuzzy match.
    """
    from sieve.catalog.registry import merge_pull
    from sieve.sources.aa_media import AAMediaSource
    from sieve.store import Store

    store = Store(":memory:")
    scores = AAMediaSource().pull(
        SourceConfig(
            name="aa_media",
            options={"key_env": "ARTIFICIAL_ANALYSIS_API_KEY"},
            modalities=["text-to-image", "image-editing", "text-to-video", "image-to-video"],
        ),
        player,
    )
    store.upsert_models(scores.models)
    store.add_observations(scores.observations, snapshot=store.new_snapshot(source_rows=0))

    prices = AAMediaPricesSource().pull(SourceConfig(name="aa_media_prices"), player)
    folded, rewrite = merge_pull(prices, [m.id for m in store.models()], store.aliases())
    assert rewrite, "uuids in the alias table are the join, and it must fire"
    assert all(len(k) == 36 for k in rewrite), "every fold here is a uuid, never a name"

    store.upsert_models(folded.models)
    assert store.add_prices(folded.prices).added == len(folded.prices)

    priced = store.latest_prices("text-to-image")
    scored = {m.id for m in store.models("text-to-image")}
    landed = set(priced) & scored
    assert landed, "prices must reach models the catalogue already scored"


# --------------------------------------------------------------------------- #
# what the v2 API does not publish
# --------------------------------------------------------------------------- #


def test_elo_is_taken_only_where_the_api_does_not_cover_the_arena(
    player: FixturePlayer,
) -> None:
    """One organisation's single measurement must not arrive twice.

    `aa_media` already stores the Elo for every arena the v2 data API exposes,
    from the documented endpoint. Taking it from the page as well would put two
    rows under two source names for one number, and an axis reading both would
    count it twice.

    `video-editing` is the exception, and the reason this matters: the API has
    no such arena at all, so the page is the only place its Elo exists and the
    modality is unrankable without it.
    """
    result = AAMediaPricesSource().pull(SourceConfig(name="aa_media_prices"), player)
    scored = {o.modality for o in result.observations if o.field == "elo"}
    assert scored == {"video-editing"}

    covered = {b.modality for b in BOARDS if not b.scores}
    assert "text-to-video" in covered and "text-to-image" in covered


def test_win_rate_is_taken_everywhere_it_is_offered(player: FixturePlayer) -> None:
    """The v2 API publishes no win rate at all, so there is nothing to duplicate.

    It is a fraction, not a percentage: the row carries 0.65 for 65%.
    """
    result = AAMediaPricesSource().pull(SourceConfig(name="aa_media_prices"), player)
    rates = [o for o in result.observations if o.field == "win_rate"]
    assert rates, "the image boards publish it"
    assert all(o.unit == "fraction" for o in rates)
    assert all(0.0 <= o.value <= 1.0 for o in rates)
    # only the image boards carry it; the video boards do not
    assert {o.modality for o in rates} <= {"text-to-image", "image-editing"}


def test_the_confidence_interval_is_the_half_width_aa_media_also_stores(
    player: FixturePlayer,
) -> None:
    """A row with elo 1178.11 carries ciLower 1168.11, ciUpper 1188.11 and
    ciDelta 10, so `ciDelta` is the half-width -- which is what `ci95` means
    everywhere else in this store. Reading it as the full width would make every
    interval twice as wide and every model look half as settled."""
    board = next(b for b in BOARDS if b.modality == "video-editing")
    rows = rows_of(flight(_page(board)), board.key)
    # `rows_of` finds the object carrying the price key, which on a board page
    # is the `values` object itself rather than its `{formatted, values}` wrapper
    row = next(r for r in rows if r.get("ciDelta"))
    assert row["ciUpper"] - row["ciLower"] == pytest.approx(2 * row["ciDelta"])

    result = AAMediaPricesSource().pull(SourceConfig(name="aa_media_prices"), player)
    elo = [o for o in result.observations if o.field == "elo"]
    assert elo and all(o.ci95 is not None and o.n is not None for o in elo)


def test_video_editing_is_a_modality_that_ranks(player: FixturePlayer) -> None:
    """End to end: an arena the v2 API does not expose, made rankable from the
    page alone. Every one of its nine models carries both an Elo and a price,
    which is unusual for media and is why this modality works at all."""
    from sieve.contracts import MODALITIES

    assert "video-editing" in MODALITIES

    result = AAMediaPricesSource().pull(SourceConfig(name="aa_media_prices"), player)
    priced = {p.model_id for p in result.prices if p.modality == "video-editing"}
    scored = {o.model_id for o in result.observations if o.modality == "video-editing"}
    assert priced and scored
    assert priced == scored, "on this board every scored model is priced"

    repo = Path(__file__).resolve().parents[1]
    axes = {p.stem for p in (repo / "data" / "axes" / "video-editing").glob("*.yaml")}
    assert axes == {"quality", "maturity", "cost"}
    assert (repo / "profiles" / "video-editing" / "video_edit_general.yaml").exists()
