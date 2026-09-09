"""Two bugs that only showed up when someone ran the documented commands.

Neither had a failing unit test, because both are about what the *pool* of
judgeable models contains, and every existing fixture hands the scorer a pool
that is already populated. They are pinned here so a future refactor of the
pull gate or the cost injection cannot quietly restore the old behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sieve import cli
from sieve.contracts import ObsTable, Price, Profile, Shape

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path) -> Path:
    """The smallest sieve.toml that can pull: a scratch store, the repo's own
    axes and profiles, one key-gated source, and no inventory to reach for."""
    path = tmp_path / "sieve.toml"
    path.write_text(
        "\n".join(
            [
                "[store]",
                f'path = "{(tmp_path / "sieve.db").as_posix()}"',
                "",
                "[paths]",
                f'axes = "{(ROOT / "data" / "axes").as_posix()}"',
                f'profiles = "{(ROOT / "profiles").as_posix()}"',
                f'aliases = "{(ROOT / "data" / "aliases.yaml").as_posix()}"',
                f'out = "{(tmp_path / "out").as_posix()}"',
                "",
                "[sources.aa_llm]",
                "enabled = true",
                'key_env = "ARTIFICIAL_ANALYSIS_API_KEY"',
                'modalities = ["llm"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# a fixture run must not be gated on a key it will never send
# --------------------------------------------------------------------------- #


def test_fixtures_pull_a_key_gated_source_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`SIEVE_FIXTURES=1` answers from disk, so no key is needed to reach AA.

    The gate used to be `needs_key and not key`, which skipped every
    Artificial Analysis source on any machine without a key -- including CI,
    where the whole point is that the fixtures stand in for the API. The web
    app was then served with zero observations and nothing ranked.
    """
    monkeypatch.delenv("ARTIFICIAL_ANALYSIS_API_KEY", raising=False)
    monkeypatch.setenv("SIEVE_FIXTURES", "1")

    assert cli.main(["--config", str(_config(tmp_path)), "pull", "aa_llm"]) == 0
    out = capsys.readouterr().out
    assert "skipped" not in out, out
    # the recording is trimmed to 60 of the 644 the endpoint publishes
    assert "aa_llm: 60 models" in out, out


# --------------------------------------------------------------------------- #
# cost reaches every priced model, not only the already-observed ones
# --------------------------------------------------------------------------- #


def test_cost_reaches_a_model_that_has_a_price_and_no_benchmark() -> None:
    """A gateway prices far more models than anyone benchmarks.

    `add_cost_observations` used to walk the models that already had an
    observation, which is circular: a priced-but-unbenchmarked model was not in
    the table, so it never got a cost, so it never entered the table. A keyless
    `sieve pull openrouter` -- the first command in the README -- therefore
    ranked nothing at all, and said so as if there were no models.
    """
    from sieve.engine import COST_FIELD, COST_SOURCE, add_cost_observations

    at = datetime(2026, 1, 1, tzinfo=UTC)
    obs = ObsTable(modality="llm")
    obs.prices["vendor/priced-only"] = Price(
        model_id="vendor/priced-only",
        source="openrouter",
        unit="usd_per_1m_tokens",
        input=1.0,
        output=2.0,
        observed_at=at,
    )
    assert obs.models() == []

    profile = Profile(
        name="bulk",
        modality="llm",
        purpose="volume work",
        weights={"cost": 1.0},
        shape=Shape(in_tokens=2000, out_tokens=500),
    )
    costs, from_telemetry = add_cost_observations(obs, profile, at)
    assert from_telemetry == set(), "no telemetry here, so cost is the shape's"

    assert costs["vendor/priced-only"] == pytest.approx(1.0 * 0.002 + 2.0 * 0.0005)
    assert obs.models() == ["vendor/priced-only"]
    injected = obs.get("vendor/priced-only", COST_SOURCE, COST_FIELD)
    assert injected is not None and injected.unit == "usd_per_task"


# --------------------------------------------------------------------------- #
# an effort mode inherits what it can do from the model it is a mode of
# --------------------------------------------------------------------------- #


