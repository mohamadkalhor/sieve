"""Sources, inventory, targets and profile validation, all offline.

Nothing here touches the network: every connector is handed the fixture player,
which serves recorded payloads by URL.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

from sieve.contracts import Chain, InventoryConfig, SourceConfig, TargetConfig
from sieve.http import FixturePlayer, fixture_slug
from sieve.inventory import OpenAICompatInventory, StaticListInventory
from sieve.profiles import parse_profile, save_profile, validate_profile
from sieve.profiles.load import ProfileError, load_profiles
from sieve.sources import AALLMSource, AAMediaSource, ManualSource, OpenRouterSource
from sieve.sources.aa_media import parse_ci95
from sieve.sources.openrouter import URL as OR_URL
from sieve.store.db import now
from sieve.targets import FileTarget

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def player() -> FixturePlayer:
    return FixturePlayer(FIXTURES)


# --------------------------------------------------------------------------- #
# OpenRouter
# --------------------------------------------------------------------------- #


def test_openrouter_needs_no_key_and_yields_prices_and_capabilities(
    player: FixturePlayer,
) -> None:
    source = OpenRouterSource()
    assert source.needs_key is False

    result = source.pull(SourceConfig(name="openrouter"), player)

    assert len(result.models) == 60
    assert len(result.prices) == 60
    assert len(result.capabilities) == 60
    assert result.observations == [], "OpenRouter publishes no measurements of its own"

    price = result.prices[0]
    assert price.unit == "usd_per_1m_tokens"
    assert price.input is None or price.input >= 0

    with_context = [c for c in result.capabilities.values() if c.context_window]
    assert with_context, "context_length is what `require: context_min` reads"
    multimodal = [c for c in result.capabilities.values() if set(c.input_modalities) - {"text"}]
    assert multimodal, "input_modalities is what `require: input_modalities` reads"


def test_openrouter_prices_are_per_million_tokens(player: FixturePlayer) -> None:
    """The API publishes USD per token; a price per 1M is three zeroes apart."""
    body = player.get(OR_URL).body
    raw = next(
        e
        for e in body["data"]
        if (e.get("pricing") or {}).get("prompt") not in (None, "0", "0.0", "-1")
    )
    expected = float(raw["pricing"]["prompt"]) * 1_000_000

    result = OpenRouterSource().pull(SourceConfig(name="openrouter"), player)
    found = next(p for p in result.prices if p.model_id == str(raw["id"]).lower())
    assert found.input == pytest.approx(expected)


def test_openrouter_does_not_store_republished_aa_numbers(player: FixturePlayer) -> None:
    result = OpenRouterSource().pull(SourceConfig(name="openrouter"), player)
    assert all(o.source != "aa_llm" for o in result.observations)


# --------------------------------------------------------------------------- #
# Artificial Analysis
# --------------------------------------------------------------------------- #


def _aa_config() -> SourceConfig:
    return SourceConfig(name="aa_llm", key_env="ARTIFICIAL_ANALYSIS_API_KEY")


def test_aa_llm_maps_every_field_with_its_documented_unit(player: FixturePlayer) -> None:
    """Against the real recording: 60 models, and every unit as documented."""
    result = AALLMSource().pull(_aa_config(), player)

    assert len(result.models) == 60, "the recording is trimmed to 60 of the 644 published"
    for model in result.models:
        creator, _, slug = model.id.partition("/")
        assert creator and slug, f"canonical id is <creator>/<slug>, got {model.id}"
        assert model.id == model.id.lower()

    units = {(o.field, o.unit) for o in result.observations}
    assert ("artificial_analysis_intelligence_index", "index_0_100") in units
    assert ("gpqa", "fraction") in units
    assert ("terminalbench_v2_1", "fraction") in units
    assert ("median_output_tokens_per_second", "tokens_per_s") in units
    assert ("median_time_to_first_token_seconds", "seconds") in units

    # 672 observations over 60 models on 2026-09-08. Asserted as a floor, not an
    # equality: AA adds evaluations, and a new one must not fail this test.
    per_model = len(result.observations) / len(result.models)
    assert per_model >= 11, f"every published evaluation should land, got {per_model:.1f}"

    assert len(result.prices) == 60, "the LLM endpoint prices every model it lists"
    price = result.prices[0]
    assert price.unit == "usd_per_1m_tokens" and price.output is not None


def test_aa_llm_stores_unknown_evaluation_keys_and_says_so(
    player: FixturePlayer, tmp_path: Path
) -> None:
    """AA ships a benchmark on the site before the API documents it.

    Every one of the 17 evaluation keys in the 2026-09-08 recording is already
    mapped, so the recording cannot exercise this path -- a real pull of it warns
    about nothing. Rather than keep a hand-built fixture alive for one behaviour,
    this takes the real recording and adds a single key to it. Nothing here is an
    invented *measurement*: the number is arbitrary and the test never reads it,
    only that an unmapped key is stored, warned about, and given the unit its
    name implies.
    """
    slug = fixture_slug("https://artificialanalysis.ai/api/v2/data/llms/models")
    body = json.loads((FIXTURES / f"{slug}.json").read_text(encoding="utf-8"))
    body["data"][0]["evaluations"]["aa_briefcase"] = 0.5
    body["data"][0]["evaluations"]["coding_agent_index"] = 50
    (tmp_path / f"{slug}.json").write_text(json.dumps(body), encoding="utf-8")

    result = AALLMSource().pull(_aa_config(), FixturePlayer(tmp_path))

    stored = {o.field for o in result.observations}
    assert "aa_briefcase" in stored, "an unknown key must still be stored"
    assert "coding_agent_index" in stored

    warning = " ".join(result.warnings)
    assert "aa_briefcase" in warning and "coding_agent_index" in warning
    assert (
        next(o for o in result.observations if o.field == "coding_agent_index").unit
        == "index_0_100"
    ), "an unknown *_index is still an index"


def test_aa_llm_warns_about_nothing_in_the_recording(player: FixturePlayer) -> None:
    """The companion to the test above, and the reason it needs a shaped body.

    If this ever fails, AA has published an evaluation key the source does not
    map -- which is exactly the signal the warning exists to raise.
    """
    result = AALLMSource().pull(_aa_config(), player)
    assert result.warnings == [], f"unmapped keys appeared: {result.warnings}"


def test_aa_llm_without_a_key_warns_rather_than_inventing(
    player: FixturePlayer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARTIFICIAL_ANALYSIS_API_KEY", raising=False)
    empty = FixturePlayer(FIXTURES / "does-not-exist")
    result = AALLMSource().pull(_aa_config(), empty)
    assert result.models == [] and result.observations == []
    assert "no key" in " ".join(result.warnings)


def test_aa_media_makes_one_field_per_published_category(player: FixturePlayer) -> None:
    """Categories come back as a list whose name sits in one of three columns."""
    cfg = SourceConfig(
        name="aa_media",
        key_env="ARTIFICIAL_ANALYSIS_API_KEY",
        modalities=["text-to-video", "text-to-image"],
    )
    result = AAMediaSource().pull(cfg, player)

    fields = {o.field for o in result.observations}
    assert {"elo", "rank", "appearances", "ci95"} <= fields
    # format_category, subject_matter_category and style_category respectively
    assert "elo:moving_camera" in fields
    assert "elo:physics" in fields
    assert "elo:anime" in fields

    modalities = {m.modality for m in result.models}
    assert modalities == {"text-to-video", "text-to-image"}

    elo = next(o for o in result.observations if o.field == "elo")
    assert elo.unit == "elo" and elo.n is not None and elo.ci95 is not None

    # a category carries its own sample size, not the model's overall one
    physics = next(o for o in result.observations if o.field == "elo:physics")
    assert physics.n is not None and physics.ci95 is not None


def test_aa_media_publishes_no_price_so_none_is_invented(player: FixturePlayer) -> None:
    """The arena endpoints carry no pricing key of any kind.

    The hand-built fixtures had one, so the media `cost` axes were written
    against a price that does not exist. Nothing may fill that in: a media model
    has no cost from this source, and the axis has to report the coverage loss.
    """
    cfg = SourceConfig(
        name="aa_media",
        key_env="ARTIFICIAL_ANALYSIS_API_KEY",
        modalities=["text-to-video", "text-to-image", "image-editing", "text-to-speech"],
    )
    result = AAMediaSource().pull(cfg, player)

    assert result.models, "the pull did happen"
    assert result.prices == [], "no arena row publishes a price; none may be invented"


def test_aa_media_records_which_endpoints_publish_categories(player: FixturePlayer) -> None:
    """Two of the five arena endpoints publish no categories at all.

    `include_categories=true` is sent to all five; image-editing and
    text-to-speech answer without a `categories` list. An axis over a category
    for those modalities would sit at coverage 0 forever, so this pins which
    ones can carry category axes and which cannot.
    """
    cfg = SourceConfig(
        name="aa_media",
        key_env="ARTIFICIAL_ANALYSIS_API_KEY",
        modalities=["text-to-image", "image-editing", "text-to-video", "text-to-speech"],
    )
    result = AAMediaSource().pull(cfg, player)

    by_modality: dict[str, set[str]] = {}
    for observation in result.observations:
        if observation.field.startswith("elo:"):
            by_modality.setdefault(observation.modality, set()).add(observation.field)

    assert len(by_modality.get("text-to-video", set())) >= 25
    assert len(by_modality.get("text-to-image", set())) >= 10
    assert by_modality.get("image-editing", set()) == set()
    assert by_modality.get("text-to-speech", set()) == set()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-12/12", 12.0), ("-40/38", 40.0), ("9", 9.0), (None, None), ("n/a", None)],
)
def test_ci95_is_parsed_from_the_string_the_api_writes(
    raw: str | None, expected: float | None
) -> None:
    assert parse_ci95(raw) == expected


def test_aa_media_free_tier_is_off_unless_switched_on(player: FixturePlayer) -> None:
    cfg = SourceConfig(name="aa_media", modalities=["text-to-image"])
    result = AAMediaSource().pull(cfg, player)
    assert not any("free tier" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# manual drops
# --------------------------------------------------------------------------- #


def test_manual_reads_csv_and_reports_the_bad_line(tmp_path: Path) -> None:
    drop = tmp_path / "observations"
    drop.mkdir()
    (drop / "my_eval.csv").write_text(
        "model_id,modality,source,field,value,unit,n,ci95,observed_at\n"
        "anthropic/claude-opus-5,llm,my_eval,house_style,0.82,fraction,40,0.03,2026-09-01T00:00:00Z\n"
        "anthropic/claude-sonnet-5,llm,my_eval,house_style,0.71,fraction,40,0.04,2026-09-01T00:00:00Z\n"
        ",llm,my_eval,house_style,0.50,fraction,,,\n",
        encoding="utf-8",
    )
    result = ManualSource().pull(SourceConfig(name="manual", dir=str(drop)), FixturePlayer(drop))

    assert len(result.observations) == 2
    assert {o.source for o in result.observations} == {"my_eval"}
    assert result.observations[0].field == "house_style"
    assert len(result.models) == 2
    assert any("line 4" in w and "model_id" in w for w in result.warnings)


def test_manual_says_so_when_there_is_nothing_to_read(tmp_path: Path) -> None:
    result = ManualSource().pull(
        SourceConfig(name="manual", dir=str(tmp_path / "nope")), FixturePlayer(tmp_path)
    )
    assert result.observations == []
    assert "does not exist" in " ".join(result.warnings)


# --------------------------------------------------------------------------- #
# inventory
# --------------------------------------------------------------------------- #


def test_openai_compat_reads_ids_and_believes_the_gateway(tmp_path: Path) -> None:
    url = "http://localhost:20128/v1/models"
    (tmp_path / f"{fixture_slug(url)}.json").write_text(
        '{"data": ['
        '{"id": "oc-go/glm-5.3", "context_window": 200000, "tools": true},'
        '{"id": "anthropic/claude-opus-5"}'
        "]}",
        encoding="utf-8",
    )
    found = OpenAICompatInventory().list(
        InventoryConfig(name="gateway", kind="openai_compat", base_url="http://localhost:20128"),
        FixturePlayer(tmp_path),
    )
    assert [r.local_id for r in found] == ["oc-go/glm-5.3", "anthropic/claude-opus-5"]
    assert found[0].capability.context_window == 200000
    assert found[0].capability.tools is True
    assert found[1].capability.tools is None, "silence is unknown, not false"


def test_static_list_inventory_dedupes(tmp_path: Path) -> None:
    found = StaticListInventory().list(
        InventoryConfig(
            name="pinned",
            kind="list",
            models=["anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5", " "],
        ),
        FixturePlayer(tmp_path),
    )
    assert [r.local_id for r in found] == ["anthropic/claude-sonnet-5"]


# --------------------------------------------------------------------------- #
# targets
# --------------------------------------------------------------------------- #


def test_file_target_writes_only_when_asked_and_reads_back(tmp_path: Path) -> None:
    target = FileTarget()
    cfg = TargetConfig(name="out", kind="file", dir=str(tmp_path))
    chain = Chain(
        profile="coder",
        computed_at=now(),
        primary="anthropic/claude-opus-5",
        fallbacks=["z-ai/glm-5.3"],
        local={"anthropic/claude-opus-5": ["gw/claude-opus-5"]},
    )

    dry = target.write(cfg, [chain], True)
    assert dry.dry_run and not list(tmp_path.iterdir()), "a dry run writes nothing"

    target.write(cfg, [chain], False)
    assert {p.name for p in tmp_path.iterdir()} == {"coder.json", "chains.csv"}
    assert target.current(cfg) == {"coder": ["anthropic/claude-opus-5", "z-ai/glm-5.3"]}

    rows = (tmp_path / "chains.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "profile,position,model_id,local_ids"
    assert rows[1].startswith("coder,0,anthropic/claude-opus-5,gw/claude-opus-5")


# --------------------------------------------------------------------------- #
# profiles
# --------------------------------------------------------------------------- #


BAD = Path(__file__).parent / "bad_profiles"


def test_the_bad_profiles_are_all_rejected() -> None:
    problems: dict[str, list[str]] = {}
    for file in sorted(BAD.glob("*.yaml")):
        try:
            profile = parse_profile(file)
        except ProfileError as exc:
            problems[file.name] = [str(exc)]
            continue
        problems[file.name] = list(validate_profile(profile, {("llm", "agentic_coding")}))

    assert len(problems) == 3
    assert all(problems.values()), f"something passed that should not: {problems}"
    assert any("sum to" in " ".join(v) for v in problems.values())
    assert any("unknown axis" in " ".join(v) for v in problems.values())
    assert any("unknown constraint" in " ".join(v) for v in problems.values())


def test_a_good_profile_round_trips_and_keeps_its_comments(tmp_path: Path) -> None:
    source = tmp_path / "llm" / "coder.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(
        "# the coding seat\n"
        "name: coder\n"
        "modality: llm\n"
        "purpose: agentic coding inside a repository\n"
        "weights:\n"
        "  agentic_coding: 0.6\n"
        "  cost: 0.4\n"
        "shape: {in: 30000, out: 4000}\n",
        encoding="utf-8",
    )

    profile = next(iter(load_profiles(tmp_path)))
    assert profile.name == "coder" and profile.shape.in_tokens == 30000
    assert not list(validate_profile(profile, {("llm", "agentic_coding"), ("llm", "cost")}))

    moved = profile.model_copy(update={"weights": {"agentic_coding": 0.7, "cost": 0.3}})
    save_profile(tmp_path, moved)

    text = source.read_text(encoding="utf-8")
    assert "# the coding seat" in text, "an API write must not throw a comment away"
    assert "0.7" in text
    assert "in: 30000" in text, "shape keeps the YAML spelling"

    assert next(iter(load_profiles(tmp_path))).weights == {"agentic_coding": 0.7, "cost": 0.3}


def test_the_recordings_match_what_the_manifest_claims(player: FixturePlayer) -> None:
    """The counts a reader is asked to trust, asserted rather than asserted-in-prose.

    `RECORDINGS.json` says what each file came from, how many rows the endpoint
    published on 2026-09-08 and how many were kept. A trimmed recording whose
    manifest drifts is worse than none, because every count in the tests below
    is then quietly measuring something else.
    """
    manifest = json.loads((FIXTURES / "RECORDINGS.json").read_text(encoding="utf-8"))
    assert len(manifest) == 16

    for name, entry in manifest.items():
        body = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        # Three shapes: AA answers `{data: [...]}`, fal `{items: [...]}`, the
        # Hugging Face datasets-server `{rows: [...]}`.
        rows = body
        if isinstance(body, dict):
            for key in ("data", "items", "rows"):
                if key in body:
                    rows = body[key]
                    break

        if entry["rows_kept"] is None:
            # not a row list at all -- the dataset-metadata recording, which is
            # there so the cache has a commit sha to compare against
            assert isinstance(body, dict) and body, f"{name} should still be a body"
            continue

        assert len(rows) == entry["rows_kept"], f"{name} holds {len(rows)}, manifest says {entry}"
        assert entry["rows_kept"] <= entry["rows_published"]
        assert f"{fixture_slug(entry['url'], _params_of(entry))}.json" == name

    llms = manifest["artificialanalysis_ai_api_v2_data_llms_models.json"]
    assert llms["rows_published"] == 644, "the figure the phase 1 report could not claim"

    result = AALLMSource().pull(_aa_config(), player)
    assert (len(result.models), len(result.observations), len(result.prices)) == (60, 672, 60), (
        "the recorded pull's real counts"
    )


def _params_of(entry: dict[str, object]) -> dict[str, str] | None:
    """The params as the source passed them, decoded.

    The manifest stores them url-encoded, and the arena's `where` clause is full
    of encoded quotes -- comparing the encoded form against what `fixture_slug`
    hashes produced two different names for one file.
    """
    raw = entry.get("params")
    if not raw:
        return None
    return dict(parse_qsl(str(raw), keep_blank_values=True))
