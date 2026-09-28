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
from types import SimpleNamespace
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
TOKENS = "ops:read,profiles:write,apply:s3cret;feed:telemetry:t3lem"
BOX = {"Authorization": "Bearer s3cret"}
#: The box's telemetry token, the shape sieve-feed and sieve-probe carry.
FEED = {"Authorization": "Bearer t3lem"}
GATE = "http://127.0.0.1:8122"


class FakeReply:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class GateStub:
    """gate, as both readers see it: `GET /v1/session` and `GET /v1/user/{id}`.

    One body answers both, which is what the real gate does closely enough for
    this file: `status` and `role` are what the owner-status lookup reads, and
    `user_id`/`email`/`app` are what the session lookup reads.

    Two entry points, because sieve and the kit dial gate differently: sieve's
    own reader calls `httpx.get`, the kit's `GateLookup` holds an `httpx.Client`
    and calls `.get` on it. Both are stood in for by the fixture below, so no
    test can reach a gate running on this box.
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

    def client(self, *args: Any, **kwargs: Any) -> GateStub:
        """`httpx.Client(...)` -- the kit's way in. It keeps no state of its own."""
        return self

    def get(
        self, url: str, *, headers: dict[str, str] | None = None, timeout: float = 0.0
    ) -> FakeReply:
        """What the kit's `GateLookup` calls on the client it was handed."""
        return self(url, headers=headers, timeout=timeout)


@pytest.fixture(autouse=True)
def _gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GateStub]:
    """A gate that is always reachable and always says `owner`."""
    auth._gate_cache.clear()
    fake = GateStub(
        user_id=17, email=ADA, name="Ada", role="owner", status="active", app="sieve"
    )
    monkeypatch.setattr(httpx, "get", fake)
    monkeypatch.setattr(httpx, "Client", fake.client)
    yield fake
    auth._gate_cache.clear()


def forget_gate(client: TestClient) -> None:
    """Drop the kit's cached role answers for this app.

    §3.1 caches a gate answer for 30 s, so a test that changes what gate says
    has to say so here as well as at `auth._gate_cache` -- otherwise the second
    request is answered from the first request's cache and proves nothing.
    """
    lookup = aio.gate_lookup(client.app)
    if lookup is not None:
        lookup.forget()


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
    """The gate owner and one member, signed in for the first time.

    Both carry gate's own user id, because that is what a real sign-in records
    (`owners.sign_in(..., gate_id=...)`, from gate's session): §3.1's G1 lookup
    asks gate about that id, and a local row gate has never issued an id to is
    a row gate cannot vouch for. The stub answers for any id.
    """
    store = Store(box.db_path)
    axis_control.seed(store, box.axes_dir)
    boss = owners.sign_in(store, OWNER_EMAIL, "owner", box.profiles_dir, gate_id="17")
    control.seed(store, box.profiles_dir, boss.id)
    owners.sign_in(store, ADA, "member", box.profiles_dir, gate_id="18")
    return store


def secret_for(store: Store, email: str, scopes: set[str]) -> tuple[str, str]:
    """Mint a key for the person with `email`; return (secret, token id)."""
    who = owners.by_email(store, email)
    assert who is not None
    token, secret = script_tokens.mint(store, who.id, "cron", scopes)
    return secret, token.id


def all_routes(app: Any) -> list[APIRoute]:
    """Every APIRoute the app answers with, including those of included routers.

    FastAPI 0.141 keeps an included router as one `_IncludedRouter` entry in
    `app.routes` that reads its `original_router` lazily; walk into those.
    """
    found: list[APIRoute] = []

    def walk(routes: Any) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
            elif isinstance(route, APIRoute):
                found.append(route)

    walk(app.routes)
    return found


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
    assert answer.json()["error"]["code"] == "unauthorized"


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
    # /v1/apply is a run: it needs an Idempotency-Key (§3.4) before anything else.
    answer = on.post("/v1/apply", json={}, headers={**BOX, "Idempotency-Key": "reach-1"})
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "not_found"


