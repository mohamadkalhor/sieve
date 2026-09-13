"""AMS-31: three named runs, a schedule per step, and one run at a time.

Nothing here touches the network: the step body is replaced with a stub, so
what is under test is the machinery around it -- the row, the log, the 409, the
next moment a schedule is due, and the scheduler's refusal to fire the instant
the service starts.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from sieve import runs
from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent
TOKENS = "ops:read,profiles:write,apply:s3cret;watcher:read:reader"
AMS = ZoneInfo("Europe/Amsterdam")


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
    )


@pytest.fixture
def store(cfg: Config) -> Store:
    return Store(cfg.db_path)


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every step becomes a sentence. The loop itself is tested elsewhere."""

    def stub(
        config: Config, store: Store, step: str, *, actor: str, config_path: str
    ) -> runs.Outcome:
        print(f"stub ran {step} for {actor}")
        if step == "ship_profiles":
            return runs.Outcome("0 ranked, 0 decided, 0 combos shipped", "a target refused")
        return runs.Outcome(f"{step} did nothing, on purpose")

    monkeypatch.setattr(runs, "execute", stub)


# --------------------------------------------------------------------------- #
# schedules
# --------------------------------------------------------------------------- #


def test_seeded_schedules_are_the_retired_timer(store: Store) -> None:
    rows = {s.step: s for s in runs.schedules(store)}
    assert set(rows) == {"full", "pull_sources", "harvest_connectors", "ship_profiles"}
    assert rows["full"].mode == "daily"
    assert rows["full"].at_time == "04:30"
    assert [rows[s].mode for s in runs.SINGLE_STEPS] == ["off", "off", "off"]


def test_next_fire_daily_is_the_next_local_0430() -> None:
    schedule = runs.Schedule("full", "daily", 0, "04:30", "Europe/Amsterdam", None)
    at = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    upcoming = runs.next_fire(schedule, at)
    assert upcoming is not None
    assert upcoming.astimezone(AMS).strftime("%Y-%m-%d %H:%M") == "2026-09-14 04:30"


def test_next_fire_hourly_is_within_the_hour() -> None:
    schedule = runs.Schedule("full", "hourly", 15, "04:30", "Europe/Amsterdam", None)
    at = datetime(2026, 9, 13, 12, 20, tzinfo=UTC)
    upcoming = runs.next_fire(schedule, at)
    assert upcoming is not None
    assert 0 < (upcoming - at).total_seconds() <= 3600
    assert upcoming.astimezone(AMS).minute == 15


def test_an_off_schedule_never_fires() -> None:
    schedule = runs.Schedule("pull_sources", "off", 0, "04:30", "Europe/Amsterdam", None)
    assert runs.next_fire(schedule) is None
    assert runs.previous_fire(schedule) is None


def test_put_schedule_refuses_nonsense(store: Store) -> None:
    with pytest.raises(ValueError):
        runs.put_schedule(store, "full", mode="sometimes")
    with pytest.raises(ValueError):
        runs.put_schedule(store, "nope", mode="daily")
    with pytest.raises(ValueError):
        runs.put_schedule(store, "full", mode="hourly", at_minute=77)
    with pytest.raises(ValueError):
        runs.put_schedule(store, "full", mode="daily", at_time="half four")

    changed = runs.put_schedule(store, "pull_sources", mode="hourly", at_minute=7)
    assert (changed.mode, changed.at_minute) == ("hourly", 7)


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #


def test_a_run_is_recorded_with_a_summary_and_a_log(cfg: Config, store: Store, quiet: None) -> None:
    runner = runs.Runner(cfg, store, config_path="sieve.toml")
    run, outcome = runner.run_now("pull_sources", "mohamad")

    assert outcome.ok
    row = runs.run_by_id(store, run.id)
    assert row is not None
    assert row.step == "pull_sources"
    assert row.requested_by == "mohamad"
    assert row.ok is True
    assert row.summary and "on purpose" in row.summary
    assert row.finished is not None
    assert "stub ran pull_sources" in runs.read_log(row)


def test_a_failed_step_is_a_failed_run_not_a_dead_service(
    cfg: Config, store: Store, quiet: None
) -> None:
    runner = runs.Runner(cfg, store, config_path="sieve.toml")
    _, outcome = runner.run_now("ship_profiles", "mohamad")
    assert not outcome.ok
    last = runs.last_finished(store)
    assert last is not None and last.ok is False and last.error == "a target refused"


