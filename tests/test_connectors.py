"""AMS-20 — routers as data.

**Nothing here touches a real gateway.** Both kinds are exercised against a
stub HTTP server on localhost that serves the two shapes that matter: an
OpenAI-compatible `/v1/models` catalogue, and 9router's `/api/combos` admin API
with its own header. A test that needs a live router has proved nothing anybody
else can re-run.

The rule the whole feature exists to keep is asserted twice over, because it is
the one that would be expensive to get wrong: **no response, no row and no
error message ever carries a token.**
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.connectors import seed_from_toml
from sieve.connectors.base import ConnectorError
from sieve.connectors.ninerouter import NineRouterConnector
from sieve.connectors.openai_compat import OpenAICompatConnector
from sieve.connectors.registry import adapter_for, kinds
from sieve.connectors.seed import from_toml
from sieve.contracts import Chain, Connector, InventoryConfig, TargetConfig
from sieve.store import Store

NOW = datetime(2026, 9, 13, tzinfo=UTC)

#: The secret the stub demands. If this string ever reaches an API response or
#: a stored row, the test that looks for it fails.
SECRET = "sk-do-not-leak-me"

CATALOGUE: list[dict[str, Any]] = [
    {"id": "oc-go/glm-5.3", "object": "model", "context_length": 200000, "supports_tools": True},
    {"id": "openai/gpt-5-6-sol", "object": "model"},
    {"id": "sieve-coder", "object": "model", "owned_by": "combo"},
]


class Router(BaseHTTPRequestHandler):
    """One stub standing in for both kinds.

    `bearer` and `cli_token`, when set, are demanded on the catalogue and the
    admin API respectively -- so a test can watch a 401 become a sentence
    naming the environment variable rather than the key.
    """

    bearer: ClassVar[str | None] = None
    cli_token: ClassVar[str | None] = None
    combos: ClassVar[list[dict[str, Any]]] = []
    calls: ClassVar[list[tuple[str, str, Any]]] = []

    def log_message(self, *args: Any) -> None:  # keep pytest output readable
        return

    def _send(self, status: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> Any:
        length = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _admin_ok(self) -> bool:
        return not self.cli_token or self.headers.get("x-9r-cli-token") == self.cli_token

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            if self.bearer and self.headers.get("authorization") != f"Bearer {self.bearer}":
                self._send(401, {"error": "Unauthorized"})
                return
            self._send(200, {"object": "list", "data": CATALOGUE})
            return
        if self.path == "/api/combos":
            if not self._admin_ok():
                self._send(401, {"error": "Unauthorized"})
                return
            self._send(200, {"combos": self.combos})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        body = self._body()
        if self.path != "/api/combos" or not self._admin_ok():
            self._send(401, {"error": "Unauthorized"})
            return
        Router.calls.append(("POST", self.path, body))
        Router.combos = [*self.combos, {"id": "c-new", **body}]
        self._send(201, {"combo": {"id": "c-new", **body}})

    def do_PUT(self) -> None:
        body = self._body()
        if not self.path.startswith("/api/combos/") or not self._admin_ok():
            self._send(401, {"error": "Unauthorized"})
            return
        Router.calls.append(("PUT", self.path, body))
        self._send(200, {"combo": {"id": self.path.rsplit("/", 1)[-1], **body}})


@pytest.fixture
def router() -> Any:
    """A stub router on a free port, reset between tests."""
    Router.bearer = None
    Router.cli_token = None
    Router.combos = []
    Router.calls = []
    server = HTTPServer(("127.0.0.1", 0), Router)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def connector(base_url: str, **over: Any) -> Connector:
    body: dict[str, Any] = {
        "id": "c1",
        "name": "gateway",
        "kind": "openai_compat",
        "base_url": base_url,
        "read": True,
        "write": False,
        "created_at": NOW,
    }
    body.update(over)
    return Connector(**body)


# --------------------------------------------------------------------------- #
# adapters
# --------------------------------------------------------------------------- #


def test_openai_compat_lists_and_tests(router: str) -> None:
    adapter = OpenAICompatConnector(connector(router))
    assert adapter.list_models() == [e["id"] for e in CATALOGUE]

    outcome = adapter.test()
    assert outcome.ok is True
    assert outcome.models_count == 3
    assert outcome.error is None


def test_reachable_keeps_what_the_router_published(router: str) -> None:
    rows = OpenAICompatConnector(connector(router)).reachable()
    first = next(r for r in rows if r.local_id == "oc-go/glm-5.3")
    assert first.inventory == "gateway"
    assert first.capability.context_window == 200000
    assert first.capability.tools is True
    # Silence stays None. A gateway that said nothing about tools has not said
    # the model cannot use them.
    plain = next(r for r in rows if r.local_id == "openai/gpt-5-6-sol")
    assert plain.capability.tools is None


def test_a_refused_token_names_the_variable_not_the_token(
    router: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    Router.bearer = SECRET
    monkeypatch.setenv("GATEWAY_TOKEN", "the-wrong-one")
    outcome = OpenAICompatConnector(connector(router, token_env="GATEWAY_TOKEN")).test()
    assert outcome.ok is False
    assert outcome.models_count == 0
    assert "GATEWAY_TOKEN" in (outcome.error or "")
    assert "the-wrong-one" not in (outcome.error or "")


def test_a_router_that_is_not_there_is_an_answer_not_a_crash() -> None:
    # Port 1 is reserved and nothing listens on it; the connection is refused
    # rather than hanging.
    outcome = OpenAICompatConnector(connector("http://127.0.0.1:1"), timeout=1.0).test()
    assert outcome.ok is False
    assert "gateway" in (outcome.error or "")


def test_openai_compat_will_not_pretend_to_write(router: str) -> None:
    outcome = OpenAICompatConnector(connector(router)).put_combo("sieve-coder", ["a", "b"])
    assert outcome.ok is False
    assert "read" in (outcome.error or "")


def test_ninerouter_creates_then_updates_a_combo(
    router: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    Router.cli_token = SECRET
    monkeypatch.setenv("NINEROUTER_TOKEN", SECRET)
    adapter = NineRouterConnector(
        connector(
            router,
            kind="ninerouter",
            write=True,
            options={"admin_token_env": "NINEROUTER_TOKEN"},
        )
    )

    created = adapter.put_combo("sieve-coder", ["gw/a", "gw/b"])
    assert created.ok is True and created.created is True
    verb, path, body = Router.calls[-1]
    assert verb == "POST"
    # `kind` is a service kind, not a description: a combo carrying anything
    # else is filtered out of every catalogue listing and served to nobody.
    assert body["kind"] == "llm"
    assert body["models"] == ["gw/a", "gw/b"]

    again = adapter.put_combo("sieve-coder", ["gw/b", "gw/a"])
    assert again.ok is True and again.created is False
    verb, path, body = Router.calls[-1]
    assert verb == "PUT"
    assert path.endswith("/c-new")


def test_ninerouter_without_its_admin_token_says_which_one(router: str) -> None:
    Router.cli_token = SECRET
    adapter = NineRouterConnector(
        connector(
            router, kind="ninerouter", write=True, options={"admin_token_env": "NINEROUTER_TOKEN"}
        )
    )
    outcome = adapter.put_combo("sieve-coder", ["gw/a"])
    assert outcome.ok is False
    assert "NINEROUTER_TOKEN" in (outcome.error or "")
    # Reading is a different credential and still works.
    assert adapter.test().ok is True


def test_a_combo_with_no_models_is_refused(router: str) -> None:
    adapter = NineRouterConnector(connector(router, kind="ninerouter", write=True))
    assert adapter.put_combo("sieve-coder", []).ok is False


def test_the_registry_names_the_kinds(router: str) -> None:
    assert kinds() == ["ninerouter", "openai_compat"]
    assert isinstance(adapter_for(connector(router, kind="ninerouter")), NineRouterConnector)
    with pytest.raises(ConnectorError):
        adapter_for(connector(router, kind="telepathy"))


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


def test_the_store_round_trips_a_connector_and_never_a_token(tmp_path: Path) -> None:
    store = Store(tmp_path / "sieve.db")
    made = store.add_connector(connector("http://box:20128", token_env="GATEWAY_TOKEN"))
    assert [c.id for c in store.connectors()] == [made.id]
    assert store.connector_named("gateway") is not None

    store.touch_connector(made.id, pulled_at=NOW, error="refused")
    held = store.connector(made.id)
    assert held is not None and held.last_pull_at == NOW and held.last_error == "refused"

    # A later success clears the failure rather than leaving a sentence on the
    # screen that stopped being true an hour ago.
    store.touch_connector(made.id, pushed_at=NOW)
    held = store.connector(made.id)
    assert held is not None and held.last_error is None and held.last_push_at == NOW

    assert "token" not in json.dumps(held.model_dump(mode="json")).replace("token_env", "")
    assert store.delete_connector(made.id) is True
    assert store.connectors() == []


def test_inventory_rows_carry_the_connector(tmp_path: Path, router: str) -> None:
    store = Store(tmp_path / "sieve.db")
    made = store.add_connector(connector(router))
    rows = OpenAICompatConnector(made).reachable()
    store.set_reachable(made.name, rows, connector_id=made.id)

    assert len(store.reachable_for(made.id)) == len(CATALOGUE)
    assert store.reachable_for("some-other-connector") == []


# --------------------------------------------------------------------------- #
# seeding: the box migrates itself
# --------------------------------------------------------------------------- #


def toml_config(tmp_path: Path) -> Config:
    """The shape this box was actually configured in before connectors."""
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(profiles=str(tmp_path / "profiles")),
        inventories={
            "gateway": InventoryConfig(
                name="gateway",
                kind="openai_compat",
                base_url="http://localhost:20128",
                token_env="GATEWAY_TOKEN",
            ),
            "pinned": InventoryConfig(name="pinned", kind="list", models=["anthropic/claude"]),
            "other": InventoryConfig(
                name="other", kind="openai_compat", base_url="http://elsewhere:8080"
            ),
        },
        targets={
            "out": TargetConfig(name="out", kind="file", dir="out"),
            "gateway": TargetConfig(
                name="gateway",
                kind="ninerouter",
                url="http://127.0.0.1:20128",
                options={"token_env": "NINEROUTER_TOKEN"},
            ),
        },
    )


def test_the_toml_gateway_becomes_one_connector(tmp_path: Path) -> None:
    made = {c.name: c for c in from_toml(toml_config(tmp_path))}

    # One connector for the gateway, not two, even though the config describes
    # it twice -- once to read and once to write.
    gateway = made["gateway"]
    assert gateway.kind == "ninerouter"
    assert gateway.read is True and gateway.write is True
    assert gateway.base_url == "http://127.0.0.1:20128"
    assert gateway.token_env == "GATEWAY_TOKEN"
    assert gateway.options["admin_token_env"] == "NINEROUTER_TOKEN"

    # A second gateway that is only ever read from.
    assert made["other"].read is True and made["other"].write is False

    # A pinned list is not a router and a file is not a router.
    assert "pinned" not in made and "out" not in made


def test_seeding_happens_once(tmp_path: Path) -> None:
    cfg = toml_config(tmp_path)
    store = Store(cfg.db_path)
    assert {c.name for c in seed_from_toml(cfg, store)} == {"gateway", "other"}

    # A connector deleted on purpose does not grow back every hour.
    store.delete_connector(store.connectors()[0].id)
    assert seed_from_toml(cfg, store) == []
    assert len(store.connectors()) == 1


# --------------------------------------------------------------------------- #
# the API
# --------------------------------------------------------------------------- #

TOKENS = "ops:read,profiles:write,apply,telemetry:s3cret"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("GATEWAY_TOKEN", SECRET)
    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(profiles=str(tmp_path / "profiles")),
    )
    with TestClient(create_app(cfg)) as client:
        yield client


AUTH = {"authorization": "Bearer s3cret"}


def test_the_api_adds_tests_pulls_and_forgets_a_connector(client: Any, router: str) -> None:
    assert client.get("/v1/connectors").json() == []

    created = client.post(
        "/v1/connectors",
        json={
            "name": "spare",
            "kind": "openai_compat",
            "base_url": router,
            "token_env": "GATEWAY_TOKEN",
            "read": True,
        },
        headers=AUTH,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    connector_id = body["id"]
    assert body["token_env"] == "GATEWAY_TOKEN"
    assert body["token_present"] is True
    assert SECRET not in created.text

    listed = client.get("/v1/connectors")
    assert [c["name"] for c in listed.json()] == ["spare"]
    assert SECRET not in listed.text

    # `test` and `pull` decide nothing about what is routed, but they do reach
    # an arbitrary connector's `base_url` on demand, so gate v2 gates them on
    # `profiles:write` like every other connector write (CONTRACTS section 10).
    denied = client.post(f"/v1/connectors/{connector_id}/test")
    assert denied.status_code == 401

    tested = client.post(f"/v1/connectors/{connector_id}/test", headers=AUTH).json()
    assert tested["ok"] is True and tested["models_count"] == len(CATALOGUE)

    pulled = client.post(f"/v1/connectors/{connector_id}/pull", headers=AUTH).json()
    assert pulled["found"] == len(CATALOGUE)

    models = client.get(f"/v1/connectors/{connector_id}/models").json()
    assert models["count"] == len(CATALOGUE)
    assert {m["local_id"] for m in models["models"]} == {e["id"] for e in CATALOGUE}

    updated = client.put(
        f"/v1/connectors/{connector_id}", json={"read": False}, headers=AUTH
    ).json()
    assert updated["read"] is False and updated["name"] == "spare"

    assert client.delete(f"/v1/connectors/{connector_id}", headers=AUTH).status_code == 200
    assert client.get("/v1/connectors").json() == []


def test_writes_need_a_token_and_reads_do_not(client: Any, router: str) -> None:
    body = {"name": "spare", "kind": "openai_compat", "base_url": router}
    assert client.post("/v1/connectors", json=body).status_code == 401
    assert client.get("/v1/connectors").status_code == 200


def test_the_api_refuses_what_cannot_work(client: Any, router: str) -> None:
    def post(**over: Any) -> Any:
        body = {"name": "spare", "kind": "openai_compat", "base_url": router}
        body.update(over)
        return client.post("/v1/connectors", json=body, headers=AUTH)

    # A kind that only reads, asked to write.
    refused = post(write=True)
    assert refused.status_code == 400
    assert "ninerouter" in refused.json()["error"]["message"]

    # The token itself, pasted where its variable name belongs. This is the
    # mistake the whole design is trying to make impossible.
    leaked = post(token_env=SECRET)
    assert leaked.status_code == 400
    assert "NAME of an environment variable" in leaked.json()["error"]["message"]

    assert post(kind="telepathy").status_code == 400
    assert post(base_url="localhost:20128").status_code == 400
    assert post(options={"token": SECRET}).status_code == 400

    assert post().status_code == 200
    assert post().status_code == 409  # the name is taken


def test_an_unknown_v1_path_is_json_not_the_web_app(client: Any) -> None:
    missing = client.get("/v1/connectors-that-do-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #


def test_apply_ships_through_the_connector_and_shadows_the_toml_target(
    tmp_path: Path, router: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: the chain reaches the router through the row, once."""
    from sieve.engine import apply_targets

    Router.cli_token = SECRET
    monkeypatch.setenv("NINEROUTER_TOKEN", SECRET)
    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(profiles=str(tmp_path / "profiles")),
        targets={
            "gateway": TargetConfig(
                name="gateway",
                kind="ninerouter",
                url=router,
                options={"token_env": "NINEROUTER_TOKEN"},
            )
        },
    )
    store = Store(cfg.db_path)
    chain = Chain(
        profile="coder",
        computed_at=NOW,
        primary="openai/gpt-5-6-sol",
        fallbacks=["oc-go/glm-5.3"],
        local={"openai/gpt-5-6-sol": ["openai/gpt-5-6-sol"], "oc-go/glm-5.3": ["oc-go/glm-5.3"]},
    )

    results = apply_targets(cfg, [chain], dry_run=False, store=store)
    assert [r.target for r in results] == ["gateway"]
    assert results[0].error is None
    assert results[0].written == ["sieve-coder: openai/gpt-5-6-sol -> oc-go/glm-5.3"]

    # Once. The `[targets.gateway]` block describes the same box and must not
    # be written a second time.
    assert [verb for verb, _, _ in Router.calls] == ["POST"]

    held = store.connector_named("gateway")
    assert held is not None and held.last_push_at is not None and held.last_error is None
    assert [d.kind for d in store.decisions(profile="coder")] == ["apply"]
