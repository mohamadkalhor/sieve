"""CONSOLE.md section 4 -- the shapes the console reads, end to end.

`/v1/seats` (4.1), the richer preview row (4.2), `/v1/model-card` (4.3) and the
two status numbers (4.4). Every test runs against the fixture store that
`test_api_acceptance.workspace` builds -- the shipped axes and profiles over the
recorded catalogue -- so the numbers here are the numbers a box would have.

Two of these are contract tests rather than behaviour tests, and both matter
more than the rest: `changes()` is asserted against `tests/fixtures/lineup_diff.json`,
the file the browser suite asserts against too, and the model card's `answer` is
asserted against the list row that opened it. Either one drifting would let two
screens tell one person two different things.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sieve import owners
from sieve import tokens as script_tokens
from sieve.api.app import create_app
from sieve.api.routes import v1
from sieve.config import Config
from sieve.contracts import (
    AxisScore,
    Capability,
    Connector,
    ModelRef,
    ProfileSettings,
    Rank,
    Reachable,
)
from sieve.profiles import hand
from sieve.scoring.select import changes
from sieve.store import Store
from tests.test_api_acceptance import workspace  # noqa: F401  (the fixture)

REPO = Path(__file__).resolve().parent.parent


DIFF = json.loads((REPO / "tests" / "fixtures" / "lineup_diff.json").read_text(encoding="utf-8"))

#: The row `/v1/seats` promises, and the shape `SeatRow` types in `client.ts`.
SEAT_KEYS = {
    "name",
    "modality",
    "purpose",
    "mode",
    "ship",
    "live",
    "lineup",
    "in_step",
    "changes",
    "shipped_at",
}
#: `Listed` in `client.ts`: what `listed()` serialises for every row.
LISTED_KEYS = {
    "id",
    "name",
    "local_ids",
    "score",
    "abilities",
    "lacks",
    "axes",
    "raw",
    "health",
    "factor",
    "confidence",
    "cost_per_task",
    "cost_from",
}
#: and the two the preview route adds to each of them.
ROW_KEYS = LISTED_KEYS | {"pinned", "scored"}
#: `ModelCard` in `client.ts`. The model's own fields ride along with these.
CARD_KEYS = {
    "id",
    "name",
    "creator",
    "modality",
    "effort",
    "family",
    "price",
    "abilities",
    "context_window",
    "served_by",
    "scored",
}
NEEDS = ("vision", "reasoning", "tools", "structured_output")
BOX = {"Authorization": "Bearer s3cret"}

OWNER_EMAIL = "mohamad@example.test"
ADA = "ada@example.test"
BO = "bo@example.test"


def bearer(secret: str) -> dict[str, str]:
    """The one header a minted secret needs."""
    return {"Authorization": f"Bearer {secret}"}


def seats_named(client: TestClient, name: str, secret: str | None = None) -> list[dict[str, Any]]:
    """Every `/v1/seats` row with this name, as `secret` sees them.

    More than one is the gate owner's view of a shared box: `readable()` is
    `1=1` for him, so a name three members share is three rows -- the same
    rows `/v1/profiles` hands him, in the same order.
    """
    rows: list[dict[str, Any]] = client.get(
        "/v1/seats", headers=bearer(secret) if secret else {}
    ).json()
    return [row for row in rows if row["name"] == name]


def seat(client: TestClient, name: str, secret: str | None = None) -> dict[str, Any]:
    """The one `/v1/seats` row with this name, as `secret` sees it."""
    found = seats_named(client, name, secret)
    assert len(found) == 1, f"/v1/seats says {len(found)} things about {name!r}"
    return found[0]


def card(client: TestClient, model_id: str, secret: str | None = None) -> dict[str, Any]:
    """One model card, as `secret` sees it."""
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    response = client.get(
        "/v1/model-card", params={"id": model_id, "modality": "llm"}, headers=headers
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def router(owner_id: str, name: str) -> Connector:
    """A connector row, of the kind a person adds in the UI."""
    return Connector(
        id=f"cn-{name}",
        name=name,
        kind="openai_compat",
        base_url="http://127.0.0.1:20128/v1",
        read=True,
        owner_id=owner_id,
    )


def published(inventory: str, item: Reachable, local_id: str | None = None) -> Reachable:
    """`item` as `inventory` publishes it.

    The local id keeps the router's own prefix, which is why it is rewritten:
    the same model has a different local id on every router. `local_id` names it
    outright, for the case of one router added twice under two names.
    """
    return Reachable(
        inventory=inventory,
        local_id=local_id or f"{inventory}/{item.local_id.split('/', 1)[-1]}",
        model_id=item.model_id,
        capability=item.capability,
        seen_at=item.seen_at,
    )


def secret_for(store: Store, user: owners.User) -> str:
    """A read token for one person, the way the console's own login mints one."""
    _token, secret = script_tokens.mint(store, user.id, "console", {"read"})
    return secret


