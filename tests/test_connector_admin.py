"""A01 — a connector is the owner's, and `admin` is what says so.

Three claims, in the order they matter:

**Every connector write needs `admin`**, which the gate owner's role alone
carries: create, update, delete, `test` and `pull`. A `member` keeps `apply`
-- shipping a chain to a router somebody already configured is a different act
from deciding which host a token this box holds is sent to -- and a `viewer`
keeps `profiles:write`. Neither can add, redirect, delete, test or refresh one.

**A whole-configuration bundle that changes a connector is refused whole**
without `admin`, and a bundle that leaves the connectors exactly as they are
needs none. That second half is the one that could quietly break people: the
export, edit, import round trip of everything else in the document must keep
working for a member.

**Names are unique per owner, not globally** (migration 0013's
`connectors_owner_name`), so two owners may each hold a connector called
`spare`, and a bundle naming one of them writes the caller's own row and
leaves the other person's untouched.

The gate is stubbed the way `test_gate_v2.py` stubs it -- `httpx.get` answered
from a dict, no socket -- because what matters here is the role-to-scope
mapping meeting the real store.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from sieve.api import auth
from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import SourceConfig

REPO = Path(__file__).resolve().parent.parent

#: `ada` is the box's owner; `bo` is a second one, which gate may also say.
#: The configured tokens deliberately carry no `admin`: a token holds the
#: scopes it was minted with, and that is not this card's to change.
SEATS = {
    "ada": ("owner", "ada@example.test", 11),
    "bo": ("owner", "bo@example.test", 12),
    "mem": ("member", "mem@example.test", 13),
    "view": ("viewer", "view@example.test", 14),
}
#: `SIEVE_TOKENS` separates records with `;`; one record per line here.
TOKENS = ";".join(
    [
        "ops:read,profiles:write,apply:ops-secret",
        "root:read,profiles:write,apply,admin:root-secret",
    ]
)
OPS = {"authorization": "Bearer ops-secret"}
ROOT = {"authorization": "Bearer root-secret"}

SPARE = {"name": "spare", "kind": "openai_compat", "base_url": "http://127.0.0.1:1"}
#: The same connector as a bundle carries it: every field, spelled out.
BUNDLED = {**SPARE, "token_env": None, "read": True, "write": False, "poll_minutes": 60}


class FakeReply:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class GateBox:
    """Answers gate's `GET /v1/session` for whichever seat the `Cookie` header
    names, so one test can be several people in turn."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def __call__(
        self, url: str, *, headers: dict[str, str] | None = None, timeout: float = 0.0
    ) -> FakeReply:
        sent = dict(headers or {})
        self.calls.append(sent)
        cookie = next((v for k, v in sent.items() if k.lower() == "cookie"), "")
        named = dict(part.strip().split("=", 1) for part in cookie.split(";") if "=" in part)
        seat = SEATS.get(named.get("gate_session", ""))
        if seat is None:
            return FakeReply(401, {"error": {"code": "unauthenticated"}})
        role, email, gate_id = seat
        return FakeReply(
            200,
            {
                "user_id": gate_id,
                "email": email,
                "name": email.split("@")[0],
                "role": role,
                "status": "active",
                "app": "sieve",
            },
        )


@pytest.fixture(autouse=True)
def _reset_gate_cache() -> Any:
    """`auth._gate_cache` is process-global; a seat reused across two tests would
    otherwise inherit the last test's answer."""
    auth._gate_cache.clear()
    yield
    auth._gate_cache.clear()


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("SIEVE_OWNER_EMAIL", "ada@example.test")
    monkeypatch.setenv("SIEVE_GATE_URL", "http://127.0.0.1:8122")
    profiles = tmp_path / "profiles"
    axes = tmp_path / "axes"
    shutil.copytree(REPO / "profiles", profiles)
    shutil.copytree(REPO / "data" / "axes", axes)
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(axes), profiles=str(profiles)),
        sources={"aa_llm": SourceConfig(name="aa_llm", modalities=["llm"])},
    )


@pytest.fixture
def client(box: Config, monkeypatch: pytest.MonkeyPatch) -> Any:
    stub = GateBox()
    monkeypatch.setattr(httpx, "get", stub)
    with TestClient(create_app(box)) as opened:
        opened.gate_stub = stub  # type: ignore[attr-defined]
        yield opened


