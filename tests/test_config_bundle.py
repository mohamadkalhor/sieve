from __future__ import annotations

import copy
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import Connector, Observation, SourceConfig
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent
AUTH = {"authorization": "Bearer secret"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIEVE_TOKENS", "agent:read,profiles:write:secret")
    profiles = tmp_path / "profiles"
    axes = tmp_path / "axes"
    shutil.copytree(REPO / "profiles", profiles)
    shutil.copytree(REPO / "data" / "axes", axes)
    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path="sieve.db"),
        paths=Paths(axes=str(axes), profiles=str(profiles)),
        sources={"aa_llm": SourceConfig(name="aa_llm", modalities=["llm"])},
    )
    store = Store(cfg.db_path)
    now = datetime.now(UTC)
    store.add_observations(
        [
            Observation(
                model_id="test/model",
                modality="llm",
                source="aa_llm",
                field="livecodebench",
                value=1,
                unit="index_0_100",
                observed_at=now,
                pulled_at=now,
            )
        ]
    )
    store.add_connector(
        Connector(
            id="gateway-id",
            name="gateway",
            kind="openai_compat",
            base_url="http://127.0.0.1:9999",
            token_env="GATEWAY_TOKEN",
        )
    )
    store.close()
    with TestClient(create_app(cfg)) as opened:
        yield opened


def test_export_dry_run_import_and_reexport(client: TestClient) -> None:
    original = client.get("/v1/config", headers=AUTH)
    assert original.status_code == 200
    document = original.json()
    judge = next(profile for profile in document["profiles"] if profile["name"] == "judge")
    axis = next(iter(judge["settings"]["weights"]))
    before = judge["settings"]["weights"][axis]["value"]
    after = before / 2 if before else 0.1
    judge["settings"]["weights"][axis]["value"] = after
    judge["model_status"]["test/model"] = {"status": "pinned", "pin_order": 1}
    document["cost_multipliers"]["cc"] = 0.1

    preview = client.put("/v1/config?dry_run=1", headers=AUTH, json=document)
    assert preview.status_code == 200, preview.text
    assert preview.json() == [
        {
            "path": "cost_multipliers/cc",
            "before": None,
            "after": 0.1,
        },
        {
            "path": "profiles/judge/model_status/test/model",
            "before": None,
            "after": {"status": "pinned", "pin_order": 1},
        },
        {
            "path": f"profiles/judge/settings/weights/{axis}/value",
            "before": before,
            "after": after,
        },
    ]
    assert client.get("/v1/config", headers=AUTH).json()["cost_multipliers"].get("cc") is None

    applied = client.put("/v1/config", headers=AUTH, json=document)
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == 3
    exported = client.get("/v1/config", headers=AUTH).json()
    document["exported_at"] = exported["exported_at"]
    assert exported == document


def test_invalid_import_is_atomic(client: TestClient) -> None:
    before = client.get("/v1/config", headers=AUTH).json()
    invalid = copy.deepcopy(before)
    judge = next(profile for profile in invalid["profiles"] if profile["name"] == "judge")
    axis = next(iter(judge["settings"]["weights"]))
    judge["settings"]["weights"][axis]["value"] = 2
    response = client.put("/v1/config", headers=AUTH, json=invalid)
    assert response.status_code == 400
    after = client.get("/v1/config", headers=AUTH).json()
    before["exported_at"] = after["exported_at"]
    assert after == before


def test_unknown_keys_and_guide(client: TestClient) -> None:
    document = client.get("/v1/config", headers=AUTH).json()
    document["secret"] = "must not be ignored"
    assert client.put("/v1/config?dry_run=1", headers=AUTH, json=document).status_code == 400
    guide = client.get("/v1/guide", headers=AUTH)
    assert guide.status_code == 200
    assert guide.headers["content-type"].startswith("text/markdown")
    assert "sieve://operating-guide" in guide.text
