"""AMS-29 — the inventory is the last pull, and two patches that were missing.

Three things are asserted here, and the first is the expensive one.

**A pull is the whole truth about that inventory.** Sieve reads its ids from a
gateway, and a gateway keeps advertising a provider that has gone dark. While
the row for a dead id stayed in `reachable` looking exactly like a live one,
chains resolved through it and the hourly run seated it in a combo again --
which is how 37 ids from a provider that stopped answering on 2026-09-13 were
still being shipped that evening. So the pull that follows retires what it did
not list: the row survives for the history, marked `stale`, and every reader
that answers "where can traffic go" sees only fresh rows.

The other two are small holes in the profile API: an axis could be given a
weight but never taken off a profile, and a description could not be fixed
without PUTting the entire profile back.

Nothing here reaches the network. The store tests build rows by hand; the API
tests stand up a real app over a temporary database and the shipped profiles.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import Reachable
from sieve.profiles import control
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 13, 21, 3, tzinfo=UTC)
HEADERS = {"Authorization": "Bearer s3cret"}
TOKENS = "ops:read,profiles:write,apply,telemetry:s3cret"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(str(tmp_path / "sieve.db"))


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A real app with the shipped profiles and axes, and no source at all."""
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)
    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "api.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
    )
    return TestClient(create_app(cfg))


def _row(
    local_id: str, model_id: str | None, at: datetime, inventory: str = "gateway"
) -> Reachable:
    return Reachable(inventory=inventory, local_id=local_id, model_id=model_id, seen_at=at)


def _any_profile(client: TestClient) -> str:
    """Whichever profile the shipped set happens to lead with."""
    names = [p["name"] for p in client.get("/v1/profiles").json()]
    assert names, "the shipped profiles directory seeded nothing"
    return str(names[0])


# --------------------------------------------------------------------------- #
# 1. inventory = last pull
# --------------------------------------------------------------------------- #


def test_a_pull_retires_the_ids_it_no_longer_lists(store: Store) -> None:
    """The dead provider's ids leave the inventory; the rows stay as history."""
    store.set_reachable(
        "gateway",
        [_row("oc-go/deepseek-flash", "deepseek/v4-flash", NOW), _row("ocg/glm-5.3", "z/glm", NOW)],
        connector_id="c1",
    )
    assert {r.local_id for r in store.reachable()} == {"oc-go/deepseek-flash", "ocg/glm-5.3"}

    later = NOW + timedelta(hours=1)
    store.set_reachable("gateway", [_row("ocg/glm-5.3", "z/glm", later)], connector_id="c1")

    assert [r.local_id for r in store.reachable()] == ["ocg/glm-5.3"]
    kept = {r.local_id: r for r in store.reachable(include_stale=True)}
    assert kept["oc-go/deepseek-flash"].stale is True
    assert kept["oc-go/deepseek-flash"].seen_at == NOW, "history keeps when it was last there"
    assert kept["ocg/glm-5.3"].stale is False


def test_a_chain_cannot_resolve_through_a_retired_local_id(store: Store) -> None:
    """`local_ids` is a routing instruction, so it never offers a stale row."""
    store.set_reachable(
        "gateway",
        [_row("oc-go/glm-5.3", "z/glm", NOW), _row("ocg/glm-5.3", "z/glm", NOW)],
        connector_id="c1",
    )
    assert store.local_ids()["z/glm"] == ["oc-go/glm-5.3", "ocg/glm-5.3"]

    store.set_reachable("gateway", [_row("ocg/glm-5.3", "z/glm", NOW)], connector_id="c1")
    assert store.local_ids()["z/glm"] == ["ocg/glm-5.3"]


def test_a_returning_id_stops_being_stale(store: Store) -> None:
    """A provider that comes back is routable again, without a second row."""
    store.set_reachable("gateway", [_row("ocg/glm-5.3", "z/glm", NOW)], connector_id="c1")
    store.set_reachable("gateway", [], connector_id="c1")
    assert store.reachable() == []

    back = NOW + timedelta(hours=2)
    store.set_reachable("gateway", [_row("ocg/glm-5.3", "z/glm", back)], connector_id="c1")
    rows = store.reachable(include_stale=True)
    assert len(rows) == 1
    assert rows[0].stale is False
    assert rows[0].seen_at == back


def test_one_dark_connector_does_not_retire_another_routers_ids(store: Store) -> None:
    """Staleness is per inventory. Two routers are two independent truths."""
    store.set_reachable("gateway", [_row("ocg/glm-5.3", "z/glm", NOW)], connector_id="c1")
    store.set_reachable(
        "spare", [_row("sp/glm-5.3", "z/glm", NOW, inventory="spare")], connector_id="c2"
    )

    store.set_reachable("gateway", [], connector_id="c1")

    assert [r.local_id for r in store.reachable()] == ["sp/glm-5.3"]
    assert store.reachable_for("c1") == []
    assert [r.local_id for r in store.reachable_for("c1", include_stale=True)] == ["ocg/glm-5.3"]