def be(client: TestClient, who: str) -> dict[str, Any]:
    """Be this person for the rest of the test. Returns `/v1/me`."""
    client.cookies.set("gate_session", who)
    me = client.get("/v1/me")
    assert me.status_code == 200, me.text
    return dict(me.json())


def refusals(client: TestClient, connector_id: str) -> None:
    """Every way of managing an existing connector, from a seat without `admin`.

    (`test` and `pull` are in here on purpose: both make this box reach an
    arbitrary `base_url` on demand, carrying the token that connector names.)
    """
    for method, path, kwargs in (
        ("put", f"/v1/connectors/{connector_id}", {"json": {"read": False}}),
        ("delete", f"/v1/connectors/{connector_id}", {}),
        ("post", f"/v1/connectors/{connector_id}/test", {}),
        ("post", f"/v1/connectors/{connector_id}/pull", {}),
    ):
        answer = getattr(client, method)(path, **kwargs)
        assert answer.status_code == 403, (method, path, answer.text)
        assert answer.json()["error"]["code"] == "forbidden"
        assert "admin" in answer.json()["error"]["message"]


def owner_makes_one(client: TestClient) -> str:
    """Ada adds `spare`, so a refusal has something real to fail on."""
    be(client, "ada")
    made = client.post("/v1/connectors", json=SPARE)
    assert made.status_code == 200, made.text
    return str(made.json()["id"])


# --------------------------------------------------------------------------- #
# who may manage a connector
# --------------------------------------------------------------------------- #