def test_an_effort_mode_inherits_its_familys_capabilities() -> None:
    """Nothing publishes capabilities per mode, so a mode had none at all.

    Artificial Analysis is the only source that lists the modes and publishes no
    capabilities whatsoever; OpenRouter publishes them and carries only the base
    id. So `gpt-5-6-sol-high` arrived with no tools, no context window and no
    reasoning, and every profile with a `require:` block excluded every mode it
    had -- reported as "excluded by tools", which reads as a fact about the
    model rather than a hole in the catalogue.

    That made PLAN 2.1a unreachable: the modes were split into their own rows
    and then none of them could ever be seated.
    """
    from sieve.contracts import Capability, ModelRef
    from sieve.engine import inherit_family_capabilities

    base = ModelRef(
        id="openai/gpt-6-astra",
        modality="llm",
        name="GPT-6 Astra",
        creator="openai",
        family="openai/gpt-6-astra",
        effort="max",
    )
    high = base.model_copy(update={"id": "openai/gpt-6-astra-high", "effort": "high"})
    low = base.model_copy(update={"id": "openai/gpt-6-astra-low", "effort": "low"})
    stranger = ModelRef(id="other/thing", modality="llm", name="Thing", creator="other")

    caps = {
        "openai/gpt-6-astra": Capability(tools=True, context_window=400_000, reasoning=True),
        # the mode publishes one thing for itself, and it must win
        "openai/gpt-6-astra-low": Capability(reasoning=False),
    }

    out = inherit_family_capabilities(caps, [base, high, low, stranger])

    assert out["openai/gpt-6-astra-high"].tools is True, "the mode can be given tools"
    assert out["openai/gpt-6-astra-high"].context_window == 400_000

    assert out["openai/gpt-6-astra-low"].tools is True, "inherited"
    assert out["openai/gpt-6-astra-low"].reasoning is False, "and its own value wins"

    assert "other/thing" not in out, "a model in no family inherits nothing"
    assert out["openai/gpt-6-astra"] == caps["openai/gpt-6-astra"], "the base is untouched"


def test_a_family_with_no_capabilities_anywhere_inherits_nothing() -> None:
    """Not every family has a base OpenRouter carries. That is not a failure."""
    from sieve.contracts import ModelRef
    from sieve.engine import inherit_family_capabilities

    base = ModelRef(id="v/m", modality="llm", name="M", creator="v", family="v/m", effort="max")
    high = base.model_copy(update={"id": "v/m-high", "effort": "high"})
    assert inherit_family_capabilities({}, [base, high]) == {}


def test_two_sources_that_disagree_about_one_price_are_surfaced(tmp_path: Path) -> None:
    """A price is one vendor charging to run one model, so two sources differ.

    A factor of three is past "different margins" and into "somebody is reading
    a different number" -- which is how the `veo3.1/lite` fold was found, a
    cheaper model's rate sitting on a dearer model's row.

    It is a note, not a failure: both numbers may be true, and the person who
    can tell is the one reading the line.
    """
    from datetime import UTC, datetime

    from sieve.cli import _prices_that_disagree
    from sieve.contracts import ModelRef, Price
    from sieve.store import Store

    db = tmp_path / "sieve.db"
    store = Store(db)
    store.upsert_models(
        [
            ModelRef(id="acme/one", modality="text-to-video", name="One", creator="acme"),
            ModelRef(id="acme/two", modality="text-to-video", name="Two", creator="acme"),
        ]
    )

    def price(model_id: str, source: str, rate: float, tier: str | None = None) -> Price:
        return Price(
            model_id=model_id,
            source=source,
            modality="text-to-video",
            unit="usd_per_second",
            per_unit=rate,
            tier=tier,
            observed_at=datetime.now(UTC),
        )

    store.add_prices(
        [
            # five times apart: reported
            price("acme/one", "fal", 0.03),
            price("acme/one", "deepinfra", 0.15),
            # thirty per cent apart: two vendors, not a bug
            price("acme/two", "fal", 0.10),
            price("acme/two", "deepinfra", 0.13),
        ]
    )

    class _Cfg:
        db_path = db

    notes = _prices_that_disagree(_Cfg())  # type: ignore[arg-type]
    assert len(notes) == 1, notes
    assert "acme/one" in notes[0]
    assert "5.0x" in notes[0]
    assert "fal 0.03" in notes[0] and "deepinfra 0.15" in notes[0]
    assert "acme/two" not in " ".join(notes)


def test_one_sources_own_tiers_are_not_a_disagreement(tmp_path: Path) -> None:
    """A model priced $0.0125 at 480p and $0.04 at 1080p by *one* source is a
    tiered price, not two sources contradicting each other. Reporting it would
    make the note fire on every tiered video model and teach everyone to skip
    the line."""
    from datetime import UTC, datetime

    from sieve.cli import _prices_that_disagree
    from sieve.contracts import ModelRef, Price
    from sieve.store import Store

    db = tmp_path / "sieve.db"
    store = Store(db)
    store.upsert_models(
        [ModelRef(id="acme/tiered", modality="text-to-video", name="T", creator="acme")]
    )
    store.add_prices(
        [
            Price(
                model_id="acme/tiered",
                source="fal",
                modality="text-to-video",
                unit="usd_per_second",
                per_unit=rate,
                tier=tier,
                observed_at=datetime.now(UTC),
            )
            for rate, tier in ((0.0125, "480p"), (0.02, "768p"), (0.04, "1080p"))
        ]
    )

    class _Cfg:
        db_path = db

    assert _prices_that_disagree(_Cfg()) == []  # type: ignore[arg-type]
