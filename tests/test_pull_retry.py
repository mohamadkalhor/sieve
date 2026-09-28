"""A retried pull job writes its observations once -- card O9b, step 5.

A pull job's snapshot id is derived from (source, job id), so a second attempt
lands on the snapshot the first one wrote. The sources stamp `observed_at` with
the time of the pull, so the second attempt's rows are not the first attempt's
rows under the table's natural key; without care they would sit beside them,
under one snapshot, twice over.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sieve.contracts import Observation, Price
from sieve.store import Store
from tests.test_pull_job import box, on  # noqa: F401  (fixtures pytest finds by name)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _obs(at: datetime, value: float = 50.0, field: str = "intelligence") -> Observation:
    return Observation(
        model_id="maker/one",
        modality="llm",
        source="aa",
        field=field,
        value=value,
        unit="index_0_100",
        observed_at=at,
        pulled_at=at,
    )


def _count(store: Store, snapshot: str) -> int:
    row = store.db.execute(
        "SELECT COUNT(*) AS c FROM observations WHERE snapshot = ?", (snapshot,)
    ).fetchone()
    return int(row["c"])


def test_a_second_attempt_does_not_double_the_snapshot(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    sid = store.new_snapshot(source_rows=2, sid="job-snap")
    store.add_observations([_obs(T0), _obs(T0, 7.0, "speed")], snapshot=sid, replace=True)
    later = T0 + timedelta(minutes=3)
    store.add_observations([_obs(later, 51.0), _obs(later, 7.0, "speed")], snapshot=sid, replace=True)
    assert _count(store, sid) == 2
    kept = {o.field: o.value for o in store.observations_for("maker/one")}
    assert kept == {"intelligence": 51.0, "speed": 7.0}


def test_replacing_one_snapshot_leaves_the_others_alone(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    other = store.new_snapshot(source_rows=1)
    store.add_observations([_obs(T0)], snapshot=other)
    sid = store.new_snapshot(source_rows=1, sid="job-snap")
    store.add_observations([_obs(T0 + timedelta(hours=1), 52.0)], snapshot=sid, replace=True)
    store.add_observations([_obs(T0 + timedelta(hours=2), 53.0)], snapshot=sid, replace=True)
    assert _count(store, other) == 1
    assert _count(store, sid) == 1


def test_without_replace_the_append_only_rule_is_unchanged(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    sid = store.new_snapshot(source_rows=1)
    store.add_observations([_obs(T0)], snapshot=sid)
    store.add_observations([_obs(T0 + timedelta(minutes=3), 51.0)], snapshot=sid)
    assert _count(store, sid) == 2


def test_a_retried_price_is_the_latest_one_not_a_second_price(tmp_path: Path) -> None:
    """Prices carry no snapshot: a retry's rows are history, and the latest wins."""
    store = Store(tmp_path / "s.db")
    from sieve.contracts import ModelRef

    store.upsert_models([ModelRef(id="maker/one", modality="llm", name="One", creator="maker")])

    def price(at: datetime, out: float) -> Price:
        return Price(
            model_id="maker/one", source="aa", unit="usd_per_1m_tokens", input=1.0, output=out, observed_at=at
        )

    store.add_prices([price(T0, 2.0)])
    store.add_prices([price(T0 + timedelta(minutes=3), 2.0)])
    assert store.latest_prices("llm")["maker/one"].output == 2.0


def test_a_retried_pull_job_holds_its_rows_once(on, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The job's own path: the handler run twice, the source stamping a new time."""
    from types import SimpleNamespace
    from unittest.mock import Mock

    from sieve.api import v1_jobs
    from sieve.contracts import ModelRef, PullResult
    from tests.test_pull_job import SOURCE

    clock = iter(T0 + timedelta(minutes=n) for n in range(10))

    def pull(*_a, **_k):  # type: ignore[no-untyped-def]
        at = next(clock)
        return PullResult(
            source=SOURCE,
            models=[ModelRef(id="maker/one", modality="llm", name="One", creator="maker")],
            observations=[_obs(at)],
        )

    monkeypatch.setattr(
        "sieve.plugins.load", Mock(return_value=SimpleNamespace(pull=pull))
    )
    handler = on.app.state.aio_jobs.kind(v1_jobs.PULL).handler
    ctx = SimpleNamespace(job_id="job-x", input={"source": SOURCE, "force": True}, failure=None)
    first = handler(ctx)
    handler(ctx)
    assert _count(on.app.state.store, first["snapshot"]) == 1
