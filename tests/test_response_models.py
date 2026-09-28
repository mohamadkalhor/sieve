"""O9b-4 -- response_model on the agent-facing reads must not change a body.

`tests/fixtures/response_bodies.json` was captured from the code *before* any
response model was added. Every target route is called on the fixture store and
its JSON compared, byte for byte after a stable dump, with that file. Set
`SIEVE_REGEN_GOLDEN=1` to rewrite it (only ever from unchanged route code).
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config
from sieve.contracts import Chain
from sieve.store import Store
from tests.test_api_acceptance import workspace  # noqa: F401  (the fixture)

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "response_bodies.json"
AUTH = {"Authorization": "Bearer s3cret"}

CALLS = [
    "/v1/leaderboard?modality=llm",
    "/v1/status",
    "/v1/models?modality=llm&q=gemini&limit=50",
    "/v1/models?modality=llm&reachable=true&limit=50",
    "/v1/recommend?profile=coder",
    "/v1/recommend?profile=coder&n=2&reachable_only=false",
    "/v1/profiles",
    "/v1/profiles/coder",
    "/v1/rankings/coder",
    "/v1/runs",
    "/v1/config",
]

#: values that legitimately differ between two runs of the same code.
VOLATILE = {"computed_at", "started", "finished", "seconds", "at", "next", "previous", "shipped_at", "observed_at", "pulled_at", "exported_at"}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (re.sub(r"\d", "9", v) if k in VOLATILE and isinstance(v, str) else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _diff(a: Any, b: Any, path: str = "") -> str | None:
    """The first place two JSON values differ, so a failure names the key."""
    if type(a) is not type(b):
        return f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: key {'missing' if k not in a else 'extra'}"
            found = _diff(a[k], b[k], f"{path}.{k}")
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            found = _diff(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


def _capture(client: TestClient, cfg: Config) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for url in CALLS:
        r = client.get(url, headers=AUTH)
        body = _scrub(r.json())
        if url.startswith("/v1/models") and isinstance(body, dict):
            # the store hands ties back in hash order; the golden is order-blind here
            body["items"] = sorted(body["items"], key=lambda m: m["id"])
            for item in body["items"]:
                item["aliases"] = sorted(item["aliases"])
        out[url] = {"status": r.status_code, "body": body}
    # the other branch of /v1/recommend: a profile that has shipped a chain
    store = Store(cfg.db_path)
    store.put_chain(
        Chain(
            profile="coder",
            computed_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            primary="google/gemini-3.8-flash",
            fallbacks=["openai/gpt-5-6-sol"],
            local={"google/gemini-3.8-flash": ["google/gemini-3-8-flash"]},
        )
    )
    store.close()
    for url in ("/v1/recommend?profile=coder&n=2", "/v1/recommend?profile=coder&n=1"):
        r = client.get(url, headers=AUTH)
        out["shipped:" + url] = {"status": r.status_code, "body": _scrub(r.json())}
    return out


def _seed_run(cfg: Config) -> None:
    store = Store(cfg.db_path)
    store.db.execute(
        "INSERT INTO runs(id,step,requested_by,started,finished,ok,summary) VALUES(?,?,?,?,?,?,?)",
        ("r_golden", "pull_sources", "test", "2026-09-01T10:00:00+00:00",
         "2026-09-01T10:01:00+00:00", 1, "pulled"),
    )
    store.db.commit()
    store.close()


def test_bodies_match_golden(workspace: Config) -> None:  # noqa: F811
    _seed_run(workspace)
    with TestClient(create_app(workspace)) as client:
        got = _capture(client, workspace)
    if os.environ.get("SIEVE_REGEN_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for url in got:
        assert got[url]["status"] == want[url]["status"], url
        assert _diff(want[url]["body"], got[url]["body"]) is None, (
            url,
            _diff(want[url]["body"], got[url]["body"]),
        )


def test_openapi_builds_and_names_the_models(  # noqa: F811
    workspace: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_V1", "1")
    with TestClient(create_app(workspace)) as client:
        r = client.get("/v1/openapi.json")
        assert r.status_code == 200, r.text[:300]
        spec = r.json()
    for path in ("/v1/leaderboard", "/v1/status", "/v1/models", "/v1/recommend", "/v1/runs", "/v1/config"):
        op = spec["paths"][path]["get"]
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema != {}, path
        inner = schema.get("items", schema)
        assert "$ref" in inner or "properties" in inner, (path, schema)
