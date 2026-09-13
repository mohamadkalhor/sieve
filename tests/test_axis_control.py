from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import Observation, SourceConfig
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent
TOKEN = {"authorization": "Bearer secret"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIEVE_TOKENS", "ops:profiles:write:secret")
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)
    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path="sieve.db"),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
        sources={"aa_llm": SourceConfig(name="aa_llm", modalities=["llm"])},
    )
    store = Store(cfg.db_path)
    now = datetime.now(UTC)
    store.add_observations(
        [
            Observation(model_id="test/model", modality="llm", source="aa_llm", field=field,
                        value=1, unit="index_0_100", observed_at=now, pulled_at=now)
            for field in ("terminalbench_v2_1", "livecodebench")
        ]
    )
    store.close()
    with TestClient(create_app(cfg)) as opened:
        yield opened


def body(name: str = "test_axis") -> dict[str, object]:
    return {
        "name": name,
        "modality": "llm",
        "label": "Test axis",
        "describes": "a test criterion",
        "fields": [
            {"source": "aa_llm", "field": "terminalbench_v2_1", "weight": 0.6},
            {"source": "aa_llm", "field": "livecodebench", "weight": 0.4},
        ],
        "missing": "renormalise",
        "min_coverage": 0.5,
        "higher_is_better": True,
    }


def test_axes_seed_crud_and_field_catalogue(client: TestClient) -> None:
    seeded = client.get("/v1/axes").json()
    assert len([axis for axis in seeded if axis["modality"] == "llm"]) == 11
    assert all(axis["builtin"] for axis in seeded)
    fields = client.get("/v1/sources/aa_llm/fields").json()
    assert fields == [
        {"field": "livecodebench", "rows": 1},
        {"field": "terminalbench_v2_1", "rows": 1},
    ]

    assert client.post("/v1/axes", json=body()).status_code == 401
    created = client.post("/v1/axes", headers=TOKEN, json=body())
    assert created.status_code == 201
    assert created.json()["fields_count"] == 2
    assert created.json()["builtin"] is False
    assert client.get("/v1/axes/test_axis").json()["label"] == "Test axis"

    changed = body()
    changed["label"] = "Changed"
    updated = client.put("/v1/axes/test_axis", headers=TOKEN, json=changed)
    assert updated.json()["label"] == "Changed"
    assert client.delete("/v1/axes/test_axis", headers=TOKEN).status_code == 200
    assert client.get("/v1/axes/test_axis").status_code == 404
    assert len(client.get("/v1/decisions").json()) == 3


def test_axis_write_validation_and_in_use_delete(client: TestClient) -> None:
    invalid = body("invalid")
    invalid["fields"] = [{"source": "aa_llm", "field": "not_real", "weight": 1}]
    response = client.post("/v1/axes", headers=TOKEN, json=invalid)
    assert response.status_code == 400
    assert "no observations exist" in response.json()["error"]["message"]

    blocked = client.delete("/v1/axes/agentic_coding", headers=TOKEN)
    assert blocked.status_code == 409
    assert "coder" in blocked.json()["profiles"]
    forced = client.delete("/v1/axes/agentic_coding?force=1", headers=TOKEN)
    assert forced.status_code == 200
    assert "coder" in forced.json()["profiles_zeroed"]
