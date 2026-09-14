"""B1 and B2: what a request is allowed to cost, and how a failure arrives.

Every test here stands for a measurement taken read-only against the live
store on 2026-09-14, where 2,095,878 observations (1,046,838 of them llm) made
`obs_table("llm")` a 34-second, 786 MB call that preview, apply, the
leaderboard and the hourly run each made on a request thread.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sieve import runs
from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import Modality, ModelRef, Observation, ObsTable, Price
from sieve.engine import RankingBusyError, ranking_slot
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent
AT = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("SIEVE_TOKENS", "ops:read,profiles:write,apply:s3cret")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
    )


@pytest.fixture
def store(cfg: Config) -> Store:
    """Two models and a history: three measurements of one (model, source,
    field), so "the latest of each" is a claim that can be wrong."""
    store = Store(cfg.db_path)
    store.upsert_models(
        [
            ModelRef(id="a/one", modality="llm", name="One", creator="a"),
            ModelRef(id="b/two", modality="llm", name="Two", creator="b"),
            ModelRef(id="c/three", modality="music", name="Three", creator="c"),
        ]
    )
    store.add_observations(
        [
            Observation(
                model_id="a/one",
                modality="llm",
                source="aa",
                field="intelligence",
                value=value,
                unit="index_0_100",
                observed_at=AT - timedelta(days=day),
                pulled_at=AT - timedelta(days=day),
            )
            for day, value in ((2, 40.0), (1, 50.0), (0, 60.0))
        ]
        + [
            Observation(
                model_id="b/two",
                modality="llm",
                source="aa",
                field="intelligence",
                value=30.0,
                unit="index_0_100",
                observed_at=AT,
                pulled_at=AT,
            )
        ]
    )
    return store


# --------------------------------------------------------------------------- #
# B1a: the observation table
# --------------------------------------------------------------------------- #


def test_obs_table_is_the_latest_of_each_not_the_history(store: Store) -> None:
    table = store.obs_table("llm")
    assert sorted(table.latest) == ["a/one", "b/two"]
    found = table.get("a/one", "aa", "intelligence")
    assert found is not None
    assert found.value == 60.0  # the newest of the three, not the first or the last read
    assert found.observed_at == AT


def test_a_view_is_built_once_and_shared(store: Store) -> None:
    first = store.cache.view("llm")
    second = store.cache.view("llm")
    assert first is second
    assert (store.cache.hits, store.cache.misses) == (1, 1)


def test_a_new_snapshot_is_a_new_view(store: Store) -> None:
    first = store.cache.view("llm")
    store.new_snapshot()
    second = store.cache.view("llm")
    assert second is not first
    assert store.cache.misses == 2


def test_a_harvest_invalidates_what_it_changed(store: Store) -> None:
    first = store.cache.view("llm")
    store.invalidate_views()
    assert len(store.cache) == 0
    assert store.cache.view("llm") is not first


def test_the_cache_is_bounded(store: Store) -> None:
    store.cache.max_entries = 2
    for snapshot in ("s1", "s2", "s3"):
        store.cache.view("llm", snapshot)
    assert len(store.cache) == 2
    assert [key[0] for key in store.cache.keys] == ["s2", "s3"]  # the oldest is evicted


def test_concurrent_misses_build_the_view_once(store: Store) -> None:
    builds = 0
    real = store.obs_table

    def slow(modality: Modality) -> ObsTable:
        nonlocal builds
        builds += 1
        time.sleep(0.2)  # long enough that every thread is inside view()
        return real(modality)

    store.obs_table = slow  # type: ignore[method-assign]
    threads = [threading.Thread(target=lambda: store.cache.view("llm")) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert builds == 1
    assert store.cache.misses == 1


def test_the_working_copy_does_not_reach_the_shared_view(store: Store) -> None:
    view = store.cache.view("llm")
    working = view.working()
    working.add(
        Observation(
            model_id="a/one",
            modality="llm",
            source="sieve",
            field="cost_per_task",
            value=0.5,
            unit="usd_per_task",
            observed_at=AT,
            pulled_at=AT,
        )
    )
    assert working.get("a/one", "sieve", "cost_per_task") is not None
    assert view.obs.get("a/one", "sieve", "cost_per_task") is None
    assert store.cache.view("llm").obs.get("a/one", "sieve", "cost_per_task") is None


# --------------------------------------------------------------------------- #
# B1b: the model routes
# --------------------------------------------------------------------------- #


def test_a_model_is_read_off_its_key_not_found_by_walking(store: Store) -> None:
    found = store.model("b/two")
    assert found is not None and found.name == "Two"
    assert store.model("a/one", "music") is None
    assert store.model("nobody/nothing") is None


def test_the_model_route_does_not_walk_the_catalogue(cfg: Config, store: Store) -> None:
    """The store the app opens is the one the fixture filled: same file."""
    with TestClient(create_app(cfg)) as client:
        serving: Store = client.app.state.store  # type: ignore[attr-defined]

        def refuse(modality: Modality | None = None) -> list[ModelRef]:
            raise AssertionError("the id lookup listed the whole catalogue")

        serving.models = refuse  # type: ignore[method-assign]
        answered = client.get("/v1/models/b/two")
        assert answered.status_code == 200
        assert answered.json()["id"] == "b/two"
        assert client.get("/v1/models/nobody/nothing").status_code == 404


def test_the_list_prices_the_page_and_not_the_modality(cfg: Config, store: Store) -> None:
    asked: list[object] = []
    with TestClient(create_app(cfg)) as client:
        serving: Store = client.app.state.store  # type: ignore[attr-defined]
        real = serving.latest_prices

        def spy(modality: Modality, only: Collection[str] | None = None) -> dict[str, Price]:
            asked.append(only)
            return real(modality, only)

        serving.latest_prices = spy  # type: ignore[method-assign]
        body = client.get("/v1/models?modality=llm&limit=1").json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] == "1"
    assert asked == [[body["items"][0]["id"]]]  # the page was priced, not the modality


# --------------------------------------------------------------------------- #
# B1c: the ranking slots
# --------------------------------------------------------------------------- #


def test_a_ranking_waits_for_a_slot_and_then_says_so() -> None:
    with ranking_slot(), ranking_slot(), pytest.raises(RankingBusyError), ranking_slot(0.05):
        pass  # pragma: no cover - the third slot is never granted


def test_a_busy_ranking_is_a_503_with_a_retry_after(cfg: Config) -> None:
    app = create_app(cfg)

    @app.get("/busy")
    def busy() -> None:
        raise RankingBusyError(60.0)

    with TestClient(app) as client:
        answered = client.get("/busy")
    assert answered.status_code == 503
    assert answered.json()["error"]["code"] == "ranking_busy"
    assert answered.headers["retry-after"] == "10"


# --------------------------------------------------------------------------- #
# B2: reads that write, and failures that arrive as text
# --------------------------------------------------------------------------- #


def test_status_does_not_write(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(cfg)
    with TestClient(app) as client:
        seeded = 0
        real = runs.ensure_schedules

        def counted(store: Store) -> None:
            nonlocal seeded
            seeded += 1
            real(store)

        monkeypatch.setattr(runs, "ensure_schedules", counted)
        for _ in range(3):
            body = client.get("/v1/status").json()
            assert {row["step"] for row in body["schedules"]} == set(runs.STEPS)
        assert seeded == 0  # startup seeded them; polling does not


def test_a_fresh_store_still_reads_four_schedules(store: Store) -> None:
    assert store.db.execute("SELECT COUNT(*) c FROM schedules").fetchone()["c"] == 0
    assert {row.step for row in runs.schedules(store)} == set(runs.STEPS)


def test_an_unhandled_exception_is_the_envelope_and_a_500(cfg: Config) -> None:
    app = create_app(cfg)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("the kind of thing nobody wrote a handler for")

    with TestClient(app, raise_server_exceptions=False) as client:
        answered = client.get("/boom")
    assert answered.status_code == 500
    assert answered.headers["content-type"].startswith("application/json")
    assert answered.json()["error"]["code"] == "internal_error"
    assert "nobody wrote a handler" not in answered.text  # the log gets the detail


def test_a_locked_database_is_a_503_and_not_a_500(cfg: Config) -> None:
    app = create_app(cfg)

    @app.get("/locked")
    def locked() -> None:
        raise sqlite3.OperationalError("database is locked")

    with TestClient(app) as client:
        answered = client.get("/locked")
    assert answered.status_code == 503
    assert answered.json()["error"]["code"] == "database_locked"
    assert answered.headers["retry-after"] == "2"


def test_another_sqlite_fault_is_still_a_500_in_the_envelope(cfg: Config) -> None:
    app = create_app(cfg)

    @app.get("/broken")
    def broken() -> None:
        raise sqlite3.OperationalError("no such table: nothing")

    with TestClient(app) as client:
        answered = client.get("/broken")
    assert answered.status_code == 500
    assert answered.json()["error"]["code"] == "store_error"


def test_every_connection_waits_for_a_writer_and_uses_wal(store: Store) -> None:
    row = store.db.execute("PRAGMA journal_mode").fetchone()
    assert row[0].lower() == "wal"
    assert store.db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
