"""The integrator's own pieces: store, config, tokens, types.ts, the fixture.

A owns `tests/test_scoring*.py` and `tests/test_axes*.py`; B owns
`tests/test_sources*.py` and `tests/test_catalog*.py`. This file covers only
what CONTRACTS section 9 lists under the integrator.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.api.auth import Tokens
from sieve.config import default_config, load_config
from sieve.contracts import Decision, ModelRef, Observation, Price, Profile, Shape
from sieve.store import Store
from sieve.typegen import render

FIXTURE = Path(__file__).parent / "fixtures" / "rank_case.json"


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "sieve.db")


def _observation(**over: object) -> Observation:
    base: dict[str, object] = {
        "model_id": "anthropic/claude-opus-5",
        "modality": "llm",
        "source": "aa_llm",
        "field": "gpqa",
        "value": 0.91,
        "unit": "fraction",
        "observed_at": datetime(2026, 9, 1, tzinfo=UTC),
        "pulled_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    base.update(over)
    return Observation.model_validate(base)


def test_observations_are_append_only(store: Store) -> None:
    obs = _observation()
    assert store.add_observations([obs]) == 1
    assert store.add_observations([obs]) == 0, "the same measurement must not duplicate"

    later = _observation(value=0.93, observed_at=datetime(2026, 9, 5, tzinfo=UTC))
    assert store.add_observations([later]) == 1, "a new observed_at is a new row"
    assert store.count_observations("llm") == 2

    table = store.obs_table("llm")
    held = table.get("anthropic/claude-opus-5", "aa_llm", "gpqa")
    assert held is not None and held.value == 0.93, "obs_table keeps the newest"


def test_catalog_upsert_keeps_aliases(store: Store) -> None:
    store.upsert_models(
        [
            ModelRef(
                id="anthropic/claude-opus-5",
                modality="llm",
                name="Claude Opus 5",
                creator="anthropic",
                aliases=["claude-opus-5", "claude-opus-5-20260101"],
            )
        ]
    )
    store.upsert_models(
        [
            ModelRef(
                id="anthropic/claude-opus-5",
                modality="llm",
                name="Claude Opus 5",
                creator="anthropic",
                aliases=["opus-5"],
            )
        ]
    )
    models = store.models("llm")
    assert len(models) == 1
    assert set(models[0].aliases) == {"claude-opus-5", "claude-opus-5-20260101", "opus-5"}


def test_prices_and_decisions(store: Store) -> None:
    store.upsert_models([ModelRef(id="x/y", modality="llm", name="Y", creator="x")])
    store.add_prices(
        [
            Price(
                model_id="x/y",
                source="openrouter",
                unit="usd_per_1m_tokens",
                input=3.0,
                output=15.0,
                observed_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        ]
    )
    assert store.latest_prices("llm")["x/y"].output == 15.0

    store.add_decision(
        Decision(
            id="d1",
            at=datetime.now(UTC),
            profile="coder",
            kind="hold",
            actor="cli",
            reason="challenger x/y +1.4 inside margin 3.0",
        )
    )
    held = store.decisions(profile="coder")
    assert len(held) == 1 and held[0].kind == "hold"
    assert "inside margin" in held[0].reason


def test_telemetry_prunes(store: Store) -> None:
    from sieve.contracts import TelemetryEvent

    old = TelemetryEvent(model="x/y", ok=True, at=datetime.now(UTC) - timedelta(days=45))
    new = TelemetryEvent(model="x/y", ok=False, status=429, at=datetime.now(UTC))
    store.add_telemetry([old, new])
    assert store.prune_telemetry(30) == 1
    assert len(store.telemetry()) == 1


# --------------------------------------------------------------------------- #
# config and tokens
# --------------------------------------------------------------------------- #


def test_config_reads_sections_and_never_holds_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toml = tmp_path / "sieve.toml"
    toml.write_text(
        """
