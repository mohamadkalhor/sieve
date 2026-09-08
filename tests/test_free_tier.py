"""Phase 2 part 2 — the five defects in the free-tier media endpoints.

Each was found by checking the implementation against the live API rather than
against the documentation it was written from. They are pinned here because
every one of them is silent: the pull succeeds, the numbers look plausible, and
the wrong model wins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sieve.contracts import SourceConfig
from sieve.http import FixturePlayer
from sieve.sources import AAMediaSource
from sieve.sources.aa_media import ENDPOINTS, FREE_ENDPOINTS, model_id_of

FIXTURES = Path(__file__).parent / "fixtures"

ALL_FREE = {
    "free_music_instrumental": True,
    "free_music_with_vocals": True,
    "free_speech_to_text": True,
    "free_speech_to_speech": True,
}


@pytest.fixture
def free() -> object:
    cfg = SourceConfig(
        name="aa_media",
        key_env="ARTIFICIAL_ANALYSIS_API_KEY",
        modalities=["music", "speech-to-text", "speech-to-speech"],
        options=dict(ALL_FREE),
    )
    return AAMediaSource().pull(cfg, FixturePlayer(FIXTURES))


# --------------------------------------------------------------------------- #
# 1 · music collided with itself
# --------------------------------------------------------------------------- #


def test_the_two_music_leaderboards_do_not_overwrite_each_other(free: object) -> None:
    """Instrumental and with-vocals are two votes, not one.

    Both used to be stored as a field called `elo` for the same model in one
    pull, and observations are unique on (model, source, field, observed_at), so
    one of them silently lost every time.
    """
    fields = {o.field for o in free.observations}  # type: ignore[attr-defined]
    assert "elo:instrumental" in fields
    assert "elo:with_vocals" in fields
    assert "elo" not in fields, "a bare `elo` here means the two collapsed again"

    by_field: dict[str, dict[str, float]] = {}
    for observation in free.observations:  # type: ignore[attr-defined]
        by_field.setdefault(observation.field, {})[observation.model_id] = observation.value

    both = set(by_field["elo:instrumental"]) & set(by_field["elo:with_vocals"])
    assert both, "some models appear on both leaderboards"
    differing = [
        m for m in both if by_field["elo:instrumental"][m] != by_field["elo:with_vocals"][m]
    ]
    assert differing, "if every model scored the same on both, they are not two contests"

    # the pair the brief names, read from the recording
    suno = "suno/suno-v5-5"
    assert by_field["elo:instrumental"][suno] == pytest.approx(1186)
    assert by_field["elo:with_vocals"][suno] == pytest.approx(1170)


# --------------------------------------------------------------------------- #
# 2 · text-to-speech was pulled twice
# --------------------------------------------------------------------------- #


def test_text_to_speech_is_served_by_exactly_one_endpoint() -> None:
    """It was in both tables, so two sources wrote the same (model, field)."""
    free_modalities = {spec.modality for spec in FREE_ENDPOINTS.values()}
    assert "text-to-speech" in ENDPOINTS.values()
    assert "text-to-speech" not in free_modalities, (
        "the arena endpoint wins: it publishes `rank`, it is the documented one, and "
        "the free tier's only advantage is one extra model"
    )


# --------------------------------------------------------------------------- #
# 3 · the confidence interval was stored as a measurement
# --------------------------------------------------------------------------- #


def test_the_interval_lands_on_the_score_and_not_beside_it(free: object) -> None:
    """`ci_95` describes the Elo; it is not a second Elo.

    The old generic parser stored every numeric key it found, so the interval
    arrived as a field of its own and any axis over "all aa_media fields" would
    have weighted it as if it were a benchmark.
    """
    fields = {o.field for o in free.observations}  # type: ignore[attr-defined]
    assert "ci_95" not in fields and "ci95" not in fields

    music = [o for o in free.observations if o.field.startswith("elo:")]  # type: ignore[attr-defined]
    assert music, "the music endpoints did pull"
    assert any(o.ci95 is not None for o in music), "the interval survived onto the score"
    for observation in music:
        assert observation.unit == "elo"


# --------------------------------------------------------------------------- #
# 4 · speech-to-speech was never pulled at all
# --------------------------------------------------------------------------- #


def test_speech_to_speech_is_pulled_with_its_three_scores(free: object) -> None:
    """The endpoint existed and the `Modality` literal had no name for it."""
    rows = [o for o in free.observations if o.modality == "speech-to-speech"]  # type: ignore[attr-defined]
    assert rows, "nothing was stored for speech-to-speech"

    counts: dict[str, int] = {}
    for observation in rows:
        counts[observation.field] = counts.get(observation.field, 0) + 1

    # the three are not interchangeable, and their coverage is the proof
    assert counts["bba_score"] > counts["fdb_score"] > counts["tau_voice_score"], counts
    assert counts["bba_score"] == 34
    assert counts["fdb_score"] == 26
    assert counts["tau_voice_score"] == 21


# --------------------------------------------------------------------------- #
# 5 · the direction of aa_wer_index
# --------------------------------------------------------------------------- #


def test_the_word_error_rate_axis_says_lower_is_better() -> None:
    """Settled from the published leaderboard, not guessed.

    artificialanalysis.ai ranks speech-to-text by AA-WER -- "% of words
    transcribed incorrectly" -- with Fun-Realtime-ASR-preview at 1.7% first and
    Google's legacy Chirp at 31.2% last. Guessing the other way would have made
    Chirp the single best model in the field.
    """
    import yaml

    repo = Path(__file__).resolve().parents[1]
    axis = yaml.safe_load(
        (repo / "data" / "axes" / "speech-to-text" / "accuracy.yaml").read_text(encoding="utf-8")
    )
    assert axis["higher_is_better"] is False
    assert axis["fields"][0]["field"] == "aa_wer_index"


def test_the_worst_published_model_is_the_one_with_the_highest_rate(free: object) -> None:
    """A direction check that reads the data rather than the axis file."""
    wer = {
        o.model_id: o.value
        for o in free.observations  # type: ignore[attr-defined]
        if o.field == "aa_wer_index"
    }
    assert wer
    worst = max(wer, key=lambda m: wer[m])
    assert "chirp" in worst, f"the highest rate should be Google's legacy Chirp, got {worst}"


def test_the_rate_is_rounded_and_that_limit_is_written_down() -> None:
    """44 of 58 models read 0.0, so this axis separates broken from working only."""
    body = json.loads(
        (FIXTURES / "artificialanalysis_ai_api_v2_media_speech_to_text_models_free.json").read_text(
            encoding="utf-8"
        )
    )
    values = [r["aa_wer_index"] for r in body["data"] if r.get("aa_wer_index") is not None]
    assert len(set(values)) <= 4, "if this grows, the endpoint stopped rounding -- reweight"

    repo = Path(__file__).resolve().parents[1]
    axis = (repo / "data" / "axes" / "speech-to-text" / "accuracy.yaml").read_text(encoding="utf-8")
    # the wrapped comment in the axis file, matched on a fragment that fits one line
    assert "decimal place" in axis, "the limit has to be written where it is used"


# --------------------------------------------------------------------------- #
# ids, where no slug is published
# --------------------------------------------------------------------------- #


def test_a_uuid_is_never_used_as_a_model_id() -> None:
    """Music and speech-to-text rows carry no `slug`, and their `id` is a UUID.

    Falling through to it produced `suno/8a999846-4c1d-...`, which matches
    nothing and never will.
    """
    row = {
        "id": "8a999846-4c1d-4ce7-a8b7-1310a7166fd7",
        "name": "Suno V5.5",
        "model_creator": {"name": "Suno"},
    }
    assert model_id_of(row) == "suno/suno-v5-5"

    assert model_id_of({"id": "8a999846-4c1d-4ce7-a8b7-1310a7166fd7"}) is None
    assert model_id_of({"slug": "kept", "id": "8a999846-4c1d-4ce7-a8b7-1310a7166fd7"}) == "kept"


def test_every_free_tier_model_has_a_usable_id(free: object) -> None:
    """No id may be a UUID, and none may repeat the creator after a comma."""
    import re

    uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
    for model in free.models:  # type: ignore[attr-defined]
        creator, _, slug = model.id.partition("/")
        assert creator and slug, model.id
        assert not uuid.search(model.id), f"{model.id} is a UUID, not an id"
        assert "," not in model.id, f"{model.id} kept the name's trailing creator"
        assert not slug.endswith(f"-{creator}"), f"{model.id} repeats its creator"


# --------------------------------------------------------------------------- #
# a model is one row per model PER MODALITY
# --------------------------------------------------------------------------- #


def test_a_model_on_two_leaderboards_keeps_both_scores() -> None:
    """The sibling of the price-had-no-modality bug, and worse.

    `observations` carried a `modality` column from the beginning but guarded
    uniqueness with (model_id, source, field, observed_at). A model measured on
    two leaderboards -- one model id, two modalities -- writes `elo` twice in one
    pull with the same pull-time stamp, and the second was silently dropped.

    Measured against these recordings before the fix: image-editing kept 13 of
    its 40 models and image-to-video 14 of 40, while text-to-image and
    text-to-video, pulled first, kept all 40.
    """
    from sieve.store import Store

    cfg = SourceConfig(
        name="aa_media",
        key_env="ARTIFICIAL_ANALYSIS_API_KEY",
        modalities=["text-to-image", "image-editing", "text-to-video", "image-to-video"],
    )
    pulled = AAMediaSource().pull(cfg, FixturePlayer(FIXTURES))

    store = Store(":memory:")
    store.upsert_models(pulled.models)
    added = store.add_observations(pulled.observations)
    assert added == len(pulled.observations), (
        f"{len(pulled.observations) - added} observations were dropped on the way in"
    )

    scored: dict[str, set[str]] = {}
    for observation in pulled.observations:
        scored.setdefault(observation.modality, set()).add(observation.model_id)

    for modality in ("image-editing", "image-to-video", "text-to-image", "text-to-video"):
        held = {
            row["model_id"]
            for row in store.db.execute(
                "SELECT DISTINCT model_id FROM observations WHERE modality = ?", (modality,)
            )
        }
        assert held == scored[modality], (
            f"{modality}: pulled {len(scored[modality])} models, store kept {len(held)}"
        )
