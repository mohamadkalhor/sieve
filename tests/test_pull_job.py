"""The pull, as a job -- card O9b, step 2.

`POST /v1/sources/{name}/pull` used to run the whole pull inside the request: a
source whose upstream takes a minute held the connection open for a minute. With
§3.5's kit on the route queues a `pull` job and answers 202, and the work happens
on a worker of the pool -- the same `_pull_source`, the same store lock, the same
decision row. These tests are that claim: with the kit off the answer is exactly
what it was, with it on the job really pulls and the row it wrote is the snapshot
it named, a replayed key queues one job, a second attempt under one job id writes
one snapshot, and a store somebody else holds fails the job by name.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from sieve.api import auth, v1_jobs
from sieve.api.app import create_app
from sieve.config import Config, StoreConfig
from sieve.contracts import ModelRef, Observation, PullResult, SourceConfig
from sieve.storelock import lock_path
from tests.test_aio import GateStub, all_routes
from tests.test_store_lock import Holder

REPO = Path(__file__).resolve().parents[1]

#: The box's token: `read` to poll a job, `apply` to pull. `name` is the token's
#: own name and the name the pull's decision row is filed under -- so a job's
#: audit line and the route's own pull are the same line.
NAME = "boss"
SECRET = "fixture-boss"

#: The bearer, and §3.4's key: the write routes demand one, and a job-creating
#: call is a write.
AUTH = {"Authorization": f"Bearer {SECRET}"}

SOURCE = "manual"
AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def keyed(key: str) -> dict[str, str]:
    """The headers of a pull: the token, and the replay key."""
    return {**AUTH, "Idempotency-Key": key}


@pytest.fixture(autouse=True)
def _gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[GateStub]:
    """A gate that is always reachable and always says `owner`.

    No test may reach a gate running on this box: the kit's `GateLookup` holds an
    `httpx.Client` and sieve's own reader calls `httpx.get`, and both are stood
    in for here.
    """
    auth._gate_cache.clear()
    fake = GateStub(
        user_id=17, email="ada@example.test", name="Ada", role="owner", status="active",
        app="sieve",
    )
    monkeypatch.setattr(httpx, "get", fake)
    monkeypatch.setattr(httpx, "Client", fake.client)
    yield fake
    auth._gate_cache.clear()


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """A box of its own, with one source that is on and answers without a socket."""
    monkeypatch.setenv("SIEVE_TOKENS", f"{NAME}:read,profiles:write,apply:{SECRET}")
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        sources={SOURCE: SourceConfig(name=SOURCE, enabled=True)},
    )


@pytest.fixture
def off(box: Config, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The box as it runs today: no kit, so no jobs."""
    monkeypatch.delenv("AGENT_V1", raising=False)
    with TestClient(create_app(box)) as client:
        yield client


