"""Every profile write uses effective weights and the same validation rules."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api_acceptance import TOKENS
from test_api_acceptance import workspace as workspace

from sieve.api.app import create_app
from sieve.config import Config
from sieve.profiles import control


@pytest.mark.parametrize("door", ["post", "put", "patch", "settings"])
@pytest.mark.parametrize(
    ("weights", "message"),
    [
        ({"missing_axis": 1.0}, "missing_axis"),
        ({"quality": 1.0}, "quality"),  # Exists for media, not for this LLM profile.
        ({"cost": 0.2}, "sum"),
        ({"cost": 1.2, "reasoning": -0.2}, "cost"),
    ],
)
def test_all_doors_reject_invalid_weights(
    workspace: Config, door: str, weights: dict[str, float], message: str
) -> None:
    with TestClient(create_app(workspace)) as client:
        client.headers["Authorization"] = f"Bearer {TOKENS.split(';')[0].rsplit(':', 1)[1]}"
        original = client.get("/v1/profiles/judge").json()
        settings = client.get("/v1/profiles/judge/settings").json()
        if door == "post":
            response = client.post(
                "/v1/profiles", json={"name": "copy", "from": "judge", "weights": weights}
            )
            assert client.get("/v1/profiles/copy").status_code == 404
        elif door == "put":
            response = client.put("/v1/profiles/judge", json={**original, "weights": weights})
        elif door == "patch":
            response = client.patch("/v1/profiles/judge/weights", json=weights)
        else:
            response = client.put(
                "/v1/profiles/judge/settings",
                json={
                    "weights": {a: {"value": w} for a, w in weights.items()},
                    "remove_axes": list(set(settings["weights"]) - set(weights)),
                },
            )
        assert response.status_code == 400, response.text
        assert message in response.json()["error"]["message"]
        assert client.get("/v1/profiles/judge").json() == original
        assert client.get("/v1/profiles/judge/settings").json() == settings


@pytest.mark.parametrize("removal", ["null", "remove_axes"])
def test_settings_are_the_document_and_copy_truth(workspace: Config, removal: str) -> None:
    app = create_app(workspace)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKENS.split(';')[0].rsplit(':', 1)[1]}"
        original = client.get("/v1/profiles/judge").json()
        patch: dict[str, Any] = {"weights": {"cost": {"value": 1.0, "locked": True}}}
        if removal == "null":
            patch["weights"].update(dict.fromkeys(set(original["weights"]) - {"cost"}))
        else:
            patch["remove_axes"] = list(set(original["weights"]) - {"cost"})
        response = client.put("/v1/profiles/judge/settings", json=patch)
        assert response.status_code == 200, response.text
        assert response.json()["weights"]["cost"]["locked"] is True
        assert client.get("/v1/profiles/judge").json()["weights"] == {"cost": 1.0}
        listed = {p["name"]: p for p in client.get("/v1/profiles").json()}
        assert listed["judge"]["weights"] == {"cost": 1.0}
        for key in ("from", "copy_from"):
            copied = client.post("/v1/profiles", json={"name": f"copy-{key}", key: "judge"})
            assert copied.status_code == 200, copied.text
            assert copied.json()["weights"] == {"cost": 1.0}
        # No cached ranking: the fallback must use settings, not the seed YAML.
        ranking = client.get("/v1/rankings/judge")
        assert ranking.status_code == 200
        for row in ranking.json()["ranks"]:
            for axis in row["axes"]:
                if axis["axis"] != "cost":
                    assert axis["contribution"] == 0
        # Document replacement and PATCH write back to settings, retaining bounds/lock.
        for method in ("put", "patch"):
            weights = {"cost": 0.5, "reasoning": 0.5}
            if method == "put":
                response = client.put("/v1/profiles/judge", json={**original, "weights": weights})
            else:
                response = client.patch("/v1/profiles/judge/weights", json=weights)
            assert response.status_code == 200, response.text
            selected = client.get("/v1/profiles/judge/settings").json()["weights"]
            assert {a: w["value"] for a, w in selected.items()} == weights
            assert selected["cost"]["locked"] is True
        held = control.profile(app.state.store, "judge")
        assert held is not None and held.weights == weights


def test_put_does_not_create(workspace: Config) -> None:
    with TestClient(create_app(workspace)) as client:
        client.headers["Authorization"] = f"Bearer {TOKENS.split(';')[0].rsplit(':', 1)[1]}"
        profile = client.get("/v1/profiles/judge").json()
        response = client.put("/v1/profiles/unknown", json={**profile, "name": "unknown"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        assert "create it with POST /v1/profiles" in response.json()["error"]["message"]
        assert client.get("/v1/profiles/unknown").status_code == 404
