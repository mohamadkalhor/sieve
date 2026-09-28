"""agentkit on sieve's /v1 surface -- card O9a.

The kit is vendored at `_vendor/agentkit` and mounted by `sieve/api/aio.py`.
Everything here is about that seam, in three parts:

    the switch -- `AGENT_V1` unset means the box answers exactly as it did
    before the kit arrived, asserted on the same routes the new tests use;

    the credential rule -- §3.1's table, one test per row: no credential, a
    bearer, a session cookie, a revoked key, an owner whose role has dropped,
    and a bearer and a cookie together;

    what the kit brings -- the guide, `llms.txt`, the envelope, the body cap,
    the run key, the audit row, and the wrapper on every write route.

`httpx.get` is monkeypatched to a stand-in gate for every test, so neither the
session lookup nor the owner-status lookup can open a socket: the stub answers
`role: owner`, which caps nothing, and the one test about a dropped role says
`viewer` itself.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from sieve import owners
from sieve import tokens as script_tokens
from sieve.api import aio, auth
from sieve.api.app import create_app
from sieve.axes import control as axis_control
from sieve.config import Config, Paths, StoreConfig
from sieve.profiles import control
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent

#: `agentkit.limits.RUN_MARK`, read here as a literal so the test does not have
#: to reach into the vendored copy to say what it expects.
RUN_MARK = "__aio_run__"

OWNER_EMAIL = "mohamad@example.test"
ADA = "ada@example.test"
#: The box's own token: name, scopes, secret.
TOKENS = "ops:read,profiles:write,apply:s3cret"
BOX = {"Authorization": "Bearer s3cret"}
GATE = "http://127.0.0.1:8122"


class FakeReply:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class GateStub:
    """`httpx.get` answered like gate: `GET /v1/session` and `GET /v1/user/{id}`.

    One body answers both, which is what the real gate does closely enough for
    this file: `status` and `role` are what the owner-status lookup reads, and
    `user_id`/`email`/`app` are what the session lookup reads.
    """

    def __init__(self, status_code: int = 200, **body: Any) -> None:
        self._status_code = status_code
        self._body = body
        self.calls: list[dict[str, str]] = []

    def __call__(
        self, url: str, *, headers: dict[str, str] | None = None, timeout: float = 0.0
    ) -> FakeReply:
        self.calls.append({"url": url, **dict(headers or {})})
        return FakeReply(self._status_code, dict(self._body))


@pytest.fixture(autouse=True)
def _gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GateStub]:
    """A gate that is always reachable and always says `owner`."""
    auth._gate_cache.clear()
    fake = GateStub(
        user_id=17, email=ADA, name="Ada", role="owner", status="active", app="sieve"
    )
    monkeypatch.setattr(httpx, "get", fake)
    yield fake
    auth._gate_cache.clear()


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("SIEVE_OWNER_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("SIEVE_GATE_URL", GATE)
    monkeypatch.delenv("AGENT_V1", raising=False)
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
    )


@pytest.fixture
def seats(box: Config) -> Store:
    """The gate owner and one member, signed in for the first time."""
    store = Store(box.db_path)
    axis_control.seed(store, box.axes_dir)
    boss = owners.sign_in(store, OWNER_EMAIL, "owner", box.profiles_dir)
    control.seed(store, box.profiles_dir, boss.id)
    owners.sign_in(store, ADA, "member", box.profiles_dir)
    return store


def secret_for(store: Store, email: str, scopes: set[str]) -> tuple[str, str]:
    """Mint a key for the person with `email`; return (secret, token id)."""
    who = owners.by_email(store, email)
    assert who is not None
    token, secret = script_tokens.mint(store, who.id, "cron", scopes)
    return secret, token.id


def auth_header(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture
def off(box: Config, seats: Store) -> Iterator[TestClient]:
    """The box as it was before the kit: `AGENT_V1` unset."""
    with TestClient(create_app(box)) as client:
        yield client


@pytest.fixture
def on(box: Config, seats: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The box with the kit on."""
    monkeypatch.setenv("AGENT_V1", "1")
    with TestClient(create_app(box)) as client:
        yield client


# --------------------------------------------------------------------------- #
# off: nothing changed for anything that was already working
# --------------------------------------------------------------------------- #


def test_off_a_read_is_open_and_sieve_answers_it(off: TestClient) -> None:
    answer = off.get("/v1/profiles")
    assert answer.status_code == 200
    assert answer.json()


def test_off_a_write_needs_no_idempotency_key(off: TestClient) -> None:
    """§3.4 is the kit's rule; with the kit off, sieve's own rule stands."""
    answer = off.post("/v1/apply", json={"profiles": []}, headers=BOX)
    assert answer.status_code != 400
    assert answer.json().get("error", {}).get("code") != "idempotency_key_required"