def test_only_one_run_at_a_time(cfg: Config, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    held = threading.Event()
    release = threading.Event()

    def slow(
        config: Config, store: Store, step: str, *, actor: str, config_path: str
    ) -> runs.Outcome:
        held.set()
        release.wait(5)
        return runs.Outcome("slowly, then")

    monkeypatch.setattr(runs, "execute", slow)
    runner = runs.Runner(cfg, store, config_path="sieve.toml")
    first = runner.start("full", "mohamad")
    assert held.wait(5)

    with pytest.raises(runs.RunBusyError) as refused:
        runner.start("pull_sources", "mohamad")
    assert refused.value.run_id == first.id

    release.set()
    for _ in range(100):
        settled = runs.run_by_id(store, first.id)
        if settled is not None and not settled.running:
            break
        time.sleep(0.05)
    assert runs.running_run(store) is None


def test_reap_closes_a_run_a_restart_killed(cfg: Config, store: Store, quiet: None) -> None:
    runner = runs.Runner(cfg, store, config_path="sieve.toml")
    orphan = runner._open("full", "schedule", None)
    assert runs.running_run(store) is not None

    assert runs.reap(store) == 1
    assert runs.running_run(store) is None
    closed = runs.run_by_id(store, orphan.id)
    assert closed is not None and closed.ok is False and "restart" in (closed.error or "")


# --------------------------------------------------------------------------- #
# the scheduler
# --------------------------------------------------------------------------- #


def test_a_fresh_schedule_does_not_fire_at_once(cfg: Config, store: Store, quiet: None) -> None:
    runner = runs.Runner(cfg, store, config_path="sieve.toml")
    scheduler = runs.Scheduler(runner)
    at = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    assert scheduler.tick(at) == []
    seeded = runs.schedule_for(store, "full")
    assert seeded is not None and seeded.last_fired == at


def test_the_scheduler_fires_a_step_whose_moment_has_passed(
    cfg: Config, store: Store, quiet: None
) -> None:
    runner = runs.Runner(cfg, store, config_path="sieve.toml")
    scheduler = runs.Scheduler(runner)
    runs.put_schedule(store, "full", mode="off")
    runs.put_schedule(store, "harvest_connectors", mode="hourly", at_minute=0)

    noon = datetime(2026, 9, 13, 12, 30, tzinfo=UTC)
    assert scheduler.tick(noon) == []  # first sight: only remembered
    later = datetime(2026, 9, 13, 13, 30, tzinfo=UTC)
    assert scheduler.tick(later) == ["harvest_connectors"]

    for _ in range(100):
        if runs.running_run(store) is None:
            break
        time.sleep(0.05)
    last = runs.last_finished(store, "harvest_connectors")
    assert last is not None and last.requested_by == "schedule"


# --------------------------------------------------------------------------- #
# the API
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(cfg: Config) -> TestClient:
    return TestClient(create_app(cfg))


def _finish(client: TestClient, run_id: str) -> dict[str, object]:
    for _ in range(200):
        body = client.get(f"/v1/runs/{run_id}").json()
        if not body["running"]:
            return body  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError("the run never finished")


def test_schedules_endpoints(client: TestClient) -> None:
    listed = client.get("/v1/schedules")
    assert listed.status_code == 200
    rows = {r["step"]: r for r in listed.json()}
    assert rows["full"]["mode"] == "daily" and rows["full"]["next_fire"]
    assert rows["pull_sources"]["next_fire"] is None

    assert client.put("/v1/schedules/full", json={"mode": "hourly"}).status_code == 401

    headers = {"authorization": "Bearer s3cret"}
    changed = client.put(
        "/v1/schedules/full", json={"mode": "hourly", "at_minute": 5}, headers=headers
    )
    assert changed.status_code == 200
    assert changed.json()["mode"] == "hourly"
    assert client.get("/v1/schedules").json()[0]["next_fire"]

    bad = client.put("/v1/schedules/full", json={"mode": "never"}, headers=headers)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_schedule"


def test_post_a_run_then_read_it(client: TestClient, quiet: None) -> None:
    headers = {"authorization": "Bearer s3cret"}
    assert client.post("/v1/runs/pull_sources").status_code == 401

    started = client.post("/v1/runs/pull_sources", headers=headers)
    assert started.status_code == 202
    run_id = started.json()["id"]

    done = _finish(client, run_id)
    assert done["ok"] is True
    assert "on purpose" in str(done["summary"])
    assert client.get(f"/v1/runs/{run_id}/log").text.count("stub ran") == 1
    assert [r["id"] for r in client.get("/v1/runs?limit=5").json()] == [run_id]

    missing = client.post("/v1/runs/not_a_step", headers=headers)
    assert missing.status_code == 404


def test_a_second_run_is_a_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    release = threading.Event()

    def slow(
        config: Config, store: Store, step: str, *, actor: str, config_path: str
    ) -> runs.Outcome:
        release.wait(5)
        return runs.Outcome("eventually")

    monkeypatch.setattr(runs, "execute", slow)
    headers = {"authorization": "Bearer s3cret"}
    first = client.post("/v1/runs/full", headers=headers)
    assert first.status_code == 202

    second = client.post("/v1/runs/pull_sources", headers=headers)
    assert second.status_code == 409
    assert second.json()["running"] == first.json()["id"]

    release.set()
    _finish(client, first.json()["id"])


def test_status_tells_the_truth_about_runs(client: TestClient, quiet: None) -> None:
    before = client.get("/v1/status").json()
    assert before["runs"]["running"] is None
    assert before["runs"]["last"] is None
    assert {s["step"] for s in before["schedules"]} == set(runs.STEPS)
    # the stale `[schedule] pull = "hourly"` reading is gone: this is the store
    assert before["schedule"] == "daily"

    headers = {"authorization": "Bearer s3cret"}
    run_id = client.post("/v1/runs/harvest_connectors", headers=headers).json()["id"]
    _finish(client, run_id)

    after = client.get("/v1/status").json()
    assert after["runs"]["last"]["step"] == "harvest_connectors"
    assert after["runs"]["last"]["ok"] is True
    assert after["runs"]["last_by_step"]["pull_sources"] is None
