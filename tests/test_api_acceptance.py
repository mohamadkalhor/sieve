"""PLAN section 12, the API line, end to end on real shipped data.

    PATCH /v1/profiles/coder/weights with a profiles:write token changes the
    file on disk, logs a decision with the actor, and the next
    GET /v1/rankings/coder reflects it; without a token it is 401.

The test pulls from the fixtures into a temporary store, so it exercises the
same path a person would and never touches the network.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import InventoryConfig, SourceConfig, TargetConfig
from sieve.http import FixturePlayer
from sieve.plugins import INVENTORIES, SOURCES, load
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"

TOKENS = "ops:read,profiles:write,apply,telemetry:s3cret;watcher:read:reader"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """A temporary Sieve with the shipped axes, a copy of the shipped profiles,
    and the fixture data loaded through the real source and inventory code."""
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("ARTIFICIAL_ANALYSIS_API_KEY", "fixture-mode")

    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)

    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
        sources={
            "openrouter": SourceConfig(name="openrouter", modalities=["llm"]),
            "aa_llm": SourceConfig(
                name="aa_llm", key_env="ARTIFICIAL_ANALYSIS_API_KEY", modalities=["llm"]
            ),
        },
        inventories={
            "gateway": InventoryConfig(
                name="gateway",
                kind="list",
                models=[
                    # Real ids from the 2026-09-08 AA recording, spread across
                    # price points so that reweighting cost genuinely reorders
                    # the list: $0.75 to $10.00 per 1M input, at coding indexes
                    # within four points of each other.
                    "openai/gpt-5-6-sol-xhigh",
                    "openai/gpt-5-6-sol",
                    "openai/gpt-6-astra-high",
                    "openai/gpt-6-astra",
                    "openai/gpt-5-6-terra",
                    "meta/muse-spark-1-3-xhigh",
                    "meta/muse-spark-1-3",
                    "google/gemini-3-8-flash",
                    "google/gemini-3-8-flash-medium",
                    "google/gemini-3-8-flash-low",
                ],
            )
        },
        targets={"out": TargetConfig(name="out", kind="file", dir=str(tmp_path / "out"))},
    )

    player = FixturePlayer(FIXTURES)
    store = Store(cfg.db_path)
    from sieve.catalog.registry import Registry, merge_pull

    for name in ("openrouter", "aa_llm"):
        source = load(SOURCES, name)
        result = source.pull(cfg.sources[name], player)
        result, _merged = merge_pull(result, [m.id for m in store.models()], store.aliases())
        store.upsert_models(result.models)
        store.add_observations(result.observations)
        store.add_prices(result.prices)
        store.set_capabilities(name, "llm", result.capabilities)

    registry = Registry()
    registry.extend(store.models())
    found = load(INVENTORIES, "list").list(cfg.inventories["gateway"], player)
    matched, unmatched = registry.attach(found, "llm")
    store.set_reachable("gateway", matched + unmatched)
    store.close()
    return cfg


def test_moving_a_weight_through_the_api_changes_the_file_and_the_ranking(
    workspace: Config,
) -> None:
    app = create_app(workspace)
    file = workspace.profiles_dir / "llm" / "coder.yaml"

    with TestClient(app) as client:
        before = client.get("/v1/rankings/coder")
        assert before.status_code == 200
        ranked_before = [r["model_id"] for r in before.json()["ranks"] if r["position"] > 0]
        assert ranked_before, "the shipped data must produce a ranking"

        # without a token: 401, and nothing on disk moves
        original = file.read_text(encoding="utf-8")
        denied = client.patch("/v1/profiles/coder/weights", json={"cost": 1.0})
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "unauthorized"
        assert file.read_text(encoding="utf-8") == original

        # with the wrong scope: 403
        wrong = client.patch(
            "/v1/profiles/coder/weights",
            json={"cost": 1.0},
            headers={"Authorization": "Bearer reader"},
        )
        assert wrong.status_code == 403

        # weights that do not sum to 1 are refused before anything is written
        bad = client.patch(
            "/v1/profiles/coder/weights",
            json={"cost": 0.5, "agentic_coding": 0.2},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_weights"
        assert file.read_text(encoding="utf-8") == original

        # with the right scope: the write lands
        moved = client.patch(
            "/v1/profiles/coder/weights",
            json={"cost": 0.9, "agentic_coding": 0.1},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert moved.status_code == 200
        assert moved.json()["weights"] == {"cost": 0.9, "agentic_coding": 0.1}

        # 1. the file on disk changed, and kept its comments
        written = yaml.safe_load(file.read_text(encoding="utf-8"))
        assert written["weights"] == {"cost": 0.9, "agentic_coding": 0.1}
        assert file.read_text(encoding="utf-8").startswith("#"), "the file is still a file"

        # 2. a decision was logged, naming the token as the actor
        decisions = client.get("/v1/decisions?profile=coder").json()
        assert decisions, "every profile change is a decision row"
        assert decisions[0]["kind"] == "weights"
        assert decisions[0]["actor"] == "ops"
        assert decisions[0]["before"] != decisions[0]["after"]

        # 3. the next ranking reflects it
        after = client.get("/v1/rankings/coder")
        assert after.status_code == 200
        ranked_after = [r["model_id"] for r in after.json()["ranks"] if r["position"] > 0]
        assert ranked_after, "the profile still ranks after the change"
        assert ranked_after != ranked_before, (
            "weighting cost at 0.9 instead of 0.2 must change the order; "
            f"before={ranked_before} after={ranked_after}"
        )


def test_evaluate_is_a_dry_run_and_stores_nothing(workspace: Config) -> None:
    app = create_app(workspace)
    with TestClient(app) as client:
        before = len(client.get("/v1/decisions").json())

        response = client.post("/v1/profiles/coder/evaluate")
        assert response.status_code == 200
        body = response.json()
        assert body["ranking"]["profile"] == "coder"
        assert body["decision"] is not None, "a dry run still says what it would decide"

        assert len(client.get("/v1/decisions").json()) == before, "a dry run stores nothing"


def test_recommend_serves_the_chain_the_engine_computed(workspace: Config) -> None:
    app = create_app(workspace)
    with TestClient(app) as client:
        response = client.get("/v1/recommend?profile=coder&n=2")
        assert response.status_code == 200
        body = response.json()
        assert body["profile"] == "coder"
        assert 1 <= len(body["models"]) <= 2
        first = body["models"][0]
        assert first["local_ids"], "recommend returns the ids the gateway serves"
        assert first["final"] is not None


def test_controlled_profile_settings_status_experience_preview_and_crud(workspace: Config) -> None:
    app = create_app(workspace)
    headers = {"Authorization": "Bearer s3cret"}
    with TestClient(app) as client:
        settings = client.get("/v1/profiles/judge/settings").json()
        assert settings["list_length"] >= 1
        changed = client.put(
            "/v1/profiles/judge/settings",
            json={"list_length": 3, "floor_score": 0.1},
            headers=headers,
        )
        assert changed.status_code == 200
        assert changed.json()["list_length"] == 3

        model = "openai/gpt-5-6-sol"
        pinned = client.put(
            f"/v1/profiles/judge/models/{model}/status",
            json={"status": "pinned"},
            headers=headers,
        )
        assert pinned.json()["status"] == "pinned"

        outcome = client.post(
            "/v1/outcomes",
            json={
                "profile": "judge",
                "model_id": model,
                "local_id": model,
                "ok": True,
                "seconds": 1.2,
                "vote": 1,
                "note": "fixture",
            },
            headers=headers,
        )
        assert outcome.status_code == 200
        experience = client.get("/v1/profiles/judge/experience").json()
        assert experience[0]["experience"] == pytest.approx(2 / 3)

        preview = client.post("/v1/profiles/judge/preview", json={"list_length": 2})
        assert preview.status_code == 200
        assert preview.json()["models"][0] == model
        assert len(preview.json()["models"]) <= 2
        assert client.get("/v1/profiles/judge/settings").json()["list_length"] == 3

        # Preview reweights the latest persisted axis scores instead of rebuilding
        # the observation table. A changed weight must therefore still change rank.
        cached = app.state.store.ranking("judge")
        assert cached is not None
        inverted = client.post(
            "/v1/profiles/judge/preview",
            json={
                "weights": {
                    "instruction": {"value": 0.0},
                    "reasoning": {"value": 1.0},
                    "intelligence": {"value": 0.0},
                }
            },
        )
        assert inverted.status_code == 200
        returned_at = datetime.fromisoformat(
            inverted.json()["ranking"]["computed_at"].replace("Z", "+00:00")
        )
        assert returned_at == cached.computed_at

        created = client.post(
            "/v1/profiles", json={"name": "judge-copy", "from": "judge"}, headers=headers
        )
        assert created.status_code == 200
        renamed = client.patch(
            "/v1/profiles/judge-copy", json={"name": "judge-copy-2"}, headers=headers
        )
        assert renamed.status_code == 200
        deleted = client.delete("/v1/profiles/judge-copy-2?force=1", headers=headers)
        assert deleted.status_code == 200
        assert client.get("/v1/profiles/judge/history").json()


def test_a_profile_that_was_never_applied_deletes_without_force(workspace: Config) -> None:
    """The delete guard used to read "does any write connector exist", which is
    true of every box that ships anything -- so a profile made and thought
    better of could not be deleted without `force=1`, and the flag stopped
    meaning anything. The combo a connector might hold is named after a chain,
    so the chain is the guard."""
    app = create_app(workspace)
    headers = {"Authorization": "Bearer s3cret"}
    with TestClient(app) as client:
        created = client.post(
            "/v1/profiles", json={"name": "scratch", "from": "judge"}, headers=headers
        )
        assert created.status_code == 200
        assert app.state.store.chain("scratch") is None, "a copy is not an apply"

        deleted = client.delete("/v1/profiles/scratch", headers=headers)
        assert deleted.status_code == 200, deleted.json()
        assert client.get("/v1/profiles/scratch").status_code == 404

        # An apply is what puts a name on a router, and it is what writes the
        # chain, so the guard fires from there on.
        applied = client.post("/v1/profiles/coder/apply", headers=headers)
        assert applied.status_code == 200, applied.json()
        assert app.state.store.chain("coder") is not None
        guarded = client.delete("/v1/profiles/coder", headers=headers)
        assert guarded.status_code == 409
        assert guarded.json()["error"]["code"] == "in_use"
        assert client.delete("/v1/profiles/coder?force=1", headers=headers).status_code == 200


def test_prefix_multipliers_appear_and_can_be_changed(workspace: Config) -> None:
    app = create_app(workspace)
    headers = {"Authorization": "Bearer s3cret"}
    with TestClient(app) as client:
        defaults = client.get("/v1/cost-multipliers").json()
        assert defaults["openai"] == 1.0
        changed = client.put("/v1/cost-multipliers", json={"openai": 0.5}, headers=headers)
        assert changed.status_code == 200
        assert changed.json()["openai"] == 0.5