def test_off_a_bad_bearer_is_sieves_own_word(off: TestClient) -> None:
    answer = off.post("/v1/apply", json={}, headers=auth_header("sv_not-a-real-token"))
    assert answer.status_code == 401
    assert answer.json()["error"]["code"] == "unauthenticated"


def test_off_the_kit_is_not_mounted(off: TestClient) -> None:
    """No `llms.txt`, no contract header, and the guide is sieve's own route."""
    assert "# sieve" not in off.get("/llms.txt").text
    assert "aio-contract" not in {k.lower() for k in off.get("/v1/profiles").headers}
    guide = off.get("/v1/guide")
    assert guide.status_code == 200
    assert "aio-contract" not in {k.lower() for k in guide.headers}


# --------------------------------------------------------------------------- #
# on: the credential rule, one test per row of §3.1
# --------------------------------------------------------------------------- #


def test_on_a_read_with_no_credential_is_told_to_sign_in(on: TestClient) -> None:
    answer = on.get("/v1/profiles")
    assert answer.status_code == 401
    assert answer.json()["error"]["code"] == "sign_in"


def test_on_a_read_with_the_configured_token_is_the_owner(
    on: TestClient, seats: Store
) -> None:
    assert on.get("/v1/profiles", headers=BOX).status_code == 200
    who = on.get("/v1/me", headers=BOX).json()
    assert who["email"] == OWNER_EMAIL
    assert who["role"] == "owner"


def test_on_a_read_with_a_minted_key_is_that_person(
    on: TestClient, seats: Store
) -> None:
    secret, _ = secret_for(seats, ADA, {"read"})
    assert on.get("/v1/profiles", headers=auth_header(secret)).status_code == 200
    assert on.get("/v1/me", headers=auth_header(secret)).json()["email"] == ADA


def test_on_a_revoked_key_is_refused_as_bad_key(on: TestClient, seats: Store) -> None:
    secret, token_id = secret_for(seats, ADA, {"read"})
    assert on.get("/v1/profiles", headers=auth_header(secret)).status_code == 200
    who = owners.by_email(seats, ADA)
    assert who is not None
    script_tokens.revoke(seats, who.id, token_id)
    answer = on.get("/v1/profiles", headers=auth_header(secret))
    assert answer.status_code == 401
    assert answer.json()["error"]["code"] == "bad_key"


def test_on_a_session_cookie_reads_as_that_person(on: TestClient) -> None:
    answer = on.get("/v1/me", headers={"Cookie": "gate_session=whatever"})
    assert answer.status_code == 200
    assert answer.json()["email"] == ADA


def test_on_a_bearer_and_a_cookie_together_are_refused(on: TestClient) -> None:
    answer = on.get("/v1/profiles", headers={**BOX, "Cookie": "gate_session=whatever"})
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "mixed_credentials"


def test_on_a_write_without_the_scope_is_not_allowed(
    on: TestClient, seats: Store
) -> None:
    secret, _ = secret_for(seats, ADA, {"read"})
    answer = on.post("/v1/apply", json={}, headers=auth_header(secret))
    assert answer.status_code == 403
    assert answer.json()["error"]["code"] == "not_allowed"


def test_on_a_write_with_the_scope_reaches_the_route(on: TestClient) -> None:
    """The rule lets it through; what the route then says is sieve's business."""
    answer = on.post("/v1/apply", json={}, headers=BOX)
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "not_found"


