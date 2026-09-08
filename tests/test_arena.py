"""Phase 2 part 7 — LMArena, human preference Elo.

Every other source is a benchmark: fixed questions, scored. This one is people
choosing between two answers, which measures the thing a benchmark cannot —
whether the output was the one somebody wanted.

**This source never reaches the network in a test.** It is disabled by default
in `sieve.toml.example` for the same reason: it is a bulk download of somebody
else's dataset and it has no business running on every CI push.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sieve.contracts import SourceConfig
from sieve.http import FixturePlayer, fixture_slug
from sieve.sources.arena import (
    CONFIGS,
    INFO,
    ArenaSource,
    ci95_of,
    field_for,
    model_id_of,
)

FIXTURES = Path(__file__).parent / "fixtures"

#: the three leaderboards recorded on 2026-09-08
RECORDED = ["llm", "text-to-video", "image-editing"]


@pytest.fixture
def player() -> FixturePlayer:
    return FixturePlayer(FIXTURES)


def _config(**kw: object) -> SourceConfig:
    return SourceConfig(name="arena", modalities=["text-to-video", "image-editing"], **kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# ids and fields
# --------------------------------------------------------------------------- #


def test_an_effort_mode_in_brackets_folds_into_the_id() -> None:
    """The arena writes `gpt-image-2 (medium)`; brackets match nothing, ever."""
    assert (
        model_id_of({"model_name": "gpt-image-2 (medium)", "organization": "openai"})
        == "openai/gpt-image-2-medium"
    )
    assert (
        model_id_of({"model_name": "claude-opus-5-high", "organization": "anthropic"})
        == "anthropic/claude-opus-5-high"
    )
    assert model_id_of({"model_name": "", "organization": "x"}) is None


def test_six_leaderboards_measure_llm_and_must_not_share_a_field() -> None:
    """Writing them all as `elo` is exactly how the music endpoints lost data."""
    llm_configs = sorted(c for c, m in CONFIGS.items() if m == "llm")
    assert len(llm_configs) >= 5
    fields = {field_for(c, "overall") for c in llm_configs}
    assert len(fields) == len(llm_configs), "one field per leaderboard, no collisions"
    assert field_for("text", "overall") == "elo:text"
    assert field_for("text_to_image", "3d_modeling") == "elo:text_to_image:3d_modeling"


def test_the_interval_is_the_wider_half_of_the_published_bounds() -> None:
    """The bounds are not always symmetric about the rating."""
    assert ci95_of(
        {"rating": 1500.0, "rating_lower": 1490.0, "rating_upper": 1512.0}
    ) == pytest.approx(12.0)
    assert ci95_of({"rating": 1500.0, "rating_lower": None, "rating_upper": 1512.0}) is None


# --------------------------------------------------------------------------- #
# the pull
# --------------------------------------------------------------------------- #


def test_the_pull_reads_elo_rank_votes_and_the_published_date(player: FixturePlayer) -> None:
    result = ArenaSource().pull(_config(), player)

    assert result.ok, f"the recorded leaderboards should all read cleanly: {result.warnings}"
    assert result.models and result.observations
    assert result.prices == [], "the arena measures preference, not price"
    assert result.capabilities == {}, "and says nothing about what a model can do"

    elo = next(o for o in result.observations if o.field.startswith("elo:"))
    assert elo.unit == "elo"
    assert elo.n is not None and elo.n > 0, "vote count carries through as the sample size"
    assert elo.ci95 is not None and elo.ci95 > 0

    # the thing no other source has: a real publication date
    assert elo.observed_at.year == 2026
    assert elo.observed_at < elo.pulled_at, (
        "observed_at is when the leaderboard was published, not when we fetched it"
    )


def test_the_publication_date_is_why_an_hourly_pull_does_not_duplicate(
    player: FixturePlayer,
) -> None:
    """`aa_llm` stamps pull time and writes a fresh set of rows every hour.

    Observations are unique on (model, source, field, observed_at), so a source
    that dates its rows by publication deduplicates itself. Two pulls of an
    unchanged leaderboard produce the same keys.
    """
    first = ArenaSource().pull(_config(), player)
    second = ArenaSource().pull(_config(), player)

    def keys(result: object) -> set[tuple[str, str, str]]:
        return {
            (o.model_id, o.field, o.observed_at.isoformat())
            for o in result.observations  # type: ignore[attr-defined]
        }

    assert keys(first) == keys(second) and keys(first)


def test_a_leaderboard_that_answers_with_nothing_is_reported(player: FixturePlayer) -> None:
    """Answered-and-empty is not the same as failed, and not the same as fine."""
    # `llm` pulls six leaderboards and only `text` was recorded, so the others
    # answer 404 -- which must be reported rather than passed over
    result = ArenaSource().pull(SourceConfig(name="arena", modalities=["llm"]), player)
    assert not result.ok
    assert any("agent" in w or "webdev" in w for w in result.warnings)
    # ...and what *was* readable is still stored
    assert any(o.field == "elo:text" for o in result.observations)


def test_a_config_with_no_sieve_modality_is_never_pulled() -> None:
    """`video_edit` is video-to-video, which Sieve has no name for."""
    assert "video_edit" not in CONFIGS
    assert "text_style_control" not in CONFIGS, "a variant leaderboard, not a modality"


# --------------------------------------------------------------------------- #
# the cache
# --------------------------------------------------------------------------- #


def test_an_unchanged_dataset_is_not_downloaded_again(
    player: FixturePlayer, tmp_path: Path
) -> None:
    """The dataset's commit sha is this source's ETag: one cheap request."""
    cfg = _config(options={"cache": str(tmp_path)})

    first = ArenaSource().pull(cfg, player)
    assert first.observations, "the first pull reads it"
    assert (tmp_path / "arena-revision.json").is_file()

    second = ArenaSource().pull(cfg, player)
    assert second.observations == [], "the second pull sees the same sha and stops"
    assert any("unchanged since the last pull" in w for w in second.warnings)

    # and it says how to force one, rather than leaving the reader stuck
    assert any("Delete the cache file" in w for w in second.warnings)


def test_the_cache_records_the_sha_the_dataset_published(
    player: FixturePlayer, tmp_path: Path
) -> None:
    ArenaSource().pull(_config(options={"cache": str(tmp_path)}), player)
    held = json.loads((tmp_path / "arena-revision.json").read_text(encoding="utf-8"))

    info = json.loads((FIXTURES / f"{fixture_slug(INFO)}.json").read_text(encoding="utf-8"))
    assert held["sha"] == info["sha"]


def test_without_a_cache_configured_it_simply_pulls(player: FixturePlayer) -> None:
    """A missing cache is a slower pull, never a refusal."""
    result = ArenaSource().pull(_config(), player)
    assert result.observations


def test_the_licence_is_attributed_as_cc_by_requires() -> None:
    """CC-BY is only a licence if the attribution is actually there."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "sources.md").read_text(encoding="utf-8")
    flat = " ".join(doc.split())
    assert "CC-BY-4.0" in flat
    assert "lmarena-ai/leaderboard-dataset" in flat
    assert "Creative Commons Attribution 4.0" in flat


def test_it_is_off_by_default_in_the_shipped_config() -> None:
    """A bulk read of somebody else's dataset should not run on every push."""
    example = (Path(__file__).resolve().parents[1] / "sieve.toml.example").read_text(
        encoding="utf-8"
    )
    block = example.split("[sources.arena]", 1)[1].split("[sources.", 1)[0]
    assert "enabled = false" in block
