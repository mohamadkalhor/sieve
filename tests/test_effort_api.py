"""EFFORT.md section 5: a seat's effort through the API, on the shipped fixture.

The fixture gateway reaches `openai/gpt-5-6-sol` (bare, so max) and
`openai/gpt-5-6-sol-xhigh` (an id that names its mode), and AA publishes Sol at
non-reasoning, low, medium, high, xhigh and max -- no `minimal`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.catalog.effort import EFFORT_ORDER
from sieve.config import Config
from sieve.contracts import Chain
from sieve.store import Store
from tests.test_api_acceptance import workspace  # noqa: F401  (the fixture)

BOX = {"Authorization": "Bearer s3cret"}
SOL = "openai/gpt-5-6-sol"
EFFORT_KEYS = {"scored_as", "effort", "effort_how", "family"}


def rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for key in ("models", "next", "blocked", "removed", "pool") for r in body[key]]


def test_preview_scores_at_the_asked_effort(workspace: Config) -> None:  # noqa: F811
    with TestClient(create_app(workspace)) as client:
        plain = client.post("/v1/profiles/coder/preview", json={}).json()
        assert plain["effort"] is None
        sol = next(r for r in plain["pool"] if r["id"] == SOL)
        assert (sol["scored_as"], sol["effort"], sol["effort_how"]) == (SOL, "max", "any")
        assert sol["family"] == SOL

        asked = client.post("/v1/profiles/coder/preview", json={"effort": "medium"})
        assert asked.status_code == 200, asked.text
        body = asked.json()
        assert body["effort"] == "medium"
        assert body["settings"]["effort"] == "medium"
        for row in rows(body):
            assert set(row) >= EFFORT_KEYS
        by_id = {r["id"]: r for r in body["pool"]}
        sol = by_id[SOL]
        assert (sol["scored_as"], sol["effort"], sol["effort_how"]) == (
            f"{SOL}-medium",
            "medium",
            "exact",
        )
        assert sol["local_ids"] == next(r for r in plain["pool"] if r["id"] == SOL)["local_ids"]
        # the router id says xhigh, and the seat does not override it
        xhigh = by_id[f"{SOL}-xhigh"]
        assert (xhigh["scored_as"], xhigh["effort_how"]) == (f"{SOL}-xhigh", "id")
        # medium Sol scores below max Sol
        assert sol["raw"] < next(r for r in plain["pool"] if r["id"] == SOL)["raw"]

        # the stored ranking is the saved settings', still at any
        again = client.post("/v1/profiles/coder/preview", json={}).json()
        assert next(r for r in again["pool"] if r["id"] == SOL)["effort_how"] == "any"


def test_effort_is_saved_logged_and_refused_on_a_media_seat(
    workspace: Config,  # noqa: F811
) -> None:
    with TestClient(create_app(workspace)) as client:
        put = client.put("/v1/profiles/coder/settings", json={"effort": "medium"}, headers=BOX)
        assert put.status_code == 200, put.text
        assert put.json()["effort"] == "medium"
        assert client.get("/v1/profiles/coder/settings").json()["effort"] == "medium"
        assert client.get("/v1/profiles/coder").json()["effort"] == "medium"
        reasons = [d["reason"] for d in client.get("/v1/decisions").json()]
        assert "effort any → medium" in reasons

        bad = client.put("/v1/profiles/coder/settings", json={"effort": "huge"}, headers=BOX)
        assert bad.status_code == 400

        for path, body in (
            ("/v1/profiles/image_general/settings", {"effort": "high"}),
            ("/v1/profiles/image_general/preview", {"effort": "high"}),
        ):
            refused = (
                client.put(path, json=body, headers=BOX)
                if path.endswith("settings")
                else client.post(path, json=body)
            )
            assert refused.status_code == 400, path
            assert refused.json()["error"]["code"] == "bad_settings"
            assert "llm seats only" in refused.text

        # the write-back carries it to the YAML, next to `ship`, and back
        document = client.get("/v1/profiles/coder").json()
        put = client.put("/v1/profiles/coder", json=document, headers=BOX)
        assert put.status_code == 200, put.text

    file = workspace.profiles_dir / "llm" / "coder.yaml"
    text = file.read_text(encoding="utf-8")
    assert "\nship: 5\neffort: medium\n" in text
    from sieve.profiles.load import load_profiles

    loaded = {p.name: p for p in load_profiles(workspace.profiles_dir)}
    assert loaded["coder"].effort == "medium"


def test_seats_and_chains_carry_the_effort(workspace: Config) -> None:  # noqa: F811
    store = Store(workspace.db_path)
    try:
        store.put_chain(Chain(profile="coder", computed_at=datetime.now(UTC), primary=SOL))
    finally:
        store.close()
    with TestClient(create_app(workspace)) as client:
        seat = next(r for r in client.get("/v1/seats").json() if r["name"] == "coder")
        assert seat["effort"] is None
        assert seat["multi_mode"] is True
        client.put("/v1/profiles/coder/settings", json={"effort": "high"}, headers=BOX)
        seat = next(r for r in client.get("/v1/seats").json() if r["name"] == "coder")
        assert seat["effort"] == "high"
        chain = client.get("/v1/chains/coder").json()
        assert chain["primary"] == SOL
        assert chain["effort"] == "high"


def test_the_model_card_ladder(workspace: Config) -> None:  # noqa: F811
    with TestClient(create_app(workspace)) as client:
        bare = client.get("/v1/model-card", params={"id": SOL, "modality": "llm"}).json()
        ladder = bare["ladder"]
        assert [e["effort"] for e in ladder] == [e for e in EFFORT_ORDER if e not in ("minimal",)]
        assert all(e["published"] and e["score"] is None and not e["here"] for e in ladder)
        assert next(e for e in ladder if e["effort"] == "max")["id"] == SOL
        assert next(e for e in ladder if e["effort"] == "max")["reachable"] is True
        assert next(e for e in ladder if e["effort"] == "low")["reachable"] is False
        assert all(e["intelligence"] is not None for e in ladder)

        client.put("/v1/profiles/coder/settings", json={"effort": "minimal"}, headers=BOX)
        card = client.get(
            "/v1/model-card", params={"id": SOL, "modality": "llm", "seat": "coder"}
        ).json()
        ladder = card["ladder"]
        assert [e["effort"] for e in ladder] == list(EFFORT_ORDER)
        minimal = next(e for e in ladder if e["effort"] == "minimal")
        assert minimal == {
            "effort": "minimal",
            "id": None,
            "published": False,
            "reachable": False,
            "score": None,
            "intelligence": None,
            "here": False,
        }
        # minimal is not published: the seat is scored on the nearest below
        assert [e["effort"] for e in ladder if e["here"]] == ["non-reasoning"]
        scored = [e["score"] for e in ladder if e["published"]]
        assert all(s is not None for s in scored)
        by_effort = {e["effort"]: e["score"] for e in ladder if e["published"]}
        assert by_effort["max"] > by_effort["low"]

        store = Store(workspace.db_path)
        try:
            single = next(i for i, _, mode in store.effort_rows("llm") if mode is None)
        finally:
            store.close()
        one = client.get("/v1/model-card", params={"id": single, "modality": "llm"})
        assert one.status_code == 200
        assert one.json()["ladder"] == []