@pytest.fixture
def three_seats(
    workspace: Config,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    """The fixture box with three seats, a router each, and one router twice.

    The fixture's own inventory rows have no connector behind them -- they were
    pulled by name -- so they belong to nobody, and the gate owner sees them the
    way `store.local_ids` hands them to anybody. Ada and Bo get connectors of
    their own, and Ada's gateway is added a second time under another name, so
    one local id is published by two inventories. A later pull of her gateway
    drops one id, which is what leaves a stale row behind.
    """
    monkeypatch.setenv("SIEVE_OWNER_EMAIL", OWNER_EMAIL)
    store = Store(workspace.db_path)
    boss = owners.sign_in(store, OWNER_EMAIL, "owner", workspace.profiles_dir)
    ada = owners.sign_in(store, ADA, "member", workspace.profiles_dir)
    bo = owners.sign_in(store, BO, "member", workspace.profiles_dir)

    box = sorted(store.reachable(), key=lambda row: row.local_id)
    assert len(box) >= 3, "the fixture inventory must have matched something"
    store.db.execute(
        "UPDATE reachable SET connector_id=? WHERE inventory='gateway'",
        (store.add_connector(router(boss.id, "house-gateway")).id,),
    )
    ada_gateway = store.add_connector(router(ada.id, "ada-gateway"))
    store.set_reachable(
        "ada-gateway", [published("ada-gateway", row) for row in box[:3]], ada_gateway.id
    )
    ada_alias = store.add_connector(router(ada.id, "ada-alias"))
    store.set_reachable(
        "ada-alias", [published("ada-alias", box[0], box[0].local_id)], ada_alias.id
    )
    bo_gateway = store.add_connector(router(bo.id, "bo-gateway"))
    store.set_reachable("bo-gateway", [published("bo-gateway", box[0])], bo_gateway.id)
    # a later pull that no longer lists the third id: the row stays, stale
    store.set_reachable(
        "ada-gateway", [published("ada-gateway", row) for row in box[:2]], ada_gateway.id
    )

    seats = {"boss": "s3cret", "ada": secret_for(store, ada), "bo": secret_for(store, bo)}
    store.close()
    return seats


# --------------------------------------------------------------------------- #
# 4.1 the diff, the seats list
# --------------------------------------------------------------------------- #


def test_the_shared_diff_fixture_is_what_the_server_computes() -> None:
    """The eight arithmetic cases, from the file the browser reads too."""
    cases = DIFF["cases"]
    assert len(cases) == 8, "CONSOLE.md section 4.1 pins eight cases"
    assert {case["name"] for case in cases} == {
        "identical",
        "both empty",
        "one added at the end",
        "one removed",
        "two adjacent ids swapped",
        "reversed, so the middle one never moved",
        "no id in common",
        "one leaves and two swap",
    }
    for case in cases:
        live, lineup = case["live"], case["lineup"]
        counted = changes(live, lineup)
        assert counted == case["changes"], case["name"]
        assert (counted == 0) == case["in_step"], case["name"]
        # "how far apart" is one question, not two: the same from either side
        assert changes(lineup, live) == counted, case["name"]
        assert len(set(live)) == len(live) and len(set(lineup)) == len(lineup), case["name"]


def test_the_diff_counts_added_removed_and_moved_apart() -> None:
    """The three parts, one at a time, so a regression names itself."""
    base = ["a", "b", "c", "d"]
    assert changes(base, base) == 0
    assert changes(base, [*base, "e"]) == 1, "one added"
    assert changes(base, base[:-1]) == 1, "one removed"
    assert changes(base, ["b", "a", "c", "d"]) == 2, "two ids changed places"
    assert changes(base, ["e", "f", "g", "h"]) == 8, "nothing in common"
    assert changes(base, ["c", "b", "a", "d"]) == 2, "only the ends moved"


def test_seats_answers_every_profile_in_one_request(workspace: Config) -> None:  # noqa: F811
    """One row per profile, with nothing stored yet reading as nothing."""
    app = create_app(workspace)
    with TestClient(app) as client:
        profiles = client.get("/v1/profiles").json()
        response = client.get("/v1/seats")
        assert response.status_code == 200, response.text
        rows = response.json()

    assert [row["name"] for row in rows] == [profile["name"] for profile in profiles]
    assert rows, "the fixture box ships profiles"
    for row in rows:
        assert set(row) == SEAT_KEYS
        assert row["mode"] in ("auto", "manual")
        assert row["ship"] >= 1
        assert row["purpose"], "a seat says what it is for"
        # nothing has been ranked or shipped: no badge, not a zero
        assert row["live"] is None and row["lineup"] is None
        assert row["in_step"] is None and row["changes"] is None
        assert row["shipped_at"] is None


def test_seats_never_ranks_and_answers_inside_the_budget(
    workspace: Config,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pane is on every page, so it reads and never builds a ranking.

    The catalogue is grown to a thousand models first: `model_names` and the
    capability map are read once per modality, and on a real box that read is
    what the budget in CONSOLE.md 4.1 is about -- 300 ms while a pane waits.
    """
    app = create_app(workspace)
    with TestClient(app) as client:
        # a stored ranking to read: the route is not allowed to make one
        assert client.post("/v1/profiles/coder/preview", json={}).status_code == 200
        store = app.state.store
        store.upsert_models(
            ModelRef(
                id=f"vendor/model-{index}",
                modality="llm",
                name=f"Model {index}",
                creator="vendor",
            )
            for index in range(1000)
        )
        assert store.model_names("llm").get("vendor/model-999") == "Model 999"

        def boom(*_: Any, **__: Any) -> Any:
            raise AssertionError("/v1/seats must not build a ranking")

        monkeypatch.setattr(v1, "rank_profile", boom)
        started = time.perf_counter()
        response = client.get("/v1/seats")
        took = (time.perf_counter() - started) * 1000

    assert response.status_code == 200, response.text
    assert took < 300, f"/v1/seats took {took:.0f} ms on the fixture store"
    coder = next(row for row in response.json() if row["name"] == "coder")
    assert coder["lineup"], "a stored ranking is a lineup the pane can read"


def test_seats_reports_the_stored_chain_against_the_saved_settings(workspace: Config) -> None:  # noqa: F811
    """What was applied, what would ship now, and whether those are one list."""
    app = create_app(workspace)
    with TestClient(app) as client:
        assert client.post("/v1/profiles/coder/preview", json={}).status_code == 200
        row = seat(client, "coder")
        assert row["live"] is None and row["shipped_at"] is None
        assert row["lineup"], "the preview stored a ranking for the pane to read"
        assert row["in_step"] is None, "with no chain there is nothing to be in step with"
        assert row["changes"] is None, "and no count either"

        applied = client.post("/v1/profiles/coder/apply", headers=BOX)
        assert applied.status_code == 200, applied.text
        shipped = [model["id"] for model in applied.json()["models"]]
        assert len(shipped) == row["ship"]

        row = seat(client, "coder")
        assert [model["id"] for model in row["live"]] == shipped
        assert [model["id"] for model in row["lineup"]] == shipped
        assert all(model["name"] and model["name"] != model["id"] for model in row["live"])
        assert row["in_step"] is True and row["changes"] == 0
        assert row["shipped_at"] is not None

        # a shorter ship: the chain is what was applied and does not move
        ship = row["ship"]
        assert (
            client.put("/v1/profiles/coder/settings", json={"ship": 1}, headers=BOX).status_code
            == 200
        )
        row = seat(client, "coder")
        assert [model["id"] for model in row["live"]] == shipped
        assert [model["id"] for model in row["lineup"]] == shipped[:1]
        assert row["in_step"] is False
        assert row["changes"] == len(shipped) - 1

        # and one axis alone, which is the pane's own gesture
        assert (
            client.put("/v1/profiles/coder/settings", json={"ship": ship}, headers=BOX).status_code
            == 200
        )
        weights = client.get("/v1/profiles/coder").json()["weights"]
        reordered = None
        for axis in sorted(weights):
            alone = dict.fromkeys(weights, 0.0)
            alone[axis] = 1.0
            assert (
                client.put(
                    "/v1/profiles/coder/settings", json={"weights": alone}, headers=BOX
                ).status_code
                == 200
            )
            lineup = [model["id"] for model in seat(client, "coder")["lineup"]]
            if lineup != shipped:
                reordered = lineup
                break
        assert reordered is not None, "no single axis moves this profile's list"
        assert len(reordered) == ship, "a weight changes the order, not the ship"
        last = seat(client, "coder")
        assert last["in_step"] is False, "a moved weight is not the chain that shipped"
        assert last["changes"] and last["changes"] >= 1


def test_seats_read_the_callers_own_chain_and_settings(
    workspace: Config,  # noqa: F811
    three_seats: dict[str, str],
) -> None:
    """Two people on one box do not read each other's lineups.

    The chain and the saved settings are stored per owner, so the same profile
    is a different seat to each of them: the gate owner has shipped, Ada has
    not, and her row says so rather than showing his list.
    """
    app = create_app(workspace)
    with TestClient(app) as client:
        assert client.post("/v1/profiles/coder/preview", json={}, headers=BOX).status_code == 200
        assert client.post("/v1/profiles/coder/apply", headers=BOX).status_code == 200
        his_secret = three_seats["boss"]

        # the gate owner reads the whole box: same seats, same order, as the
        # profiles the left pane already lists for him -- duplicates included
        his = client.get("/v1/seats", headers=bearer(his_secret)).json()
        profiles = client.get("/v1/profiles", headers=bearer(his_secret)).json()
        assert [row["name"] for row in his] == [seat_row["name"] for seat_row in profiles]
        assert len(seats_named(client, "coder", his_secret)) > 1, "three owners share a name"
        assert [row for row in his if row["name"] == "coder" and row["live"]], "and one shipped"

        # Ada reads her own seat only, and she has shipped nothing
        hers = seat(client, "coder", three_seats["ada"])
        assert hers["live"] is None and hers["shipped_at"] is None
        assert hers["in_step"] is None and hers["changes"] is None
        assert hers["ship"] >= 1, "her own seeded settings, not his"

        # a call with no token is the gate owner on a box that has one
        assert len(client.get("/v1/seats").json()) == len(his)


def test_seats_says_nothing_when_hand_scores_outdate_the_ranking(workspace: Config) -> None:  # noqa: F811
    """A stale ranking is not a wrong answer, it is no answer.

    `select` would seat a hand-scored model where the cached axis values say it
    belongs, and this route may not rank. So the lineup goes null -- "not known"
    -- rather than showing a list the next preview would not agree with.
    """
    app = create_app(workspace)
    with TestClient(app) as client:
        assert client.post("/v1/profiles/coder/preview", json={}).status_code == 200
        assert seat(client, "coder")["lineup"], "a fresh ranking is readable"

        store = app.state.store
        weights = client.get("/v1/profiles/coder").json()["weights"]
        assert hand.changed_at(store, "llm") is None, "no hand score yet"
        served = sorted(row.local_id for row in store.reachable() if row.model_id)
        assert served, "the fixture box reached something"
        hand.put(store, served[0], "llm", {"cost": 1.0}, set(weights), "test")
        assert hand.changed_at(store, "llm") is not None

        row = seat(client, "coder")
        assert row["live"] is None and row["lineup"] is None
        assert row["in_step"] is None and row["changes"] is None


# --------------------------------------------------------------------------- #
# 4.2 the row a preview draws
# --------------------------------------------------------------------------- #


def test_the_preview_row_carries_every_axis_the_proposed_weights_name(workspace: Config) -> None:  # noqa: F811
    """One bar per weight, in the weights' order, and the two sums that hold."""
    app = create_app(workspace)
    with TestClient(app) as client:
        # a ranking of the profile's own axes, then a weight for an axis no
        # ranking has ever held: a newly added one, cached nowhere
        assert client.post("/v1/profiles/coder/preview", json={}).status_code == 200
        weights = client.get("/v1/profiles/coder").json()["weights"]
        body = client.post(
            "/v1/profiles/coder/preview", json={"weights": {"ghost_axis": 0.25}}
        ).json()
        assert body["settings"]["weights"]["ghost_axis"] == 0.25
        expected = [*weights, "ghost_axis"]

        collections = [body["models"], body["next"], body["blocked"], body["removed"], body["pool"]]
        assert body["models"], "the fixture box seats something"
        for rows in collections:
            for row in rows:
                assert set(row) == ROW_KEYS
                assert [bar["axis"] for bar in row["axes"]] == expected
                assert abs(sum(bar["contribution"] for bar in row["axes"]) - row["raw"]) < 1e-9
                assert abs(row["raw"] * row["health"] * row["factor"] - row["score"]) < 1e-9, (
                    "the inspector's equation has to be the score it explains"
                )
                for bar in row["axes"]:
                    if bar["axis"] == "ghost_axis":
                        assert bar == {
                            "axis": "ghost_axis",
                            "value": None,
                            "coverage": 0.0,
                            "contribution": 0.0,
                        }, "an axis nobody cached is empty, not missing"

        # the cached value of an axis is the value the bar shows
        store = app.state.store
        ranking = store.ranking("coder")
        assert ranking is not None
        cached = {rank.model_id: rank for rank in ranking.ranks}
        for row in body["models"]:
            rank = cached[row["id"]]
            for bar in row["axes"]:
                if bar["axis"] == "ghost_axis":
                    continue
                held = [axis for axis in rank.axes if axis.axis == bar["axis"]]
                assert len(held) == 1
                assert bar["value"] == held[0].value
                assert bar["coverage"] == held[0].coverage
            assert abs(row["score"] - rank.final) < 1e-9
            assert abs(row["raw"] - rank.score) < 1e-9
            assert row["confidence"] == rank.confidence
            assert abs(row["cost_per_task"] - rank.cost_per_task) < 1e-12


def test_a_three_hundred_model_pool_stays_under_the_size_cap(workspace: Config) -> None:  # noqa: F811
    """The pool is the whole reachable catalogue, on every slider move.

    The fixture store reaches a handful of models, so this takes the widest row
    it lists -- the one with the most axes, local ids and witnesses -- and asks
    what three hundred of those would cost. That is the upper end of what a real
    box serialises, and it is the number the cap is about.
    """
    app = create_app(workspace)
    with TestClient(app) as client:
        widest = max(
            client.post("/v1/profiles/coder/preview", json={}).json()["pool"],
            key=lambda row: len(json.dumps(row)),
        )
    axes = len(widest["axes"])
    size = len(json.dumps([widest] * 300))
    assert axes >= 6, "a pool row carries the proposed weights' axes"
    assert size < 400_000, f"300 rows of {axes} axes serialise to {size} bytes"


def test_a_pool_row_carries_no_more_than_the_list_needs() -> None:
    """`listed` is the one row builder, and the pool is not a second shape."""
    rank = Rank(
        position=0,
        model_id="vendor/model",
        reachable=True,
        local_ids=["gateway/model"],
        score=0.5,
        confidence=0.5,
        health=1.0,
        final=0.5,
        axes=[
            AxisScore(axis=f"axis_{index}", value=0.5, coverage=1.0, contribution=0.0)
            for index in range(12)
        ],
        cost_per_task=0.02,
        cost_from="shape",
    )
    proposed = ProfileSettings(weights={f"axis_{index}": 1 / 12 for index in range(12)})
    row = v1.listed(rank, {"vendor/model": "Model"}, None, list(NEEDS), proposed)
    assert set(row) == LISTED_KEYS, "the pool row is the list row"
    assert len(row["axes"]) == 12
    assert row["score"] == rank.final and row["raw"] == rank.score
    assert row["health"] == rank.health and row["factor"] == 1.0
    assert row["cost_from"] == "shape"
    assert row["lacks"] == list(NEEDS), "nothing is known about this model"
    assert row["abilities"] == dict.fromkeys(NEEDS), "an unmeasured need answers null"
    assert row["local_ids"] == ["gateway/model"]


# --------------------------------------------------------------------------- #
# 4.3 the model card
# --------------------------------------------------------------------------- #


def test_the_model_card_agrees_with_the_row_that_opened_it(workspace: Config) -> None:  # noqa: F811
    """Same answer as the list, its witnesses, its price, and 404 for a stranger."""
    app = create_app(workspace)
    with TestClient(app) as client:
        preview = client.post("/v1/profiles/coder/preview", json={}).json()
        rows = preview["models"] + preview["next"]
        assert rows, "the fixture box lists something"
        for listed in rows[:8]:
            body = card(client, listed["id"])
            assert set(body) >= CARD_KEYS
            assert body["id"] == listed["id"] and body["name"] == listed["name"]
            assert body["modality"] == "llm"
            assert set(body["abilities"]) == set(NEEDS), "one entry per need, always"
            for need in NEEDS:
                said = body["abilities"][need]
                assert set(said) == {"answer", "yes", "no"}
                assert said["answer"] == listed["abilities"][need], f"{listed['id']} / {need}"
                for who in said["yes"] + said["no"]:
                    assert who, "a witness has a name"
            assert body["served_by"], f"{listed['id']} is in the pool, so it is reachable"
            for where in body["served_by"]:
                assert where["prefix"] == where["local_id"].split("/", 1)[0]
                assert where["inventory"]

        store = app.state.store
        price = store.latest_prices("llm", only=[rows[0]["id"]]).get(rows[0]["id"])
        assert price is not None, "the fixture catalogue publishes a price"
        shown = card(client, rows[0]["id"])["price"]
        assert shown is not None, "a model the catalogue priced has a price on its card"
        assert shown["unit"] == price.unit and shown["source"] == price.source
        assert shown["input"] == price.input and shown["output"] == price.output
        assert shown["per_unit"] == price.per_unit
        assert datetime.fromisoformat(shown["observed_at"]) == price.observed_at

        missing = client.get("/v1/model-card", params={"id": "nobody/nothing", "modality": "llm"})
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"


def test_the_model_card_says_who_said_so(workspace: Config) -> None:  # noqa: F811
    """No evidence, inventory-only evidence, and a source that says no."""
    app = create_app(workspace)
    with TestClient(app) as client:
        store = app.state.store
        # a model nothing has measured and no router lists
        store.upsert_models(
            [
                ModelRef(
                    id="fixture/no-evidence",
                    modality="llm",
                    name="Fixture No Evidence",
                    creator="fixture",
                )
            ]
        )
        body = card(client, "fixture/no-evidence")
        assert body["context_window"] is None
        assert body["price"] is None
        assert body["served_by"] == []
        assert body["scored"] is False
        for need in NEEDS:
            assert body["abilities"][need] == {"answer": None, "yes": [], "no": []}

        # the same model, listed by the box's own router and nothing else
        store.set_reachable(
            "gateway",
            [
                *store.reachable(),
                Reachable(
                    inventory="gateway",
                    local_id="gateway/fixture-no-evidence",
                    model_id="fixture/no-evidence",
                    capability=Capability(tools=True, context_window=200_000),
                    seen_at=datetime.now(UTC),
                ),
            ],
        )
        body = card(client, "fixture/no-evidence")
        assert body["context_window"] == 200_000
        assert body["abilities"]["tools"] == {
            "answer": True,
            "yes": ["inventory:gateway"],
            "no": [],
        }
        assert body["abilities"]["vision"]["answer"] is None, "a router said nothing about vision"
        assert [where["local_id"] for where in body["served_by"]] == ["gateway/fixture-no-evidence"]

        # and a source that contradicts it is kept, both of them, on the card
        store.set_capabilities(
            "aa_llm",
            "llm",
            {"fixture/no-evidence": Capability(tools=False, input_modalities=["image"])},
        )
        body = card(client, "fixture/no-evidence")
        assert body["abilities"]["tools"] == {
            "answer": False,
            "yes": ["inventory:gateway"],
            "no": ["aa_llm"],
        }, "the source wins the answer, the router is still shown disagreeing"
        assert body["abilities"]["vision"]["yes"] == ["aa_llm"]


def test_the_model_card_scopes_inventory_labels_to_the_caller(
    workspace: Config,  # noqa: F811
    three_seats: dict[str, str],
) -> None:
    """A router's inventory label says what somebody runs, so it is theirs."""
    app = create_app(workspace)
    with TestClient(app) as client:
        store = app.state.store
        served = {row.local_id: row for row in store.reachable() if row.inventory == "gateway"}
        assert len(served) >= 3, "the fixture box reached something"
        first = served[sorted(served)[0]]
        third = served[sorted(served)[2]]

        # the gate owner: the inventory he pulled himself, and nobody else's
        mine = card(client, str(first.model_id), three_seats["boss"])
        assert {where["inventory"] for where in mine["served_by"]} == {"gateway"}

        # Ada: her two inventories, one local id published by both
        hers = card(client, str(first.model_id), three_seats["ada"])
        assert {where["inventory"] for where in hers["served_by"]} == {"ada-gateway", "ada-alias"}
        pairs = [(where["inventory"], where["local_id"]) for where in hers["served_by"]]
        assert len(pairs) == len(set(pairs)), "one row per router, no repeats"
        aliased = [where for where in hers["served_by"] if where["inventory"] == "ada-alias"]
        assert len(aliased) == 1, "the same local id from two inventories is kept, once each"

        # Bo: his own, and none of Ada's
        his = card(client, str(first.model_id), three_seats["bo"])
        assert {where["inventory"] for where in his["served_by"]} == {"bo-gateway"}

        # a router that stopped listing it: still Ada's, still shown, marked
        stale = card(client, str(third.model_id), three_seats["ada"])["served_by"]
        assert [(where["inventory"], where["stale"]) for where in stale] == [("ada-gateway", True)]
        assert card(client, str(third.model_id), three_seats["bo"])["served_by"] == []


# --------------------------------------------------------------------------- #
# 4.4 the two status numbers
# --------------------------------------------------------------------------- #


def test_the_status_numbers_match_the_lists_they_count(workspace: Config) -> None:  # noqa: F811
    """A count that drifted from the list it counts is worse than no count."""
    app = create_app(workspace)
    with TestClient(app) as client:
        store = app.state.store
        before = client.get("/v1/status")
        assert before.status_code == 200, before.text
        assert before.json()["reachable"] == len({row.model_id for row in store.reachable()})
        assert before.json()["unscored"] == len(hand.unscored(store))

        # a router id that matches nothing counts as unscored; one without a
        # slash is a combo and counts as neither
        store.set_reachable(
            "gateway",
            [
                *store.reachable(),
                Reachable(
                    inventory="gateway",
                    local_id="gateway/ghost-model",
                    seen_at=datetime.now(UTC),
                ),
                Reachable(
                    inventory="gateway",
                    local_id="coder-strong",
                    seen_at=datetime.now(UTC),
                ),
            ],
        )
        counted = client.get("/v1/status").json()
        assert counted["unscored"] == len(hand.unscored(store)) == before.json()["unscored"] + 1
        assert counted["reachable"] == before.json()["reachable"], "a ghost is not a model"

        # and a pull that stops listing them leaves the count where the list is
        store.set_reachable(
            "gateway",
            [
                row
                for row in store.reachable()
                if row.local_id not in ("gateway/ghost-model", "coder-strong")
            ],
        )
        back = client.get("/v1/status").json()
        assert back["unscored"] == len(hand.unscored(store)) == before.json()["unscored"]
        assert back["reachable"] == before.json()["reachable"]
