"""Review-fix tests for sieve's /v1 surface (FIX-sieve: F1 F2 F3 F4 F5 F6 F15 F22)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sieve.api import aio, auth
from sieve.config import Config
from sieve.store import Store

from tests.test_aio import (  # noqa: F401  (fixtures)
    ADA,
    BOX,
    FEED,
    OWNER_EMAIL,
    GateStub,
    _gate,
    auth_header,
    box,
    forget_gate,
    off,
    on,
    secret_for,
    seats,
)

KEYS_TOKEN = "mk:read,keys:k3ys"
MINT = auth_header("k3ys")


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch, on: TestClient) -> TestClient:
    from tests.test_aio import TOKENS

    monkeypatch.setenv("SIEVE_TOKENS", TOKENS + ";" + KEYS_TOKEN)
    return on


# -- F1 ------------------------------------------------------------------ #


def test_f1_write_key_cannot_mint_beyond_its_own_scopes(on: TestClient, seats: Store) -> None:
    s, _ = secret_for(seats, OWNER_EMAIL, {"profiles:write", "keys"})
    r = on.post(
        "/v1/tokens",
        json={"name": "esc", "scopes": ["admin", "apply", "read"]},
        headers=auth_header(s),
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "scope_refused"


def test_f1_minting_needs_the_keys_scope_when_on(on: TestClient, seats: Store) -> None:
    s, _ = secret_for(seats, OWNER_EMAIL, {"profiles:write"})
    r = on.post("/v1/tokens", json={"name": "x", "scopes": ["profiles:write"]}, headers=auth_header(s))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_allowed"
    assert on.get("/v1/tokens", headers=auth_header(s)).status_code == 403


def test_f1_a_key_may_mint_a_subset_of_what_it_holds(keyed: TestClient) -> None:
    r = keyed.post("/v1/tokens", json={"name": "ok", "scopes": ["read"]}, headers=MINT)
    assert r.status_code == 201, r.text
    assert keyed.get("/v1/tokens", headers=MINT).status_code == 200


def test_f1_a_session_owner_still_mints(on: TestClient) -> None:
    r = on.post(
        "/v1/tokens",
        json={"name": "web", "scopes": ["read", "profiles:write"]},
        headers={"Cookie": "gate_session=whatever"},
    )
    assert r.status_code == 201, r.text


# -- F2 ------------------------------------------------------------------ #


def test_f2_the_mint_route_never_stores_the_secret(keyed: TestClient, box: Config) -> None:
    h = {**MINT, "Idempotency-Key": "mint-1"}
    a = keyed.post("/v1/tokens", json={"name": "one", "scopes": ["read"]}, headers=h)
    assert a.status_code == 201, a.text
    sec = a.json()["secret"]
    db = sqlite3.connect(str(box.db_path).replace("sieve.db", "aio.db"))
    rows = db.execute("select response from aio_idem").fetchall()
    assert not any(sec in str(r[0]) for r in rows)
    b = keyed.post("/v1/tokens", json={"name": "two", "scopes": ["read"]}, headers=h)
    assert b.status_code == 201, b.text
    assert b.json()["secret"] != sec
    assert b.headers.get("idempotent-replay") is None


# -- F3 ------------------------------------------------------------------ #


def test_f3_a_telemetry_only_key_cannot_read(on: TestClient) -> None:
    r = on.get("/v1/profiles", headers=FEED)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "not_allowed"


def test_f3_a_read_key_still_reads(on: TestClient, seats: Store) -> None:
    s, _ = secret_for(seats, ADA, {"read"})
    assert on.get("/v1/profiles", headers=auth_header(s)).status_code == 200


# -- F4 ------------------------------------------------------------------ #


def test_f4_a_bad_bearer_at_the_edge_is_the_kits_bad_key(on: TestClient) -> None:
    edge = {"X-Gate-Edge": "1", "CF-Connecting-IP": "9.9.9.9"}
    r = on.get("/v1/profiles", headers={**edge, "Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "bad_key"


def test_f4_off_the_edge_answer_is_unchanged(off: TestClient) -> None:
    edge = {"X-Gate-Edge": "1", "CF-Connecting-IP": "9.9.9.9"}
    r = off.get("/v1/profiles", headers={**edge, "Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


# -- F5 ------------------------------------------------------------------ #


def test_f5_a_write_key_cannot_start_a_run(
    on: TestClient, seats: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sieve.runs as R

    called: list[str] = []
    monkeypatch.setattr(
        R.Runner,
        "start",
        lambda self, step, who, run_id=None: called.append(step)
        or SimpleNamespace(json=lambda: {"id": "stub"}),
    )
    s, _ = secret_for(seats, ADA, {"profiles:write"})
    r = on.post("/v1/runs/ship_profiles", json={}, headers={**auth_header(s), "Idempotency-Key": "r1"})
    assert r.status_code == 403, r.text
    assert called == []
    r = on.put("/v1/schedules/full", json={"mode": "off"}, headers={**auth_header(s), "Idempotency-Key": "r2"})
    assert r.status_code == 403, r.text


def test_f5_an_apply_key_still_starts_a_run(
    on: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sieve.runs as R

    monkeypatch.setattr(
        R.Runner, "start", lambda self, step, who, run_id=None: SimpleNamespace(json=lambda: {"id": "stub"})
    )
    r = on.post("/v1/runs/ship_profiles", json={}, headers={**BOX, "Idempotency-Key": "r3"})
    assert r.status_code == 202, r.text


# -- F6 ------------------------------------------------------------------ #


def test_f6_startup_marks_pending_rows_interrupted(on: TestClient, box: Config) -> None:
    path = str(box.db_path).replace("sieve.db", "aio.db")
    db = sqlite3.connect(path)
    r = on.post("/v1/runs/nope", headers={**BOX, "Idempotency-Key": "p1"})
    assert r.status_code == 404
    db.execute("update aio_idem set state='pending'")
    db.commit()
    assert db.execute("select count(*) from aio_idem where state='pending'").fetchone()[0] >= 1
    aio.startup_idem(on.app)
    assert db.execute("select count(*) from aio_idem where state='pending'").fetchone()[0] == 0
    r = on.post("/v1/runs/nope", headers={**BOX, "Idempotency-Key": "p1"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_interrupted"


# -- F15 ----------------------------------------------------------------- #


BUSY = "held by pid 4242 at /srv/sieve/data/store.lock"


def _busy_lock(*a, **k):
    from contextlib import contextmanager

    from sieve.storelock import StoreBusy

    @contextmanager
    def lock(cfg, *a, **k):
        raise StoreBusy(BUSY)
        yield

    return lock


def test_f15_store_busy_route_does_not_leak_the_lock(
    off: TestClient, box: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sieve.api.routes import v1

    box.sources["fx"] = SimpleNamespace(enabled=True)
    monkeypatch.setattr(v1, "store_lock", _busy_lock())
    r = off.post("/v1/sources/fx/pull", json={}, headers=BOX)
    assert r.status_code == 503, r.text
    assert "4242" not in r.text and "store.lock" not in r.text
    assert r.json()["error"]["code"] == "store_busy"


def test_f15_store_busy_job_does_not_leak_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    from sieve.api import v1_jobs

    aio._kit()
    monkeypatch.setattr(v1_jobs, "store_lock", _busy_lock())
    with pytest.raises(Exception) as caught:
        v1_jobs._pulled(SimpleNamespace(), None, "fx", None, "a", "j", "s")
    assert "4242" not in str(caught.value) and "store.lock" not in str(caught.value)


# -- F22 ----------------------------------------------------------------- #


def test_f22_a_session_principal_id_is_never_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    token = auth.Token(name="gate:x@y", scopes=auth.ROLE_SCOPES["viewer"], sha256="k", owner_id=None)
    monkeypatch.setattr(auth, "gate_identity", lambda call: token)
    app = SimpleNamespace(state=SimpleNamespace())
    session = aio.session_lookup(app, "c=1")
    assert session is not None and session.id
