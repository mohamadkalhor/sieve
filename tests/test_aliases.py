"""Alias, source guard and multiplier regressions on an isolated SQLite store."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, StoreConfig
from sieve.contracts import ModelRef, PullResult, Reachable, SourceConfig


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(
        "SIEVE_TOKENS", "editor:profiles:write,apply:fixture-write;reader:read:fixture-read"
    )
    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "test.db")),
        sources={"manual": SourceConfig(name="manual", enabled=False)},
    )
    with TestClient(create_app(cfg)) as value:
        value.app.state.store.upsert_models(  # type: ignore[attr-defined]
            [ModelRef(id="maker/model", modality="llm", name="Model", creator="maker")]
        )
        yield value


WRITE = {"Authorization": "Bearer fixture-write"}


def test_alias_validation_and_undo(client: TestClient) -> None:
    store = client.app.state.store  # type: ignore[attr-defined]
    store.set_reachable(
        "test",
        [Reachable(inventory="test", local_id="gw/a b", model_id=None, seen_at=datetime.now(UTC))],
    )
    body = {"alias": "gw/a b", "model_id": "missing/id", "modality": "llm"}
    result = client.put("/v1/aliases", json=body, headers=WRITE)
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "unknown_model"
    assert "missing/id" in result.json()["error"]["message"]
    body["model_id"] = "maker/model"
    body["modality"] = "text-to-image"
    assert client.put("/v1/aliases", json=body, headers=WRITE).status_code == 400
    assert store.alias_rows() == []
    body["modality"] = "llm"
    assert client.put("/v1/aliases", json=body, headers=WRITE).status_code == 200
    assert client.get("/v1/aliases").json() == [{**body, "origin": "user"}]
    assert client.get("/v1/inventory?unmatched=true").json() == []
    path = "/v1/aliases/" + quote(body["alias"], safe="")
    assert client.delete(path, headers=WRITE).status_code == 200
    assert client.get("/v1/aliases").json() == []
    assert client.get("/v1/inventory?unmatched=true").json()[0]["local_id"] == body["alias"]
    assert client.delete(path, headers=WRITE).status_code == 404


def test_source_alias_delete_is_modality_specific(client: TestClient) -> None:
    store = client.app.state.store  # type: ignore[attr-defined]
    for modality in ("llm", "text-to-image"):
        store.upsert_models(
            [
                ModelRef(
                    id="maker/model",
                    modality=modality,
                    name="Model",
                    creator="maker",
                    aliases=["shared/id"],
                )
            ]
        )
    assert client.delete("/v1/aliases/shared%2Fid", headers=WRITE).status_code == 200
    rows = client.get("/v1/aliases").json()
    assert len(rows) == 1 and rows[0]["modality"] == "text-to-image"
    assert rows[0]["origin"] == "source"


@pytest.mark.parametrize("path", ["/v1/aliases/missing", "/v1/cost-multipliers/missing"])
def test_deletes_require_write_scope(client: TestClient, path: str) -> None:
    assert client.delete(path).status_code == 401
    assert client.delete(path, headers={"Authorization": "Bearer fixture-read"}).status_code == 403


def test_multiplier_delete(client: TestClient) -> None:
    prefix = "unused/with space"
    assert client.put("/v1/cost-multipliers", json={prefix: 0.2}, headers=WRITE).status_code == 200
    path = "/v1/cost-multipliers/" + quote(prefix, safe="")
    assert client.delete(path, headers=WRITE).json()["deleted"] == prefix
    assert client.delete(path, headers=WRITE).status_code == 404
    store = client.app.state.store  # type: ignore[attr-defined]
    assert (
        store.db.execute("SELECT 1 FROM cost_multipliers WHERE prefix=?", (prefix,)).fetchone()
        is None
    )


def test_disabled_source_requires_force(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pull = Mock(return_value=PullResult(source="manual"))
    load = Mock(return_value=SimpleNamespace(pull=pull))
    monkeypatch.setattr("sieve.plugins.load", load)
    path = "/v1/sources/manual/pull"
    assert client.post(path).status_code == 401
    assert client.post(path, headers={"Authorization": "Bearer fixture-read"}).status_code == 403
    result = client.post(path, headers=WRITE)
    assert result.status_code == 409 and result.json()["error"]["code"] == "source_disabled"
    load.assert_not_called()
    assert client.post(path + "?force=true", headers=WRITE).status_code == 200
    pull.assert_called_once()
    client.app.state.config.sources["manual"].enabled = True  # type: ignore[attr-defined]
    assert client.post(path, headers=WRITE).status_code == 200
    assert client.post("/v1/sources/missing/pull", headers=WRITE).status_code == 404


def test_guide_is_operating_markdown(client: TestClient) -> None:
    result = client.get("/v1/guide")
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/markdown")
    assert result.text == (Path(__file__).resolve().parents[1] / "OPERATING.md").read_text()
