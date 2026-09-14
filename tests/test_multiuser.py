"""AMS-28 -- two people on one box.

Nothing here talks to gate or to a router. Two members are signed in through
the same code path a gate session takes, each mints their own script token, and
every assertion is about what one of them can see, change and ship:

    Ada cannot read Bo's profile and cannot rewrite the shared vocabulary; her
    combos go out as `sieve-<profile>-ada` and only onto the router she owns;
    the gate owner reads both seats and keeps his bare `sieve-<profile>` names.

The stub connector is a fake adapter rather than an HTTP server: what is under
test is which router is written and under what name, not the wire format, which
`tests/test_connectors.py` already covers against a real socket.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sieve import owners
from sieve import tokens as script_tokens
from sieve.api.app import create_app
from sieve.axes import control as axis_control
from sieve.config import Config, Paths, StoreConfig
from sieve.connectors import loop
from sieve.contracts import Chain, ComboResult, Connector
from sieve.profiles import control
from sieve.profiles.load import load_profiles
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 13, tzinfo=UTC)

OWNER_EMAIL = "mohamad@example.test"
ADA = "ada@example.test"
BO = "bo@example.test"

#: The box's own token, which authenticates as the gate owner.
TOKENS = "ops:read,profiles:write,apply:s3cret"


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """A Sieve with the shipped profiles and axes and nobody signed in yet."""
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("SIEVE_OWNER_EMAIL", OWNER_EMAIL)
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
    )


def seats(cfg: Config) -> tuple[Store, owners.User, owners.User, owners.User]:
    """The gate owner and two members, signed in for the first time."""
    store = Store(cfg.db_path)
    axis_control.seed(store, cfg.axes_dir)
    boss = owners.sign_in(store, OWNER_EMAIL, "owner", cfg.profiles_dir)
    control.seed(store, cfg.profiles_dir, boss.id)
    ada = owners.sign_in(store, ADA, "member", cfg.profiles_dir)
    bo = owners.sign_in(store, BO, "member", cfg.profiles_dir)
    return store, boss, ada, bo


def secret_for(store: Store, user: owners.User) -> str:
    _token, secret = script_tokens.mint(store, user.id, "cron", {"read", "profiles:write", "apply"})
    return secret


def auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


class StubRouter:
    """A connector adapter that seats combos in a dict instead of on a gateway."""

    kind = "stub"
    writes = True
    seen: dict[str, list[str]]

    def __init__(self) -> None:
        self.seen = {}

    def list_models(self) -> list[str]:
        return []

    def test(self) -> Any:
        return None

    def put_combo(self, name: str, ordered_ids: list[str]) -> ComboResult:
        self.seen[name] = list(ordered_ids)
        return ComboResult(ok=True, created=True)


def router_for(store: Store, user: owners.User, name: str) -> Connector:
    made = Connector(
        id=uuid.uuid4().hex[:12],
        name=name,
        kind="ninerouter",
        base_url="http://127.0.0.1:1/stub",
        read=True,
        write=True,
        created_at=NOW,
        owner_id=user.id,
    )
    store.add_connector(made)
    return made


def test_a_new_member_starts_with_the_seed_profiles_and_no_router(box: Config) -> None:
    store, _boss, ada, _bo = seats(box)
    seeded = {p.name for p in load_profiles(box.profiles_dir) if p.modality == "llm"}
    mine = control.profiles(store, ada.id, shared=False)

    assert {p.name for p in mine} == seeded
    assert store.connectors(ada.id) == []
    # Copies of the shipped YAML, kept to herself.
    rows = store.db.execute(
        "SELECT visibility FROM profiles WHERE owner_id=?", (ada.id,)
    ).fetchall()
    assert {r["visibility"] for r in rows} == {"private"}


def test_me_answers_as_the_seat_the_call_came_in_on(box: Config) -> None:
    store, _boss, ada, _bo = seats(box)
    ada_secret = secret_for(store, ada)
    store.close()

    with TestClient(create_app(box)) as client:
        mine = client.get("/v1/me", headers=auth(ada_secret))
        assert mine.status_code == 200
        body = mine.json()
        assert body["email"] == ADA and body["role"] == "member" and body["slug"] == "ada"
        assert body["counts"]["connectors"] == 0
        assert body["counts"]["profiles"] >= 9

        boss = client.get("/v1/me", headers=auth("s3cret")).json()
        assert boss["email"] == OWNER_EMAIL and boss["role"] == "owner"


def test_one_member_cannot_read_another_members_profile(box: Config) -> None:
    store, _boss, ada, bo = seats(box)
    ada_secret, bo_secret = secret_for(store, ada), secret_for(store, bo)
    seed = next(p for p in load_profiles(box.profiles_dir) if p.modality == "llm")
    control.put_profile(store, seed.model_copy(update={"name": "bo-secret"}), owner_id=bo.id)
    store.close()

    with TestClient(create_app(box)) as client:
        assert client.get("/v1/profiles/bo-secret", headers=auth(bo_secret)).status_code == 200
        denied = client.get("/v1/profiles/bo-secret", headers=auth(ada_secret))
        assert denied.status_code == 404

        listed = client.get("/v1/profiles", headers=auth(ada_secret)).json()
        assert "bo-secret" not in {p["name"] for p in listed}

        # The owner of the box reads both seats.
        whole = client.get("/v1/profiles", headers=auth("s3cret")).json()
        assert "bo-secret" in {p["name"] for p in whole}


def test_a_member_cannot_rewrite_a_shared_axis(box: Config) -> None:
    store, _boss, ada, _bo = seats(box)
    ada_secret = secret_for(store, ada)
    shared = axis_control.axes(store)[0]
    store.close()
    body = shared.model_dump(mode="json")

    with TestClient(create_app(box)) as client:
        refused = client.put(f"/v1/axes/{shared.name}", json=body, headers=auth(ada_secret))
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "shared_axis"
        # The same write from the owner is not refused on those grounds.
        his = client.put(f"/v1/axes/{shared.name}", json=body, headers=auth("s3cret"))
        assert his.status_code != 403
        # What a member may do instead is keep an axis of that name of her own.
        hers = client.post(
            "/v1/axes",
            json={**body, "describes": "what Ada means by it"},
            headers=auth(ada_secret),
        )
        # (400 here, not 403: this box has no observations to validate against.
        # What matters is that it is no longer an ownership refusal.)
        assert hers.status_code != 403


def test_a_members_combos_carry_their_name_and_reach_only_their_router(
    box: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sieve.engine import apply_targets

    store, boss, ada, _bo = seats(box)
    ada_router = router_for(store, ada, "ada-gateway")
    boss_router = router_for(store, boss, "house-gateway")

    seated: dict[str, StubRouter] = {}

    def stub(connector: Connector) -> StubRouter:
        return seated.setdefault(connector.name, StubRouter())

    monkeypatch.setattr(loop, "adapter_for", stub)

    chain = Chain(
        profile="judge",
        computed_at=NOW,
        primary="openai/gpt-5-6-sol",
        local={"openai/gpt-5-6-sol": ["openai/gpt-5-6-sol"]},
    )

    results = apply_targets(box, [chain], dry_run=False, actor="test", store=store, owner_id=ada.id)
    assert [r.target for r in results] == ["ada-gateway"]
    assert list(seated["ada-gateway"].seen) == ["sieve-judge-ada"]
    assert "house-gateway" not in seated

    # The owner keeps the bare names already live on his gateway.
    apply_targets(box, [chain], dry_run=False, actor="test", store=store, owner_id=boss.id)
    assert list(seated["house-gateway"].seen) == ["sieve-judge"]
    assert loop.slug_for_connector(store, ada_router) == "ada"
    assert loop.slug_for_connector(store, boss_router) is None


def test_the_run_visits_every_person(box: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    from sieve import runs

    store, _boss, _ada, _bo = seats(box)
    assert [label for label, _ in runs._people(store)] == ["mohamad", "ada", "bo"]

    empty = Store(str(Path(box.root) / "empty.db"))
    assert runs._people(empty) == [("everyone", None)]
    empty.close()


def test_a_token_round_trip_is_mint_use_revoke(box: Config) -> None:
    """What a person does on the Tokens page, from the outside."""
    store, _boss, ada, _bo = seats(box)
    ada_secret = secret_for(store, ada)
    store.close()

    with TestClient(create_app(box)) as client:
        made = client.post(
            "/v1/tokens",
            json={"name": "ada cron", "scopes": ["read"]},
            headers=auth(ada_secret),
        )
        assert made.status_code == 201
        body = made.json()
        secret = body["secret"]

        # It is hers, it works, and it holds only what it was given.
        mine = client.get("/v1/me", headers=auth(secret)).json()
        assert mine["email"] == ADA
        refused = client.post(
            "/v1/tokens", json={"name": "more", "scopes": ["read"]}, headers=auth(secret)
        )
        assert refused.status_code == 403

        listed = client.get("/v1/tokens", headers=auth(ada_secret)).json()
        assert [row["name"] for row in listed] == ["ada cron", "cron"]
        assert not any("secret" in row for row in listed)

        gone = client.delete(f"/v1/tokens/{body['id']}", headers=auth(ada_secret))
        assert gone.status_code == 200
        # A revoked token is gone from the list, and unknown at the door.
        left = client.get("/v1/tokens", headers=auth(ada_secret)).json()
        assert [row["name"] for row in left] == ["cron"]
        assert (
            client.post(
                "/v1/tokens", json={"name": "more", "scopes": ["read"]}, headers=auth(secret)
            ).status_code
            == 401
        )


def test_a_revoked_tokens_name_is_free_again(box: Config) -> None:
    """Minting `cron` twice used to raise on the index and answer 500."""
    store, _boss, ada, _bo = seats(box)
    ada_secret = secret_for(store, ada)
    store.close()

    with TestClient(create_app(box)) as client:
        first = client.post("/v1/tokens", json={"name": "nightly"}, headers=auth(ada_secret))
        assert first.status_code == 201
        again = client.post("/v1/tokens", json={"name": "nightly"}, headers=auth(ada_secret))
        assert again.status_code == 400 and again.json()["error"]["code"] == "bad_token"

        assert (
            client.delete(f"/v1/tokens/{first.json()['id']}", headers=auth(ada_secret)).status_code
            == 200
        )
        reborn = client.post("/v1/tokens", json={"name": "nightly"}, headers=auth(ada_secret))
        assert reborn.status_code == 201
        assert reborn.json()["secret"] != first.json()["secret"]


def test_a_chain_is_served_to_the_person_who_owns_it(box: Config) -> None:
    """`GET /v1/chains/{profile}` looks up the caller's own row.

    Before this, the route asked for the unowned row, so a signed-in owner got
    404 for every chain the run had written under his name -- the profile page
    lost its selected models the moment ownership arrived.
    """
    store, _boss, ada, bo = seats(box)
    ada_secret, bo_secret = secret_for(store, ada), secret_for(store, bo)
    chain = Chain(
        profile="judge",
        computed_at=NOW,
        primary="openai/gpt-5-6-sol",
        local={"openai/gpt-5-6-sol": ["openai/gpt-5-6-sol"]},
    )
    store.put_chain(chain, owner_id=ada.id)
    store.close()

    with TestClient(create_app(box)) as client:
        mine = client.get("/v1/chains/judge", headers=auth(ada_secret))
        assert mine.status_code == 200
        assert mine.json()["primary"] == "openai/gpt-5-6-sol"
        assert client.get("/v1/chains/judge", headers=auth(bo_secret)).status_code == 404
