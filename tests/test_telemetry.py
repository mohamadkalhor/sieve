"""Phase 2 part 3 — telemetry, health, suspend, and cost from measured tokens.

Health is the one axis no benchmark can supply: whether the model is answering
*you*, today. Everything here reads only what callers reported through
`POST /v1/telemetry`. Nothing reads anyone's gateway logs.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import InventoryConfig, Shape, SourceConfig, TargetConfig, TelemetryEvent
from sieve.http import FixturePlayer
from sieve.plugins import SOURCES, load
from sieve.scoring.pulse import observed_tokens_out, percentile, pulse
from sieve.store import Store

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
TOKENS = "ops:read,profiles:write,apply,telemetry:s3cret"
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

REACHABLE = [
    "openai/gpt-5-6-sol-xhigh",
    "openai/gpt-6-astra-high",
    "openai/gpt-5-6-terra",
    "google/gemini-3-8-flash",
    "meta/muse-spark-1-3",
]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """A real store with the shipped axes and profiles, loaded from recordings."""
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("ARTIFICIAL_ANALYSIS_API_KEY", "fixture-mode")

    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)

    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
        sources={
            "aa_llm": SourceConfig(
                name="aa_llm", key_env="ARTIFICIAL_ANALYSIS_API_KEY", modalities=["llm"]
            )
        },
        inventories={
            "gateway": InventoryConfig(name="gateway", kind="list", models=list(REACHABLE))
        },
        targets={"out": TargetConfig(name="out", kind="file", dir=str(tmp_path / "out"))},
    )

    store = Store(cfg.db_path)
    source = load(SOURCES, "aa_llm")
    result = source.pull(cfg.sources["aa_llm"], FixturePlayer(FIXTURES))
    store.upsert_models(result.models)
    store.add_observations(result.observations)
    store.add_prices(result.prices)

    from sieve.plugins import INVENTORIES

    inventory = load(INVENTORIES, "list")
    reachable = inventory.list(cfg.inventories["gateway"], FixturePlayer(FIXTURES))
    # a static list serves the canonical ids it names, under a gateway prefix,
    # so the local id and the model it serves are both real here
    store.set_reachable(
        "gateway",
        [
            r.model_copy(
                update={"local_id": f"gw/{r.local_id.split('/', 1)[-1]}", "model_id": r.local_id}
            )
            for r in reachable
        ],
    )
    return cfg


def _events(
    model: str, *, ok: bool, n: int, status: int | None = None, **kw: object
) -> list[TelemetryEvent]:
    return [
        TelemetryEvent(model=model, ok=ok, status=status, at=NOW - timedelta(minutes=i), **kw)  # type: ignore[arg-type]
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# the figures behind the number
# --------------------------------------------------------------------------- #


def test_a_percentile_is_a_real_observation_not_an_interpolation() -> None:
    """p95 of eight calls should be a latency somebody actually measured."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 800.0]
    assert percentile(values, 0.50) in values
    assert percentile(values, 0.95) == 800.0
    assert percentile([], 0.5) is None
    assert percentile([7.0], 0.95) == 7.0


def test_pulse_separates_a_rate_limit_from_an_error() -> None:
    """A throttled model is busy, not broken, and the two must not read alike."""
    events = [
        *_events("a/b", ok=True, n=6, latency_ms=100),
        *_events("a/b", ok=False, n=2, status=429, latency_ms=50),
        *_events("a/b", ok=False, n=2, status=500, latency_ms=90),
    ]
    row = pulse(events, NOW)["a/b"]
    assert row["events"] == 10
    assert row["rate_limited_share"] == pytest.approx(0.2)
    assert row["ok_rate"] == pytest.approx(0.6)
    assert row["p50_latency_ms"] is not None


def test_a_model_nobody_called_is_absent_rather_than_perfect() -> None:
    """ "Nobody called it" and "every call failed" are opposite facts."""
    assert pulse(_events("a/b", ok=True, n=3), NOW, hours=24).keys() == {"a/b"}
    assert pulse(_events("a/b", ok=True, n=3), NOW + timedelta(days=9), hours=24) == {}


# --------------------------------------------------------------------------- #
# cost from measured tokens
# --------------------------------------------------------------------------- #


def test_measured_tokens_need_enough_calls_to_be_a_measurement() -> None:
    """Four calls are an anecdote; the shape's stated assumption is better."""
    few = _events("a/b", ok=True, n=4, tokens_out=900)
    assert observed_tokens_out(few, NOW) == {}

    enough = _events("a/b", ok=True, n=6, tokens_out=900)
    assert observed_tokens_out(enough, NOW) == {"a/b": 900.0}

    # a failed call burned no useful tokens and must not price the model
    failed = _events("a/b", ok=False, n=9, tokens_out=5, status=500)
    assert observed_tokens_out([*enough, *failed], NOW)["a/b"] == 900.0