def test_the_owner_may_manage_a_connector(client: TestClient) -> None:
    """The gate owner's role carries `admin`, so the whole route works.

    200 rather than 201 on create: this route has always answered the row it
    stored rather than a `Location` (CONTRACTS section 6). A01 does not move it.
    """
    connector_id = owner_makes_one(client)

    updated = client.put(f"/v1/connectors/{connector_id}", json={"read": False})
    assert updated.status_code == 200, updated.text
    assert updated.json()["read"] is False

    tested = client.post(f"/v1/connectors/{connector_id}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is False  # nothing is listening on port 1

    assert client.delete(f"/v1/connectors/{connector_id}").status_code == 200
    assert client.get("/v1/connectors").json() == []


def test_a_member_may_not_manage_a_connector(client: TestClient) -> None:
    connector_id = owner_makes_one(client)

    be(client, "mem")
    refused = client.post("/v1/connectors", json={**SPARE, "name": "theirs"})
    assert refused.status_code == 403, refused.text

    refusals(client, connector_id)

    # Nothing a member did landed: her own list is empty and the owner's row is
    # exactly as it was.
    assert client.get("/v1/connectors").json() == []
    be(client, "ada")
    held = client.get("/v1/connectors").json()
    assert [(c["name"], c["read"]) for c in held] == [("spare", True)]

    # What she could do before A01, she still can: her own profile, and an
    # `apply` -- the router was configured by the owner, not by her.
    be(client, "mem")
    assert client.put("/v1/profiles/judge/settings", json={"ship": 3}).status_code == 200
    assert client.post("/v1/profiles/judge/apply").status_code != 403


def test_a_viewer_may_not_manage_a_connector(client: TestClient) -> None:
    connector_id = owner_makes_one(client)

    be(client, "view")
    refused = client.post("/v1/connectors", json={**SPARE, "name": "theirs"})
    assert refused.status_code == 403, refused.text

    refusals(client, connector_id)

    # gate v2's viewer keeps `profiles:write` on her own rows, and only that.
    assert client.put("/v1/profiles/judge/settings", json={"ship": 3}).status_code == 200
    assert client.post("/v1/profiles/judge/apply").status_code == 403


def test_a_configured_token_keeps_the_scopes_it_was_given(client: TestClient) -> None:
    """`SIEVE_TOKENS` records and minted tokens are not role-derived.

    The role table grants the owner's *seat* `admin`; a script token that
    managed connectors before A01 keeps exactly the scopes it was minted with
    and must be given `admin` explicitly. Same route, two tokens, two answers.
    """
    refused = client.post("/v1/connectors", json=SPARE, headers=OPS)
    assert refused.status_code == 403, refused.text
    assert "admin" in refused.json()["error"]["message"]

    allowed = client.post("/v1/connectors", json=SPARE, headers=ROOT)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["name"] == "spare"

    # Everything else the ops token could do, it still does.
    settings = client.put("/v1/profiles/judge/settings", json={"ship": 3}, headers=OPS)
    assert settings.status_code == 200
    assert client.get("/v1/connectors", headers=OPS).status_code == 200


# --------------------------------------------------------------------------- #
# the whole-configuration bundle
# --------------------------------------------------------------------------- #


def test_a_bundle_that_changes_a_connector_is_refused_whole(client: TestClient) -> None:
    """Refused whole, not connector by connector, and `dry_run` included.

    The rest of the document is refused with it: half a bundle landing would be
    worse than none of it.
    """
    be(client, "mem")
    document = client.get("/v1/config").json()
    assert document["connectors"] == []
    document["connectors"] = [dict(BUNDLED)]
    document["cost_multipliers"]["cc"] = 0.1

    for query in ("", "?dry_run=1"):
        refused = client.put(f"/v1/config{query}", json=document)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "not_allowed"
        assert refused.json()["error"]["message"] == "changing connectors needs the admin scope"

    after = client.get("/v1/config").json()
    assert after["connectors"] == []
    assert after["cost_multipliers"].get("cc") is None  # the multiplier did not land either


def test_a_bundle_that_leaves_the_connectors_alone_needs_no_admin(client: TestClient) -> None:
    """The export/edit/import round trip of everything else keeps working."""
    be(client, "mem")
    document = client.get("/v1/config").json()
    document["cost_multipliers"]["cc"] = 0.1

    applied = client.put("/v1/config", json=document)
    assert applied.status_code == 200, applied.text
    assert [change["path"] for change in applied.json()["diff"]] == ["cost_multipliers/cc"]
    assert client.get("/v1/config").json()["cost_multipliers"]["cc"] == 0.1


def test_a_bundle_may_change_a_connector_with_admin(client: TestClient) -> None:
    """The gate owner, through the same route, does get to."""
    be(client, "ada")
    document = client.get("/v1/config").json()
    document["connectors"] = [dict(BUNDLED)]

    applied = client.put("/v1/config", json=document)
    assert applied.status_code == 200, applied.text
    assert client.get("/v1/connectors").json()[0]["name"] == "spare"


# --------------------------------------------------------------------------- #
# two owners, one name
# --------------------------------------------------------------------------- #


def test_a_second_owner_holding_the_same_name_leaves_the_first_row_alone(
    client: TestClient, box: Config
) -> None:
    """Names are unique per owner (migration 0013), never globally.

    A bundle names a connector; it does not address somebody else's row, and
    there is no rule here that would make the second owner's `spare` a conflict.
    """
    ada_connector = owner_makes_one(client)
    ada_held = client.get("/v1/connectors").json()

    bo = be(client, "bo")
    assert bo["email"] == "bo@example.test"
    document = client.get("/v1/config").json()
    assert document["connectors"] == []  # his own rows: none
    document["connectors"] = [{**BUNDLED, "base_url": "http://127.0.0.1:2"}]

    applied = client.put("/v1/config", json=document)
    assert applied.status_code == 200, applied.text
    assert [c["base_url"] for c in client.get("/v1/connectors").json()] == ["http://127.0.0.1:2"]

    # Ada's row of the same name is untouched, and her list is what it was.
    be(client, "ada")
    assert client.get("/v1/connectors").json() == ada_held
    assert ada_held[0]["id"] == ada_connector

    # Two rows, one name, two owners -- which is the schema's rule, not this
    # route's. Cookies are dropped so the query is about the table itself.
    with sqlite3.connect(box.db_path) as db:
        rows = db.execute("SELECT name, owner_id FROM connectors ORDER BY owner_id").fetchall()
        index = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='connectors_owner_name'"
        ).fetchone()
    assert [r[0] for r in rows] == ["spare", "spare"]
    assert None not in {r[1] for r in rows}
    assert len({r[1] for r in rows}) == 2
    assert index is not None and "IFNULL(owner_id,'')" in index[0]