[store]
path = "data/test.db"
[server]
host = "127.0.0.1"
port = 9110
[sources.aa_llm]
enabled = true
key_env = "ARTIFICIAL_ANALYSIS_API_KEY"
[inventories.gateway]
kind = "openai_compat"
base_url = "http://localhost:20128"
token_env = "GATEWAY_TOKEN"
[targets.out]
kind = "file"
dir = "out"
""",
        encoding="utf-8",
    )
    cfg = load_config(toml)
    assert cfg.server.port == 9110
    assert cfg.db_path == tmp_path / "data/test.db"
    assert cfg.sources["aa_llm"].key_env == "ARTIFICIAL_ANALYSIS_API_KEY"
    assert cfg.inventories["gateway"].base_url == "http://localhost:20128"
    assert cfg.targets["out"].kind == "file"

    # the file names the variable; the value comes from the environment or not at all
    monkeypatch.delenv("ARTIFICIAL_ANALYSIS_API_KEY", raising=False)
    assert cfg.sources["aa_llm"].key() is None
    monkeypatch.setenv("ARTIFICIAL_ANALYSIS_API_KEY", "from-the-environment")
    assert cfg.sources["aa_llm"].key() == "from-the-environment"


def test_tokens_parse_scopes_that_contain_a_colon() -> None:
    tokens = Tokens.from_env(
        "ops:read,profiles:write,apply,telemetry:s3cret;agent:read,telemetry:other"
    )
    ops = tokens.lookup("s3cret")
    assert ops is not None and ops.name == "ops"
    assert ops.allows("profiles:write") and ops.allows("apply")

    agent = tokens.lookup("other")
    assert agent is not None and not agent.allows("apply")
    assert tokens.lookup("nope") is None


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #


def test_write_routes_need_the_right_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIEVE_TOKENS", "ops:read,profiles:write:s3cret;agent:read,telemetry:other")
    app = create_app(default_config(tmp_path))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200

        anonymous = client.patch("/v1/profiles/coder/weights", json={"a": 1.0})
        assert anonymous.status_code == 401
        assert anonymous.json()["error"]["code"] == "unauthorized"

        wrong = client.patch(
            "/v1/profiles/coder/weights",
            json={"a": 1.0},
            headers={"Authorization": "Bearer other"},
        )
        assert wrong.status_code == 403

        telemetry = client.post(
            "/v1/telemetry",
            json=[{"model": "x/y", "ok": True, "at": "2026-09-07T12:00:00Z"}],
            headers={"Authorization": "Bearer other"},
        )
        assert telemetry.status_code == 200
        # `pruned` is the 30-day retention CONTRACTS section 4 asks for,
        # applied on write because that is when the table grows
        assert telemetry.json() == {"accepted": 1, "pruned": 0}


def test_the_not_built_envelope_names_the_owner_and_ships_the_shape() -> None:
    """While a module was missing, its route answered 501 with the contract's
    JSON schema, so the web could be built against it first. Every module has
    landed now, so this asserts the mechanism rather than a live 501."""
    from sieve.api.routes.v1 import not_built
    from sieve.contracts import Axis

    response = not_built(Axis, "A", "sieve.axes.load")
    body = json.loads(bytes(response.body))
    assert response.status_code == 501
    assert body["error"]["code"] == "not_built"
    assert "owned by A" in body["error"]["message"]
    assert body["shape"]["properties"]["fields"], "the contract shape travels with the 501"


def test_every_route_of_the_contract_answers(tmp_path: Path) -> None:
    """CONTRACTS section 6, end to end. An empty store may answer 404; nothing
    may answer 501 any more, and nothing may raise."""
    app = create_app(default_config(tmp_path))
    reads = [
        "/v1/modalities",
        "/v1/axes",
        "/v1/models",
        "/v1/profiles",
        "/v1/decisions",
        "/v1/sources",
        "/v1/inventory",
        "/v1/rankings/coder",
        "/v1/chains/coder",
        "/v1/recommend?profile=coder",
    ]
    with TestClient(app) as client:
        for path in reads:
            response = client.get(path)
            assert response.status_code in (200, 404), f"{path} -> {response.status_code}"
            assert response.status_code != 501, f"{path} still answers not_built"


# --------------------------------------------------------------------------- #
# generated types
# --------------------------------------------------------------------------- #


def test_types_ts_is_current() -> None:
    """CI runs `sieve export-types` and diffs; this catches it one step earlier."""
    committed = Path("web/src/lib/types.ts")
    assert committed.exists(), "run `sieve export-types` and commit the result"
    assert committed.read_text(encoding="utf-8") == render(), "types.ts is stale"


def test_types_ts_matches_what_the_api_serialises() -> None:
    generated = render()
    assert "in_tokens?: number | null;" in generated
    assert "export type Modality =" in generated
    assert "export interface Ranking {" in generated


def test_shape_accepts_the_yaml_spelling() -> None:
    profile = Profile(
        name="coder",
        modality="llm",
        purpose="agentic coding",
        weights={"agentic_coding": 1.0},
        shape=Shape.model_validate({"in": 30000, "out": 4000, "cached": 0.5}),
    )
    assert profile.shape.in_tokens == 30000
    assert profile.shape.out_tokens == 4000
    assert "in_tokens" in profile.shape.model_dump()


# --------------------------------------------------------------------------- #
# the shared scoring fixture
# --------------------------------------------------------------------------- #


def test_rank_case_fixture_is_internally_consistent() -> None:
    """A and D both assert against this file, so its own arithmetic must hold."""
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    weights = case["profile"]["weights"]
    floor = case["profile"]["policy"]["min_confidence"]
    expected = case["expected"]

    assert len(case["models"]) == 12
    assert all(len(m["axes"]) == 6 for m in case["models"])
    assert abs(sum(weights.values()) - 1.0) < 1e-9

    for model in case["models"]:
        axes = model["axes"]
        score = sum(w * (axes[a]["value"] or 0.0) for a, w in weights.items())
        confidence = sum(w * axes[a]["coverage"] for a, w in weights.items())
        assert abs(score - expected["scores"][model["id"]]) < 1e-6
        assert abs(confidence - expected["confidence"][model["id"]]) < 1e-6

    excluded = {m for m, c in expected["confidence"].items() if c < floor}
    assert excluded == set(expected["excluded"]) == {"m11"}

    ranked = [m for m in expected["scores"] if m not in excluded]
    assert expected["order"] == sorted(ranked, key=lambda m: (-expected["scores"][m], m))
    assert expected["order"][0] == "m12"
    # the exact tie is deliberate: it pins the tie-break rule
    assert abs(expected["scores"]["m03"] - expected["scores"]["m05"]) < 1e-12
    assert expected["order"].index("m03") < expected["order"].index("m05")