def test_a_retired_prefix_stops_being_offered_as_a_cost_multiplier(store: Store) -> None:
    """A knob wired to nothing is worse than no knob."""
    store.set_reachable(
        "gateway",
        [_row("oc-go/glm-5.3", "z/glm", NOW), _row("ocg/glm-5.3", "z/glm", NOW)],
        connector_id="c1",
    )
    assert set(control.live_multipliers(store)) == {"oc-go", "ocg"}

    control.put_multipliers(store, {"oc-go": 0.25})
    store.set_reachable("gateway", [_row("ocg/glm-5.3", "z/glm", NOW)], connector_id="c1")

    assert set(control.live_multipliers(store)) == {"ocg"}
    assert set(control.multipliers(store)) == {"oc-go", "ocg"}, "export still carries it"
    held = store.db.execute(
        "SELECT multiplier FROM cost_multipliers WHERE prefix='oc-go'"
    ).fetchone()
    assert held["multiplier"] == 0.25, "the number the operator set is kept, just not listed"


def test_the_inventory_endpoint_hides_stale_rows_and_can_be_asked_for_them(
    client: TestClient,
) -> None:
    with client:
        store = client.app.state.store  # type: ignore[attr-defined]
        store.set_reachable(
            "dead-router",
            [_row("oc-go/gone", "z/gone", NOW, inventory="dead-router")],
            connector_id="cx",
        )
        store.set_reachable("dead-router", [], connector_id="cx")

        live = client.get("/v1/inventory").json()
        assert not [r for r in live if r["local_id"].startswith("oc-go/")]

        everything = client.get("/v1/inventory?include_stale=true").json()
        gone = [r for r in everything if r["local_id"] == "oc-go/gone"]
        assert gone and gone[0]["stale"] is True


# --------------------------------------------------------------------------- #
# 5a. an axis can be taken off a profile
# --------------------------------------------------------------------------- #


def test_a_null_weight_drops_the_axis(client: TestClient) -> None:
    with client:
        name = _any_profile(client)
        before = client.get(f"/v1/profiles/{name}/settings").json()["weights"]
        axis = next(iter(before))

        after = client.put(
            f"/v1/profiles/{name}/settings",
            json={"weights": {axis: None}},
            headers=HEADERS,
        )
        assert after.status_code == 400  # Removal alone would leave an invalid sum.
        remaining = {a: {"value": 1 / (len(before) - 1)} for a in before if a != axis}
        after = client.put(
            f"/v1/profiles/{name}/settings",
            json={"weights": {axis: None, **remaining}},
            headers=HEADERS,
        )
        assert after.status_code == 200, after.text
        assert axis not in after.json()["weights"]
        assert axis not in client.get(f"/v1/profiles/{name}/settings").json()["weights"]


def test_remove_axes_drops_them_and_leaves_the_rest(client: TestClient) -> None:
    with client:
        name = _any_profile(client)
        before = client.get(f"/v1/profiles/{name}/settings").json()["weights"]
        doomed = list(before)[:2]
        assert len(doomed) == 2, "this profile needs two axes for the test to mean much"

        after = client.put(
            f"/v1/profiles/{name}/settings",
            json={"remove_axes": doomed},
            headers=HEADERS,
        )
        assert after.status_code == 400
        remaining = {
            a: {"value": 1 / (len(before) - len(doomed))} for a in before if a not in doomed
        }
        after = client.put(
            f"/v1/profiles/{name}/settings",
            json={"remove_axes": doomed, "weights": remaining},
            headers=HEADERS,
        )
        assert after.status_code == 200, after.text
        weights = after.json()["weights"]
        assert not set(doomed) & set(weights)
        assert set(weights) == set(before) - set(doomed)


def test_dropping_an_axis_and_setting_another_happen_in_one_call(client: TestClient) -> None:
    with client:
        name = _any_profile(client)
        axes = list(client.get(f"/v1/profiles/{name}/settings").json()["weights"])
        gone, kept = axes[0], axes[1]
        after = client.put(
            f"/v1/profiles/{name}/settings",
            json={
                "weights": {gone: None, kept: 1.0},
                "remove_axes": axes[2:],
            },
            headers=HEADERS,
        )
        assert after.status_code == 200, after.text
        weights = after.json()["weights"]
        assert gone not in weights
        assert weights[kept] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 5b. the description alone
# --------------------------------------------------------------------------- #


def test_patching_the_purpose_changes_the_description_and_nothing_else(
    client: TestClient,
) -> None:
    with client:
        name = _any_profile(client)
        before = client.get(f"/v1/profiles/{name}").json()
        answer = client.patch(
            f"/v1/profiles/{name}", json={"purpose": "the one that thinks"}, headers=HEADERS
        )
        assert answer.status_code == 200, answer.text
        after = answer.json()
        assert after["purpose"] == "the one that thinks"
        assert after["name"] == before["name"]
        assert after["weights"] == before["weights"]
        assert after["modality"] == before["modality"]
        assert client.get(f"/v1/profiles/{name}").json()["purpose"] == "the one that thinks"


def test_a_patch_with_neither_name_nor_purpose_is_refused(client: TestClient) -> None:
    with client:
        answer = client.patch(f"/v1/profiles/{_any_profile(client)}", json={}, headers=HEADERS)
        assert answer.status_code == 400


def test_patching_the_purpose_of_a_profile_that_is_not_there_is_a_404(
    client: TestClient,
) -> None:
    with client:
        answer = client.patch("/v1/profiles/nobody", json={"purpose": "x"}, headers=HEADERS)
        assert answer.status_code == 404