@pytest.fixture
def on(box: Config, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The box with §3.5's kit on: the route queues, the pool pulls."""
    monkeypatch.setenv("AGENT_V1", "1")
    with TestClient(create_app(box)) as client:
        yield client


def stand_in(monkeypatch: pytest.MonkeyPatch, rows: int = 0) -> Mock:
    """The source's plugin, stood in for: no network, `rows` observations back."""
    models = [ModelRef(id="maker/one", modality="llm", name="One", creator="maker")]
    observations = [
        Observation(
            model_id="maker/one",
            modality="llm",
            source=SOURCE,
            field="intelligence",
            value=float(50 + i),
            unit="index_0_100",
            observed_at=AT,
            pulled_at=AT,
        )
        for i in range(rows)
    ]
    pull = Mock(return_value=PullResult(source=SOURCE, models=models, observations=observations))
    monkeypatch.setattr(
        "sieve.plugins.load", Mock(return_value=SimpleNamespace(pull=pull))
    )
    return pull


def settle(client: TestClient, job_id: str, seconds: float = 20.0) -> dict[str, Any]:
    """Poll the job until it is finished for good -- the client's own move."""
    deadline = time.monotonic() + seconds
    row: dict[str, Any] = {}
    while time.monotonic() < deadline:
        answer = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        assert answer.status_code == 200, answer.text
        row = answer.json()["job"]
        if row["status"] in {"succeeded", "failed", "cancelled"}:
            return row
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never settled: {row}")


def snapshots(store: Any) -> list[tuple[str, int]]:
    """Every snapshot row: its id and how many rows it says it read."""
    return [
        (str(row["id"]), int(row["source_rows"]))
        for row in store.db.execute("SELECT id, source_rows FROM snapshots").fetchall()
    ]


# --------------------------------------------------------------------------- #
# with the kit off: the answer is what it was
# --------------------------------------------------------------------------- #


def test_off_the_pull_answers_as_it_always_did(
    off: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No kit, no job: the pull runs in the request and the body is the old one."""
    stand_in(monkeypatch, rows=1)
    answer = off.post(f"/v1/sources/{SOURCE}/pull", headers=AUTH)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert set(body) == {"job", "source", "added", "warnings"}
    assert body["source"] == SOURCE
    assert body["added"] == 1
    assert len(body["job"]) == 12
    store = off.app.state.store
    assert len(snapshots(store)) == 1


def test_off_nothing_of_the_kit_is_mounted(off: TestClient) -> None:
    """The whole surface stays off the app: no `/v1/jobs`, and no job store."""
    paths = [route.path for route in all_routes(off.app)]
    assert [path for path in paths if path.startswith("/v1/jobs")] == []
    assert off.get("/v1/jobs/whatever", headers=AUTH).status_code == 404
    assert getattr(off.app.state, "aio_jobs", None) is None


# --------------------------------------------------------------------------- #
# with the kit on: the pull is a job
# --------------------------------------------------------------------------- #


def test_on_the_pull_is_a_job(on: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """202 and a row to poll; the row wrote the snapshot it named, and the audit."""
    stand_in(monkeypatch, rows=1)
    answer = on.post(f"/v1/sources/{SOURCE}/pull", headers=keyed("pull-1"))
    assert answer.status_code == 202, answer.text
    submitted = answer.json()["job"]
    assert set(submitted) == {"id", "status", "poll"}
    assert submitted["status"] == "queued"
    assert submitted["poll"] == f"/v1/jobs/{submitted['id']}"

    row = settle(on, submitted["id"])
    assert row["status"] == "succeeded", row
    assert row["kind"] == v1_jobs.PULL
    result = row["result"]
    # the shape the synchronous route answered with, plus the snapshot's name
    assert result["source"] == SOURCE
    assert result["added"] == 1
    assert result["warnings"] == []
    assert result["snapshot"] == v1_jobs.snapshot_id(SOURCE, submitted["id"])
    store = on.app.state.store
    assert snapshots(store) == [(result["snapshot"], 1)]
    # the observation is filed under that snapshot, and the decision names the
    # key that submitted the job -- the actor the route's own pull would file
    filed = store.db.execute("SELECT snapshot FROM observations").fetchall()
    assert [str(row["snapshot"]) for row in filed] == [result["snapshot"]]
    actor = store.db.execute("SELECT actor FROM decisions WHERE kind = 'pull'").fetchall()
    assert [str(row["actor"]) for row in actor] == [NAME]


def test_on_a_replayed_key_queues_one_job(
    on: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.4: the second call with the same key is the first job, not a second."""
    stand_in(monkeypatch, rows=1)
    first = on.post(f"/v1/sources/{SOURCE}/pull", headers=keyed("pull-again"))
    second = on.post(f"/v1/sources/{SOURCE}/pull", headers=keyed("pull-again"))
    assert first.status_code == second.status_code == 202, second.text
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert len(settle(on, first.json()["job"]["id"])["result"]) > 0
    store = on.app.state.store
    assert len(snapshots(store)) == 1


def test_on_a_second_attempt_writes_one_snapshot(
    on: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-queued attempt runs the same job id, so it writes the same snapshot.

    §3.5 re-queues an interrupted job, and the row it wrote the first time is
    still there: an id minted per attempt would leave two snapshots of one pull,
    and `latest_snapshot` would have to pick between them.
    """
    stand_in(monkeypatch, rows=1)
    store = on.app.state.store
    handler = on.app.state.aio_jobs.kind(v1_jobs.PULL).handler
    ctx = SimpleNamespace(
        job_id="job-retried", input={"source": SOURCE, "force": True}, failure=None
    )
    first = handler(ctx)
    second = handler(ctx)
    assert first["snapshot"] == second["snapshot"] == v1_jobs.snapshot_id(SOURCE, "job-retried")
    assert snapshots(store) == [(first["snapshot"], 1)]


def test_on_a_busy_store_fails_the_job_by_name(
    on: TestClient, box: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store another process holds is `store_busy` on the row, not a retry.

    §3.7's lock is taken for the pull's write. A worker that waited for it would
    hold its slot for the whole wait, so the job ends failed and says why -- and
    the code has to survive the handler: the store's own `_finish` would write
    `JobFailed` where the client reads a reason.
    """
    stand_in(monkeypatch)
    monkeypatch.setenv("SIEVE_STORE_LOCK_WAIT", "0")
    with Holder(lock_path(box.db_path), seconds=20.0):
        answer = on.post(f"/v1/sources/{SOURCE}/pull", headers=keyed("pull-busy"))
        assert answer.status_code == 202, answer.text
        row = settle(on, answer.json()["job"]["id"])
    assert row["status"] == "failed", row
    assert row["error"]["code"] == "store_busy"
    assert str(row["error"]["message"]).strip()
    assert snapshots(on.app.state.store) == []


def test_on_a_source_that_is_off_is_refused_before_the_job(
    on: TestClient, box: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route's own read stays in the request: a refusal is not a job.

    A caller asking for a source that does not exist, or one that is off, is
    told so now -- a job that fails a moment later is a worse answer, and it
    costs a row.
    """
    stand_in(monkeypatch)
    box.sources[SOURCE].enabled = False
    answer = on.post(f"/v1/sources/{SOURCE}/pull", headers=keyed("pull-off"))
    assert answer.status_code == 409, answer.text
    assert answer.json()["error"]["code"] == "source_disabled"
    missing = on.post("/v1/sources/nope/pull", headers=keyed("pull-missing"))
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "not_found"
