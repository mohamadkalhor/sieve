"""Phase 2 part 5 — the loop runs itself.

The acceptance line is about restraint, not capability: a profile with
`auto_apply: true` ships a change and logs why; one with `false` records the
same decision and writes nothing. A scheduler that ships everything it computes
is not a scheduler anyone will leave switched on.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sieve.cli import main
from sieve.contracts import RateLimit
from sieve.http import MAX_RESET_WAIT, Http
from sieve.store import Store

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"

REACHABLE = ["openai/gpt-5-6-sol-non-reasoning", "alibaba/qwen3-8-flash-next"]


def _workspace(tmp_path: Path, auto: dict[str, bool]) -> Path:
    """A scratch install whose named profiles opt in (or do not) to auto_apply."""
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles" / "llm", profiles / "llm")

    for name, opted_in in auto.items():
        path = profiles / "llm" / f"{name}.yaml"
        lines = path.read_text(encoding="utf-8").split("\n")
        out: list[str] = []
        for line in lines:
            out.append(line)
            if line.strip() == "policy:":
                out.append(f"  auto_apply: {'true' if opted_in else 'false'}")
        path.write_text("\n".join(out), encoding="utf-8")

    config = tmp_path / "sieve.toml"
    config.write_text(
        "\n".join(
            [
                "[store]",
                f'path = "{(tmp_path / "sieve.db").as_posix()}"',
                "",
                "[paths]",
                f'axes = "{(REPO / "data" / "axes").as_posix()}"',
                f'profiles = "{profiles.as_posix()}"',
                f'aliases = "{(REPO / "data" / "aliases.yaml").as_posix()}"',
                f'out = "{(tmp_path / "out").as_posix()}"',
                "",
                "[sources.aa_llm]",
                "enabled = true",
                'key_env = "ARTIFICIAL_ANALYSIS_API_KEY"',
                'modalities = ["llm"]',
                "",
                "[inventories.gw]",
                'kind = "list"',
                f"models = {REACHABLE!r}".replace("'", '"'),
                "",
                "[targets.out]",
                'kind = "file"',
                f'dir = "{(tmp_path / "out").as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SIEVE_FIXTURES", "1")
    monkeypatch.delenv("ARTIFICIAL_ANALYSIS_API_KEY", raising=False)
    config = _workspace(tmp_path, {"cheap_bulk": True, "quick_chat": False})
    assert main(["--config", str(config), "pull", "aa_llm", "gw"]) == 0
    return config


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #


def test_only_a_profile_that_opted_in_ships(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance line, both halves, in one run."""
    assert main(["--config", str(workspace), "run", "--no-pull"]) == 0
    out = capsys.readouterr().out

    assert (tmp_path / "out" / "cheap_bulk.json").is_file(), "the opted-in profile shipped"
    assert not (tmp_path / "out" / "quick_chat.json").exists(), "the other one did not"
    assert "held (no auto_apply)" in out and "quick_chat" in out

    # ...and it was decided all the same, which is the point
    decisions = Store(tmp_path / "sieve.db").decisions()
    decided = {d.profile for d in decisions}
    assert {"cheap_bulk", "quick_chat"} <= decided


def test_every_profile_writes_a_decision_every_run(workspace: Path, tmp_path: Path) -> None:
    """A run that changed nothing must be as visible as one that changed everything."""
    store = Store(tmp_path / "sieve.db")

    main(["--config", str(workspace), "run", "--no-pull", "--profile", "cheap_bulk"])
    first = len(store.decisions(profile="cheap_bulk"))

    main(["--config", str(workspace), "run", "--no-pull", "--profile", "cheap_bulk"])
    second = len(store.decisions(profile="cheap_bulk"))

    assert second > first, "the second run held, and a hold is still a decision row"
    # the newest row is the `apply` that followed; the decision itself is the
    # newest row of a kind the policy produces
    rows = store.decisions(profile="cheap_bulk")
    latest = next(d for d in rows if d.kind in {"hold", "switch", "suspend"})
    assert latest.actor == "schedule", "a scheduled run is recorded as one"
    assert any(d.kind == "hold" for d in rows), "the second run held, and said so"


