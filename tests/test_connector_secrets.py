"""A02 -- a connector names a secret; the box resolves it.

A connector used to carry `token_env`, the *name* of an environment variable this
box sets, and the API let anybody with `profiles:write` write any name there. Now
it carries `secret`, an id from `[secrets.<id>]` in `sieve.toml`: the table that
turns an id into a variable name is edited on the box and does not travel.

What these tests hold to:

* an id resolves to whatever the variable it names holds, all the way to the
  wire -- and the row keeps nothing but the id;
* an id bound to another kind is refused, by the id, never by a value;
* `*_env` anywhere in a body is refused with a sentence saying what to write,
  in the connector routes and in the whole-configuration bundle;
* `tools/migrate_connector_secrets.py` prints the mapping without writing,
  refuses a name no id registers rather than guessing, and moves a row -- with a
  copy of the database -- into ids that still work.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, ConnectorConfig, Paths, StoreConfig
from sieve.contracts import Connector
from sieve.pinned import host_port
from sieve.secrets import SecretConfig
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "migrate_connector_secrets.py"
NOW = datetime(2026, 9, 13, tzinfo=UTC)

#: What the stub demands, and what the variable below holds. Look for this string
#: in a response or a stored row and the test that looks fails.
SECRET = "bearer-that-must-not-be-served"

TOKENS = "root:read,profiles:write,apply,admin:root-secret"
AUTH = {"authorization": "Bearer root-secret"}

#: The box's `[secrets.*]` table as the migration script reads it.
SECRETS_TOML = """
[secrets.gateway]
env = "GATEWAY_TOKEN"
kinds = ["ninerouter", "openai_compat"]