def test_cost_uses_the_tokens_a_model_really_burns() -> None:
    """PLAN 2.1: the rate cannot separate effort modes, only the tokens can."""
    from sieve.contracts import Price
    from sieve.scoring.weigh import cost_per_task

    price = Price(
        model_id="a/b",
        source="aa_llm",
        unit="usd_per_1m_tokens",
        input=1.0,
        output=10.0,
        observed_at=NOW,
    )
    shape = Shape.model_validate({"in": 1000, "out": 1000})

    assumed = cost_per_task(price, shape)
    measured = cost_per_task(price, shape, tokens_out=4000.0)
    assert assumed is not None and measured is not None
    assert measured > assumed, "a model that burns four times the tokens costs more"
    assert measured == pytest.approx(assumed + 3000 * 10.0 / 1_000_000)


# --------------------------------------------------------------------------- #
# end to end, through the API
# --------------------------------------------------------------------------- #


def test_posting_events_moves_a_models_health_and_its_rank(workspace: Config) -> None:
    """The acceptance line: telemetry has to change the answer, or it is decoration."""
    app = create_app(workspace)
    with TestClient(app) as client:
        # cheap_bulk floors on `intelligence` alone. `coder` also requires tools,
        # reasoning and a 200k context, and this workspace pulls only Artificial
        # Analysis -- which publishes no capabilities -- so nothing would clear it.
        before = client.get("/v1/rankings/cheap_bulk?refresh=1").json()
        ranked_before = [r["model_id"] for r in before["ranks"] if not r.get("excluded_by")]
        assert ranked_before, "the recording must produce a ranking to move"

        leader = ranked_before[0]
        now = datetime.now(UTC)
        body = [
            {
                "model": leader,
                "ok": False,
                "status": 500,
                "at": (now - timedelta(minutes=i)).isoformat(),
            }
            for i in range(40)
        ]
        posted = client.post("/v1/telemetry", json=body, headers={"authorization": "Bearer s3cret"})
        assert posted.status_code == 200
        assert posted.json()["accepted"] == 40

        after = client.get("/v1/rankings/cheap_bulk?refresh=1").json()
        row = next(r for r in after["ranks"] if r["model_id"] == leader)
        assert row["health"] < 0.5, f"40 failed calls should sink health, got {row['health']}"
        assert row["final"] < row["score"], "final = score x health, and health is no longer 1"


def test_the_health_route_serves_the_figures_and_the_sparkline(workspace: Config) -> None:
    """The route the Rankings sparkline was built for and never got."""
    app = create_app(workspace)
    with TestClient(app) as client:
        now = datetime.now(UTC)
        body = [
            {
                "model": REACHABLE[0],
                "ok": i % 5 != 0,
                "status": 429 if i % 5 == 0 else 200,
                "latency_ms": 100 + i,
                "tokens_out": 500,
                "at": (now - timedelta(hours=i % 6)).isoformat(),
            }
            for i in range(30)
        ]
        assert (
            client.post("/v1/telemetry", json=body, headers={"authorization": "Bearer s3cret"})
        ).status_code == 200

        rows = client.get("/v1/health", headers={"authorization": "Bearer s3cret"}).json()
        row = next(r for r in rows if r["model_id"] == REACHABLE[0])
        assert row["events"] == 30
        assert 0.0 < row["ok_rate"] < 1.0
        assert row["rate_limited_share"] == pytest.approx(0.2)
        assert row["p50_latency_ms"] is not None and row["p95_latency_ms"] is not None
        assert len(row["series"]) >= 1
        assert any(v is None for v in row["series"]), "a day nobody called is null, not 1.0"


def test_a_local_id_is_resolved_to_the_model_it_serves(workspace: Config) -> None:
    """A gateway knows its own ids and nothing else."""
    store = Store(workspace.db_path)
    local = next(iter(store.local_ids().values()))[0]
    canonical = next(k for k, v in store.local_ids().items() if local in v)

    app = create_app(workspace)
    with TestClient(app) as client:
        now = datetime.now(UTC)
        client.post(
            "/v1/telemetry",
            json=[{"model": local, "ok": True, "at": now.isoformat()}],
            headers={"authorization": "Bearer s3cret"},
        )
    stored = {event.model for event in Store(workspace.db_path).telemetry()}
    assert canonical in stored, f"{local} should have resolved to {canonical}, got {stored}"