def test_on_a_dropped_role_demotes_the_key(
    on: TestClient, seats: Store, _gate: GateStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.1: the key holds `admin`, gate now says this owner is a viewer."""
    secret, _ = secret_for(seats, ADA, {"admin"})
    # The key is honoured as admin while gate still says owner...
    assert on.delete("/v1/connectors/nope", headers=auth_header(secret)).status_code == 404

    _gate._body = {
        "user_id": 17, "email": ADA, "name": "Ada", "role": "viewer",
        "status": "active", "app": "sieve",
    }
    auth._gate_cache.clear()
    answer = on.delete("/v1/connectors/nope", headers=auth_header(secret))
    assert answer.status_code == 403
    assert answer.json()["error"]["code"] == "not_allowed"


def test_on_a_gate_that_cannot_be_reached_refuses_the_key(
    on: TestClient, seats: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: Any, **__: Any) -> Any:
        raise httpx.ConnectError("gate is down")

    secret, _ = secret_for(seats, ADA, {"admin"})
    monkeypatch.setattr(httpx, "get", refuse)
    auth._gate_cache.clear()
    answer = on.delete("/v1/connectors/nope", headers=auth_header(secret))
    assert answer.status_code >= 400
    assert answer.json()["error"]["code"] == "auth_unavailable"


# --------------------------------------------------------------------------- #
# on: what the kit brings with it
# --------------------------------------------------------------------------- #


def test_on_the_guide_is_the_kit_serving_operating_md(on: TestClient) -> None:
    answer = on.get("/v1/guide")
    assert answer.status_code == 200
    assert answer.text == (REPO / "OPERATING.md").read_text()
    assert answer.headers["content-type"].startswith("text/markdown")


def test_on_llms_txt_is_served(on: TestClient) -> None:
    answer = on.get("/llms.txt")
    assert answer.status_code == 200
    assert answer.text.startswith("# sieve")


def test_on_the_openapi_is_public(on: TestClient) -> None:
    answer = on.get("/v1/openapi.json")
    assert answer.status_code == 200
    assert answer.json()["paths"]


def test_on_the_contract_header_is_on_every_answer(on: TestClient) -> None:
    for answer in (on.get("/v1/profiles"), on.get("/v1/nope"), on.get("/v1/guide")):
        assert "aio-contract" in {k.lower() for k in answer.headers}, answer.request.url


def test_on_a_run_without_a_key_is_refused(on: TestClient) -> None:
    answer = on.post("/v1/runs/nope", headers=BOX)
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "idempotency_key_required"


def test_on_a_run_replayed_with_the_same_key_is_the_same_answer(on: TestClient) -> None:
    key = {"Idempotency-Key": "k-one", **BOX}
    first = on.post("/v1/runs/nope", headers=key)
    second = on.post("/v1/runs/nope", headers=key)
    assert first.status_code == second.status_code == 404
    assert first.json() == second.json()


def test_on_a_run_with_a_different_body_under_the_same_key_is_a_mismatch(
    on: TestClient,
) -> None:
    key = {"Idempotency-Key": "k-two", **BOX}
    assert on.post("/v1/runs/nope", headers=key).status_code == 404
    answer = on.post("/v1/runs/nope", json={"step": "other"}, headers=key)
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "idempotency_mismatch"


def test_on_a_replayed_write_does_not_run_twice(on: TestClient, seats: Store) -> None:
    """The mint is the proof: one key, one token, the same secret twice."""
    key = {"Idempotency-Key": "k-three", **BOX}
    body = {"name": "cron", "scopes": ["read"]}
    first = on.post("/v1/tokens", json=body, headers=key)
    second = on.post("/v1/tokens", json=body, headers=key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    boss = owners.by_email(seats, OWNER_EMAIL)
    assert boss is not None
    minted = [t for t in script_tokens.tokens(seats, boss.id) if t.name == "cron"]
    assert len(minted) == 1


def test_on_a_body_over_the_cap_is_refused(on: TestClient) -> None:
    answer = on.post(
        "/v1/apply", content=b"x" * (1024 * 1024 + 1), headers={**BOX, "content-type": "application/json"}
    )
    assert answer.status_code == 413
    assert answer.json()["error"]["code"] == "too_large"


def test_on_a_body_that_does_not_match_the_contract_is_the_kits_envelope(
    on: TestClient,
) -> None:
    answer = on.post("/v1/outcomes", json={"nonsense": True}, headers=BOX)
    assert answer.status_code == 422
    assert answer.json()["error"]["code"] == "invalid_body"


def test_on_an_unknown_v1_path_is_not_found(on: TestClient) -> None:
    answer = on.get("/v1/not-a-route", headers=BOX)
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "not_found"


def test_on_a_write_leaves_an_audit_row(on: TestClient, box: Config) -> None:
    assert on.post("/v1/tokens", json={"name": "cron", "scopes": ["read"]}, headers=BOX).status_code == 201
    rows = (
        sqlite3.connect(box.path("aio.db"))
        .execute("SELECT principal, action, target, result_status FROM aio_audit")
        .fetchall()
    )
    assert rows, "a write left no audit row"
    assert any(row[1] == "POST" and "tokens" in (row[2] or "") for row in rows)


def test_on_every_write_route_carries_the_wrapper(
    box: Config, seats: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_V1", "1")
    app = create_app(box)
    writes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/v1")
        and (route.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert writes, "no write routes found: the walk is wrong, not the app"
    for route in writes:
        assert getattr(route.endpoint, aio.WRAPPED, False), route.path


def test_on_the_run_routes_are_the_ones_that_do_work(
    box: Config, seats: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_V1", "1")
    app = create_app(box)
    marked = {
        route.path_format
        for route in app.routes
        if isinstance(route, APIRoute) and getattr(route.endpoint, RUN_MARK, False)
    }
    assert marked == set(aio.RUN_ROUTES)


def test_on_the_envelope_is_the_shape_the_ui_reads(on: TestClient) -> None:
    """`web/src/lib/api/client.ts` reads `error.code` and `error.message`."""
    for answer in (on.get("/v1/profiles"), on.get("/v1/nope")):
        error = answer.json()["error"]
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]