[secrets.ninerouter_admin]
env = "NINEROUTER_TOKEN"
kinds = ["ninerouter"]
"""


class Catalogue(BaseHTTPRequestHandler):
    """A stub catalogue that demands one bearer and remembers what it was sent."""

    bearer: ClassVar[str | None] = None
    seen: ClassVar[list[str | None]] = []

    def log_message(self, *args: Any) -> None:  # keep pytest output readable
        return

    def _send(self, status: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        sent = self.headers.get("authorization")
        Catalogue.seen.append(sent)
        if self.path != "/v1/models" or (self.bearer and sent != f"Bearer {self.bearer}"):
            self._send(401, {"error": "Unauthorized"})
            return
        self._send(
            200, {"object": "list", "data": [{"id": "openai/gpt-5-6-sol", "object": "model"}]}
        )


@pytest.fixture
def router() -> Any:
    """A stub catalogue on a free port, reset between tests."""
    Catalogue.bearer = None
    Catalogue.seen = []
    server = HTTPServer(("127.0.0.1", 0), Catalogue)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, router: str) -> Config:
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("GATEWAY_TOKEN", SECRET)
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(profiles=str(tmp_path / "profiles")),
        secrets={
            "gateway": SecretConfig(env="GATEWAY_TOKEN", kinds=["openai_compat"]),
            "router_admin": SecretConfig(env="NINEROUTER_ADMIN_TOKEN", kinds=["ninerouter"]),
        },
        # The stub's port is only known once the fixture has started it, and
        # `[connectors.hosts]` is an exact `host:port` list with no wildcards.
        # Port 1 is in it because a bundle test names a spare connector nobody
        # ever calls: the host list gates where this box may *go*, and landing a
        # row is a different question from reaching it.
        connectors=ConnectorConfig(
            hosts={
                kind: [host_port(router), "127.0.0.1:1"]
                for kind in ("openai_compat", "ninerouter")
            }
        ),
    )


@pytest.fixture
def client(box: Config) -> Any:
    with TestClient(create_app(box)) as client:
        yield client


def body_for(router: str, **over: Any) -> dict[str, Any]:
    return {
        "name": "spare",
        "kind": "openai_compat",
        "base_url": router,
        "read": True,
        **over,
    }


# --------------------------------------------------------------------------- #
# the id resolves, and the value only travels one way
# --------------------------------------------------------------------------- #


def test_an_id_carries_the_value_the_variable_holds(client: TestClient, router: str) -> None:
    """Row names an id, id names a variable, variable holds the value."""
    Catalogue.bearer = SECRET
    made = client.post("/v1/connectors", json=body_for(router, secret="gateway"), headers=AUTH)
    assert made.status_code == 200, made.text
    row = made.json()
    assert row["secret"] == "gateway"
    assert row["token_present"] is True
    # Neither the value, nor the name of the variable it lives in.
    assert SECRET not in made.text
    assert "GATEWAY_TOKEN" not in made.text

    told = client.post(f"/v1/connectors/{row['id']}/test", headers=AUTH)
    assert told.status_code == 200, told.text
    assert told.json()["ok"] is True, told.text
    # The stub demands exactly this bearer, so the value came from the variable
    # the id names -- and not from the row, which holds the id.
    assert Catalogue.seen == [f"Bearer {SECRET}"]
    assert SECRET not in told.text


def test_an_id_the_box_does_not_have_is_refused_by_name(client: TestClient, router: str) -> None:
    """An id nothing registers is a mistake to be said out loud, not a 401 later."""
    refused = client.post(
        "/v1/connectors", json=body_for(router, secret="not-a-secret-here"), headers=AUTH
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "bad_connector"
    assert "not-a-secret-here" in refused.json()["error"]["message"]
    assert "sieve.toml" in refused.json()["error"]["message"]


def test_an_id_bound_to_another_kind_is_refused_by_its_name(
    client: TestClient, box: Config, router: str
) -> None:
    """`kinds` is the binding: an id is not a key to every door.

    Written straight into the table, the way a config edit or an older row can
    leave a mismatch the connector routes would have refused.
    """
    Store(box.store.path).add_connector(
        Connector(
            id="c-drifted",
            name="drifted",
            kind="ninerouter",
            base_url=router,
            read=True,
            secret="gateway",
            created_at=NOW,
        )
    )
    told = client.post("/v1/connectors/c-drifted/test", headers=AUTH)
    assert told.status_code == 200, told.text
    assert told.json()["ok"] is False
    assert "gateway" in told.json()["error"]
    assert "openai_compat" in told.json()["error"]  # ...the kinds it does name
    assert SECRET not in told.text
    # Refused before a socket opened: the stub never heard from this box.
    assert Catalogue.seen == []


# --------------------------------------------------------------------------- #
# a body names an id, and nothing else
# --------------------------------------------------------------------------- #


def test_a_body_that_names_an_environment_variable_is_refused(
    client: TestClient, router: str
) -> None:
    """`*_env` at the top and nested in `options`, on write and on update.

    Refused rather than dropped: a field that is silently ignored is a field
    somebody believes is working.
    """
    made = client.post("/v1/connectors", json=body_for(router), headers=AUTH)
    assert made.status_code == 200, made.text
    connector_id = made.json()["id"]

    for patch in (
        {"token_env": "GATEWAY_TOKEN"},
        {"admin_token_env": "NINEROUTER_TOKEN"},
        {"options": {"admin_token_env": "NINEROUTER_TOKEN"}},
    ):
        key = next(iter(patch))
        created = client.post(
            "/v1/connectors", json=body_for(router, name="other", **patch), headers=AUTH
        )
        assert created.status_code == 422, created.text
        assert created.json()["error"]["code"] == "bad_connector"
        assert key in created.json()["error"]["message"]
        # The same word on the update route, where a name used to be settable
        # after the fact.
        updated = client.put(f"/v1/connectors/{connector_id}", json=patch, headers=AUTH)
        assert updated.status_code == 422, updated.text
        assert updated.json()["error"]["code"] == "bad_connector"

    # A value pasted where the id belongs. It is refused like any other body that
    # does not match the contract, and the shape of the fault is answered -- the
    # caller's own string comes back to the caller, and no value this box holds
    # is ever in a response.
    leaked = client.post(
        "/v1/connectors", json=body_for(router, name="other", secret=SECRET), headers=AUTH
    )
    assert leaked.status_code == 422
    assert leaked.json()["error"]["code"] == "bad_connector"

    # Nothing was written by any of it.
    assert [c["name"] for c in client.get("/v1/connectors", headers=AUTH).json()] == ["spare"]


def test_the_bundle_carries_ids_and_refuses_names(client: TestClient) -> None:
    """The widest door names ids too.

    Unscoped, the export used to say which variable on this box holds a live
    token per connector. It carries the id now, and a document that names a
    variable instead is refused like any other body.
    """
    document = client.get("/v1/config", headers=AUTH).json()
    assert "token_env" not in json.dumps(document)
    assert "admin_token_env" not in json.dumps(document)

    document["connectors"] = [
        {
            "name": "spare",
            "kind": "openai_compat",
            "base_url": "http://127.0.0.1:1",
            "secret": "gateway",
            "admin_secret": None,
            "read": True,
            "write": False,
            "poll_minutes": 60,
        }
    ]
    applied = client.put("/v1/config", json=document, headers=AUTH)
    assert applied.status_code == 200, applied.text
    assert client.get("/v1/connectors", headers=AUTH).json()[0]["secret"] == "gateway"

    for poisoned in (
        {**document["connectors"][0], "token_env": "GATEWAY_TOKEN"},
        {**document["connectors"][0], "secret": "a-secret-this-box-does-not-have"},
    ):
        document["connectors"] = [poisoned]
        refused = client.put("/v1/config", json=document, headers=AUTH)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "bad_connector"

    # Refused whole: the row is still the one that landed.
    assert client.get("/v1/connectors", headers=AUTH).json()[0]["secret"] == "gateway"


# --------------------------------------------------------------------------- #
# the one-way move of the rows that still carry names
# --------------------------------------------------------------------------- #


def legacy_row(
    db: Path,
    *,
    kind: str,
    env: str,
    admin: str | None = None,
    name: str = "gateway",
    base_url: str = "http://127.0.0.1:1",
) -> None:
    """A connector row as it looked before A02: names, in the row itself."""
    store = Store(db)
    made = store.add_connector(
        Connector(
            id="c1",
            name=name,
            kind=kind,
            base_url=base_url,
            read=True,
            write=True,
            created_at=NOW,
        )
    )
    options: dict[str, Any] = {"timeout": 30}
    if admin:
        options["admin_token_env"] = admin
    with sqlite3.connect(db) as raw:
        raw.execute(
            "UPDATE connectors SET token_env=?, options=? WHERE id=?",
            (env, json.dumps(options), made.id),
        )


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "sieve.toml"
    path.write_text(SECRETS_TOML, encoding="utf-8")
    return path


def migrate(*args: str) -> subprocess.CompletedProcess[str]:
    """The tool as the operator runs it."""
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)


def row_in(db: Path) -> tuple[Any, ...]:
    with sqlite3.connect(db) as raw:
        got = raw.execute(
            "SELECT secret, admin_secret, token_env, options FROM connectors"
        ).fetchone()
    return tuple(got)


def test_the_migration_prints_the_mapping_and_writes_nothing(tmp_path: Path) -> None:
    """Dry run is the default: names in, ids out, and the file untouched."""
    db = tmp_path / "sieve.db"
    legacy_row(db, kind="ninerouter", env="GATEWAY_TOKEN", admin="NINEROUTER_TOKEN")

    ran = migrate("--db", str(db), "--config", str(config_file(tmp_path)))

    assert ran.returncode == 0, ran.stderr
    assert "0 UNMAPPED" in ran.stdout
    assert "-> UNMAPPED" not in ran.stdout
    assert "-> secret gateway" in ran.stdout
    assert "-> admin_secret ninerouter_admin" in ran.stdout
    assert "DRY RUN" in ran.stdout
    # The names it read, so the operator can check the mapping before it lands.
    assert "GATEWAY_TOKEN" in ran.stdout
    assert "NINEROUTER_TOKEN" in ran.stdout
    assert SECRET not in ran.stdout and SECRET not in ran.stderr
    assert row_in(db) == (
        None,
        None,
        "GATEWAY_TOKEN",
        '{"timeout": 30, "admin_token_env": "NINEROUTER_TOKEN"}',
    )


def test_the_migration_refuses_a_name_no_id_registers(tmp_path: Path) -> None:
    """It does not guess which token a host is given: it stops and says which."""
    db = tmp_path / "sieve.db"
    legacy_row(db, kind="ninerouter", env="GATEWAY_TOKEN", admin="NOT_A_REGISTERED_NAME")
    backups = tmp_path / "backups"

    ran = migrate(
        "--db",
        str(db),
        "--config",
        str(config_file(tmp_path)),
        "--apply",
        "--backups",
        str(backups),
    )

    assert ran.returncode == 2, ran.stdout
    assert "UNMAPPED" in ran.stdout
    assert "refusing to apply" in ran.stderr
    assert "NOT_A_REGISTERED_NAME" in ran.stdout
    # Nothing written, and no copy taken: it never got that far.
    assert row_in(db) == (
        None,
        None,
        "GATEWAY_TOKEN",
        '{"timeout": 30, "admin_token_env": "NOT_A_REGISTERED_NAME"}',
    )
    assert list(backups.glob("*.db")) == []


def test_the_migration_moves_the_names_and_keeps_a_copy(tmp_path: Path) -> None:
    """`--apply`: the ids land, the names go, and a copy is taken first."""
    db = tmp_path / "sieve.db"
    legacy_row(db, kind="ninerouter", env="GATEWAY_TOKEN", admin="NINEROUTER_TOKEN")
    backups = tmp_path / "backups"

    ran = migrate(
        "--db",
        str(db),
        "--config",
        str(config_file(tmp_path)),
        "--apply",
        "--backups",
        str(backups),
    )

    assert ran.returncode == 0, ran.stderr
    assert "backed up to" in ran.stdout
    copies = list(backups.glob("sieve-*.db"))
    assert len(copies) == 1
    # The copy is the database as it was: a way back, not a second edit.
    assert row_in(copies[0])[2] == "GATEWAY_TOKEN"

    landed = row_in(db)
    assert landed[0] == "gateway"
    assert landed[1] == "ninerouter_admin"
    assert landed[2] is None
    # The option key that held the second name goes with it. What else was in
    # `options` stays.
    assert json.loads(landed[3]) == {"timeout": 30}


def test_a_migrated_row_still_reaches_its_router(tmp_path: Path, box: Config, router: str) -> None:
    """The point of the move: an id the script wrote works the same as one written
    by hand. Run last, on the box's own database."""
    Catalogue.bearer = SECRET
    made = Store(box.store.path)
    legacy_row(
        Path(box.store.path),
        kind="openai_compat",
        env="GATEWAY_TOKEN",
        name="spare",
        base_url=router,
    )
    assert made.connectors()[0].secret is None

    ran = migrate(
        "--db",
        str(box.store.path),
        "--config",
        str(config_file(tmp_path)),
        "--apply",
        "--backups",
        str(tmp_path / "backups"),
    )
    assert ran.returncode == 0, ran.stderr

    with TestClient(create_app(box)) as client:
        listed = client.get("/v1/connectors", headers=AUTH).json()
        assert listed[0]["secret"] == "gateway"
        assert listed[0]["token_present"] is True
        told = client.post(f"/v1/connectors/{listed[0]['id']}/test", headers=AUTH)
        assert told.status_code == 200, told.text
        assert told.json()["ok"] is True, told.text
        assert Catalogue.seen == [f"Bearer {SECRET}"]
