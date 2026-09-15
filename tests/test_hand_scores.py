"""Models no source scores: listed, scored by hand, and ranked on those scores."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config
from sieve.contracts import Reachable
from sieve.store import Store
from tests.test_api_acceptance import workspace  # noqa: F401  (the fixture)

AUTH = {"Authorization": "Bearer s3cret"}
MYSTERY = "ag/mystery-agent"


def _serve_a_mystery(cfg: Config) -> None:
    store = Store(cfg.db_path)
    store.set_reachable(
        "second",
        [Reachable(inventory="second", local_id=MYSTERY, seen_at=datetime.now(UTC))],
    )
    store.close()


def test_an_unknown_model_is_listed_scored_by_hand_and_then_ranks(
    workspace: Config,  # noqa: F811
) -> None:
    _serve_a_mystery(workspace)
    with TestClient(create_app(workspace)) as client:
        listed = client.get("/v1/unscored").json()
        row = next(r for r in listed if r["local_id"] == MYSTERY)
        assert row["reason"] == "no_match" and row["model_id"] is None

        before = client.post("/v1/profiles/coder/preview", json={}).json()
        assert all(m["id"] != "hand/" + MYSTERY for m in before["pool"])

        denied = client.put("/v1/hand-scores", json={"local_id": MYSTERY, "scores": {"cost": 1}})
        assert denied.status_code == 401
        bad = client.put(
            "/v1/hand-scores",
            json={"local_id": MYSTERY, "scores": {"no_such_axis": 1}},
            headers=AUTH,
        )
        assert bad.status_code == 400
        out_of_range = client.put(
            "/v1/hand-scores", json={"local_id": MYSTERY, "scores": {"cost": 2}}, headers=AUTH
        )
        assert out_of_range.status_code == 400

        everything = {
            axis: 1.0
            for axis in (
                "agentic_coding",
                "cost",
                "agentic_tools",
                "reasoning",
                "long_context",
                "latency",
            )
        }
        put = client.put(
            "/v1/hand-scores",
            json={"local_id": MYSTERY, "scores": everything, "name": "Mystery Agent"},
            headers=AUTH,
        )
        assert put.status_code == 200, put.text
        assert put.json()["model_id"] == "hand/" + MYSTERY

        # a perfect hand score on every weighted axis puts it first, straight
        # away: the preview sees the scores changed after its cached ranking
        after = client.post("/v1/profiles/coder/preview", json={}).json()
        assert after["models"][0]["id"] == "hand/" + MYSTERY
        assert after["models"][0]["name"] == "Mystery Agent"

        again = next(r for r in client.get("/v1/unscored").json() if r["local_id"] == MYSTERY)
        assert again["reason"] == "no_scores" and again["hand"]["cost"] == 1.0

        cleared = client.put(
            "/v1/hand-scores",
            json={"local_id": MYSTERY, "scores": dict.fromkeys(everything)},
            headers=AUTH,
        )
        assert cleared.status_code == 200 and cleared.json()["hand"] == {}
        last = client.post("/v1/profiles/coder/preview", json={}).json()
        assert last["models"][0]["id"] != "hand/" + MYSTERY


def test_a_router_combo_is_not_a_model(workspace: Config) -> None:  # noqa: F811
    store = Store(workspace.db_path)
    store.set_reachable(
        "second", [Reachable(inventory="second", local_id="sieve-coder", seen_at=datetime.now(UTC))]
    )
    store.close()
    with TestClient(create_app(workspace)) as client:
        assert all(r["local_id"] != "sieve-coder" for r in client.get("/v1/unscored").json())


def test_an_unknown_model_can_be_linked_and_pinned_without_a_score(
    workspace: Config,  # noqa: F811
) -> None:
    """Pinning needs a catalogue id; linking gives one and puts it in the pool."""
    _serve_a_mystery(workspace)
    with TestClient(create_app(workspace)) as client:
        before = client.post("/v1/profiles/coder/preview", json={}).json()
        assert any(u["local_id"] == MYSTERY for u in before["unlinked"])
        assert all(isinstance(m["scored"], bool) for m in before["pool"])

        assert client.post("/v1/unscored/link", json={"local_id": MYSTERY}).status_code == 401
        missing = client.post("/v1/unscored/link", json={"local_id": "ag/nope"}, headers=AUTH)
        assert missing.status_code == 404
        linked = client.post(
            "/v1/unscored/link", json={"local_id": MYSTERY, "name": "Mystery"}, headers=AUTH
        )
        assert linked.status_code == 200, linked.text
        model_id = linked.json()["model_id"]
        assert model_id == "hand/" + MYSTERY

        after = client.post("/v1/profiles/coder/preview", json={"pinned": [model_id]}).json()
        assert all(u["local_id"] != MYSTERY for u in after["unlinked"])
        row = next(m for m in after["pool"] if m["id"] == model_id)
        assert row["scored"] is False
        assert after["models"][0]["id"] == model_id and after["models"][0]["pinned"]


def test_axis_values_give_the_scale_a_hand_score_is_on(workspace: Config) -> None:  # noqa: F811
    with TestClient(create_app(workspace)) as client:
        pool = client.post("/v1/profiles/coder/preview", json={}).json()["pool"]
        some = pool[0]["id"]
        body = client.get(f"/v1/axis-values?modality=llm&models={some},no/such").json()
        assert "cost" in body["axes"]
        assert body["models"]["no/such"] is None
        assert all(0.0 <= v <= 1.0 for v in body["models"][some].values())
        assert client.get("/v1/axis-values?modality=llm").status_code == 400
