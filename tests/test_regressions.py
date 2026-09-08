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
    assert "aa_llm: 6 models" in out, out


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
    costs = add_cost_observations(obs, profile, at)

    assert costs["vendor/priced-only"] == pytest.approx(1.0 * 0.002 + 2.0 * 0.0005)
    assert obs.models() == ["vendor/priced-only"]
    injected = obs.get("vendor/priced-only", COST_SOURCE, COST_FIELD)
    assert injected is not None and injected.unit == "usd_per_task"