def test_a_dry_run_decides_and_writes_nothing(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(workspace), "run", "--no-pull", "--dry-run"]) == 0
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.json"))
    assert "would write" in capsys.readouterr().out


def test_a_failed_source_does_not_stop_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stale data still answers the question. Silence does not.

    The store is seeded first, then the fixture directory is pointed somewhere
    empty so every source fails on the next run -- exactly what a bad morning at
    the benchmark site looks like.
    """
    monkeypatch.setenv("SIEVE_FIXTURES", "1")
    config = _workspace(tmp_path, {"cheap_bulk": True})
    assert main(["--config", str(config), "pull", "aa_llm", "gw"]) == 0

    monkeypatch.chdir(tmp_path)  # no tests/fixtures here, so the player finds nothing
    code = main(["--config", str(config), "run"])

    out = capsys.readouterr().out
    assert code != 0, "a failed source has to show up as a failed unit"
    assert "carrying on with what is stored" in out
    assert "ranked" in out, "and the run still ranked every profile"
    assert (tmp_path / "out" / "cheap_bulk.json").is_file(), "and still shipped"


# --------------------------------------------------------------------------- #
# the rate limit
# --------------------------------------------------------------------------- #


def test_a_retry_waits_for_the_published_reset() -> None:
    """Three fast retries before the reset are three more refusals, charged."""
    http = Http(retries=2, backoff=0.5)
    http._rate = RateLimit(
        limit=1000, remaining=0, reset_at=datetime.now(UTC) + timedelta(seconds=30)
    )
    assert 25 < http._wait_for(0) <= 31, "wait for the reset, not the backoff"


def test_the_wait_is_capped_so_a_timer_is_never_held_open() -> None:
    """A reset an hour away should fail the run; the next timer picks it up."""
    http = Http(retries=2, backoff=0.5)
    http._rate = RateLimit(limit=1000, remaining=0, reset_at=datetime.now(UTC) + timedelta(hours=1))
    assert http._wait_for(0) == MAX_RESET_WAIT


def test_without_a_published_reset_it_falls_back_to_backoff() -> None:
    http = Http(retries=2, backoff=2.0)
    assert http._wait_for(2) == 4.0

    http._rate = RateLimit(reset_at=datetime.now(UTC) - timedelta(minutes=5))
    assert http._wait_for(1) == 2.0, "a reset in the past is not a wait"


# --------------------------------------------------------------------------- #
# the units
# --------------------------------------------------------------------------- #


def test_the_timer_calls_one_command_that_cannot_be_half_skipped() -> None:
    """Two ExecStart lines meant a failed pull skipped the re-rank entirely."""
    service = (REPO / "deploy" / "sieve-run.service").read_text(encoding="utf-8")
    starts = [line for line in service.splitlines() if line.startswith("ExecStart=")]
    assert len(starts) == 1, f"one command, not a chain systemd stops halfway: {starts}"
    assert starts[0].endswith("sieve run")

    timer = (REPO / "deploy" / "sieve-run.timer").read_text(encoding="utf-8")
    assert "OnCalendar=hourly" in timer
    assert "Persistent=true" in timer, "a machine asleep at the hour still runs once"
    assert "RandomizedDelaySec" in timer, "the AA limit is per key, not per host"


def test_the_rate_limit_arithmetic_is_written_down() -> None:
    """So nobody has to re-derive it before changing the schedule."""
    doc = (REPO / "docs" / "the-loop.md").read_text(encoding="utf-8")
    # the doc is prose and wraps, so compare on the words rather than the layout
    flat = " ".join(doc.split())
    assert "1,000 requests a day" in flat
    assert "144" in flat, "six requests an hour, spelled out"
    assert "per key, not per host" in flat