def test_on_a_dropped_role_demotes_the_key(
    on: TestClient, seats: Store, _gate: GateStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.1: the key holds `admin`, gate now says this owner is a viewer."""
    # `read` survives the demotion: a key left with no scope at all is refused
    # as bad_key (401) by the kit, and this test is about the 403.
    secret, _ = secret_for(seats, ADA, {"read", "admin"})
    # The key is honoured as admin while gate still says owner...
    assert on.delete("/v1/connectors/nope", headers=auth_header(secret)).status_code == 404

    _gate._body = {
        "user_id": 17, "email": ADA, "name": "Ada", "role": "viewer",
        "status": "active", "app": "sieve",
    }
    auth._gate_cache.clear()
    forget_gate(on)
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
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: SimpleNamespace(get=refuse))
    auth._gate_cache.clear()
    forget_gate(on)
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
    # FEED holds `telemetry`, which /v1/outcomes demands before it reads a body.
    answer = on.post("/v1/outcomes", json={"nonsense": True}, headers=FEED)
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
    # `action` is "<METHOD> <route template>" (agentkit.audit)
    assert any((row[1] or "").startswith("POST ") and "tokens" in row[1] for row in rows)


def test_on_the_kit_file_sits_beside_the_store_and_never_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`aio.db` comes from the data dir, not from where the process was started.

    The store's own directory is the app's data dir -- `/srv/sieve/data` on the
    box, from `[store] path = "data/sieve.db"` -- so the kit's file is
    `<store dir>/aio.db`. Deriving it from the config root instead would put a
    database in `/srv/sieve/aio.db`, and deriving it from the working directory
    puts one wherever the process happened to start.
    """
    monkeypatch.setenv("AGENT_V1", "1")
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("SIEVE_OWNER_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("SIEVE_GATE_URL", GATE)
    data = tmp_path / "data"
    data.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    box = Config(root=tmp_path, store=StoreConfig(path="data/sieve.db"))
    create_app(box)
    assert aio.data_dir(box) == data
    assert (data / "aio.db").is_file()
    assert list(elsewhere.iterdir()) == [], "the kit wrote into the working directory"


def test_on_every_write_route_carries_the_wrapper(
    box: Config, seats: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_V1", "1")
    app = create_app(box)
    writes = [
        route
        for route in all_routes(app)
        if route.path.startswith("/v1")
        and route.name != "v1_not_found"  # sieve's own JSON 404 catch-all
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
        for route in all_routes(app)
        if getattr(route.endpoint, RUN_MARK, False)
    }
    assert marked == set(aio.RUN_ROUTES)


def test_on_the_envelope_is_the_shape_the_ui_reads(on: TestClient) -> None:
    """`web/src/lib/api/client.ts` reads `error.code` and `error.message`."""
    for answer in (on.get("/v1/profiles"), on.get("/v1/nope")):
        error = answer.json()["error"]
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]


# --------------------------------------------------------------------------- #
# on: the machine callers that exist today keep working (orchestrator review)
# --------------------------------------------------------------------------- #


def test_on_a_telemetry_token_still_reports(on: TestClient) -> None:
    """sieve-feed and sieve-probe post telemetry with a `SIEVE_TOKENS` record.

    `telemetry` is in no role's table, and the kit caps a key by its owner's
    role; the owner's role carries it, so the box's own token keeps its scope.
    """
    answer = on.post("/v1/telemetry", json=[], headers=FEED)
    assert answer.status_code not in (401, 403), answer.text


def test_on_a_members_telemetry_key_is_capped(
    on: TestClient, seats: Store, _gate: GateStub
) -> None:
    """Only the owner reports outcomes: a member's key loses the scope."""
    secret, _ = secret_for(seats, ADA, {"read", "telemetry"})
    _gate._body = {
        "user_id": 18, "email": ADA, "name": "Ada", "role": "member",
        "status": "active", "app": "sieve",
    }
    auth._gate_cache.clear()
    forget_gate(on)
    answer = on.post("/v1/telemetry", json=[], headers=auth_header(secret))
    assert answer.status_code == 403
    assert answer.json()["error"]["code"] == "not_allowed"


def test_on_the_probes_apply_needs_no_idempotency_key(on: TestClient) -> None:
    """sieve-probe posts /v1/profiles/{name}/apply without a key every 15 min."""
    answer = on.post("/v1/profiles/nope/apply", json={}, headers=BOX)
    assert answer.json().get("error", {}).get("code") != "idempotency_key_required"
