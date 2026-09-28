"""`POST /v1/mcp` serves sieve's tools through the kit registry -- card O9b, step 3.

The kit's `check_parity` reads the app's own OpenAPI and fails on any tool whose
route, schema or scope has drifted from its route's. The rest calls the tools
over MCP in-process and holds them to their routes: a read tool answers its
route's JSON, a key without the route's scope is refused as the route refuses
it, and with `AGENT_V1` off the path is not there.

The fixtures are `tests/test_aio.py`'s (a box with an owner, a member, a stub
gate), imported by name.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from sieve.api import aio, v1_tools
from sieve.api.app import create_app
from sieve.config import Config
from sieve.store import Store
from tests.test_aio import (  # noqa: F401  (fixtures pytest finds by name)
    ADA,
    BOX,
    OWNER_EMAIL,
    _gate,
    auth_header,
    box,
    off,
    on,
    seats,
    secret_for,
)

OLD_TOOLS = {
    "list_profiles",
    "get_profile",
    "set_weights",
    "set_ship",
    "evaluate",
    "recommend",
    "get_ranking",
    "explain",
    "report_outcome",
    "apply",
}
NEW_TOOLS = {"list_models", "leaderboard", "status", "list_runs", "export_config"}


def rpc(client: TestClient, headers: dict[str, str], method: str, params: dict[str, Any] | None = None) -> Any:
    answer = client.post(
        "/v1/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["result"]


def call(client: TestClient, headers: dict[str, str], name: str, arguments: dict[str, Any]) -> Any:
    return rpc(client, headers, "tools/call", {"name": name, "arguments": arguments})


def test_every_tool_is_its_route(on: TestClient) -> None:
    """The kit's own parity check: route exists, schemas equal, scope equal."""
    aio._kit()
    from agentkit import mcp

    assert mcp.check_parity(on.app, v1_tools.registry()) == []


def test_parity_holds_with_the_kit_off_too(box: Config, seats: Store) -> None:
    aio._kit()
    from agentkit import mcp

    assert mcp.check_parity(create_app(box), v1_tools.registry()) == []


def test_tools_list_has_every_old_name_and_the_new_reads(on: TestClient) -> None:
    listed = rpc(on, BOX, "tools/list")["tools"]
    names = {tool["name"] for tool in listed}
    assert OLD_TOOLS <= names
    assert NEW_TOOLS <= names
    assert names == OLD_TOOLS | NEW_TOOLS
    by_name = {tool["name"]: tool for tool in listed}
    assert by_name["explain"]["x-aio-route"] == {"method": "GET", "path": "/v1/rankings/{profile}/explain"}
    assert "_idempotency_key" in by_name["apply"]["inputSchema"]["required"]


def test_a_read_tool_answers_what_its_route_answers(on: TestClient) -> None:
    for name, arguments, route in (
        ("list_profiles", {}, "/v1/profiles"),
        ("list_models", {"limit": 5}, "/v1/models?limit=5"),
        ("leaderboard", {"modality": "llm"}, "/v1/leaderboard?modality=llm"),
        ("status", {}, "/v1/status"),
        ("list_runs", {"limit": 3}, "/v1/runs?limit=3"),
        ("export_config", {}, "/v1/config"),
        ("recommend", {"profile": "coder", "n": 2}, "/v1/recommend?profile=coder&n=2"),
    ):
        by_tool = call(on, BOX, name, arguments)
        by_route = on.get(route, headers=BOX)
        assert by_route.status_code == 200, (name, by_route.text)
        assert by_tool["isError"] is False, (name, by_tool)
        wanted = by_route.json()
        if isinstance(wanted, list):  # a list answer travels as {"result": [...]}
            wanted = {"result": wanted}
        got = by_tool["structuredContent"]
        for stamp in ("exported_at", "computed_at"):  # the moment it was asked
            got.pop(stamp, None), wanted.pop(stamp, None)
        assert got == wanted, name


def test_sections_narrows_status_and_config(on: TestClient) -> None:
    full = on.get("/v1/status", headers=BOX).json()
    one = call(on, BOX, "status", {"sections": "pulled_at,reachable"})["structuredContent"]
    assert set(one) == {"pulled_at", "reachable"}
    assert one["reachable"] == full["reachable"]
    config = on.get("/v1/config", headers=BOX).json()
    key = sorted(config)[0]
    key = next(k for k in sorted(config) if k != "exported_at")
    narrowed = call(on, BOX, "export_config", {"sections": key})["structuredContent"]
    assert narrowed == {key: config[key]}


def test_explain_is_a_route(on: TestClient) -> None:
    by_tool = call(on, BOX, "explain", {"profile": "coder"})
    by_route = on.get("/v1/rankings/coder/explain", headers=BOX)
    assert by_tool["structuredContent"] == by_route.json()
    assert by_route.json()["profile"] == "coder"
    missing = call(on, BOX, "explain", {"profile": "no-such-profile"})
    assert missing["isError"] is True
    assert missing["structuredContent"]["error"]["code"] == "not_found"


def test_a_key_without_the_scope_is_refused(on: TestClient, seats: Store) -> None:
    reader, _ = secret_for(seats, ADA, {"read"})
    headers = auth_header(reader)
    # the read is served ...
    assert call(on, headers, "list_profiles", {})["isError"] is False
    # ... the writes are the route's own refusal, in the kit's envelope
    weights = call(on, headers, "set_weights", {"name": "coder", "quality": 1})
    assert weights["isError"] is True
    assert weights["structuredContent"]["error"]["code"] == "not_allowed"
    ship = call(on, headers, "set_ship", {"name": "coder", "ship": 3})
    assert ship["structuredContent"]["error"]["code"] == "not_allowed"
    applied = call(on, headers, "apply", {"_idempotency_key": "kit-mcp-1"})
    assert applied["structuredContent"]["error"]["code"] == "not_allowed"
    events = call(on, headers, "report_outcome", {"events": []})
    assert events["structuredContent"]["error"]["code"] == "not_allowed"


def test_a_key_with_the_scope_reaches_the_route(on: TestClient) -> None:
    """The owner's box token holds every scope the routes ask for."""
    applied = call(on, BOX, "apply", {"_idempotency_key": "kit-mcp-2"})
    # nothing has been computed on this store: the route's own answer, not a refusal
    assert applied["structuredContent"]["error"]["code"] == "not_found"
    reported = call(
        on,
        {"Authorization": "Bearer t3lem"},
        "report_outcome",
        {"events": [{"model": "m/a", "ok": True, "at": "2026-09-28T10:00:00Z"}]},
    )
    assert reported["isError"] is False, reported
    assert reported["structuredContent"]["accepted"] == 1


def test_set_weights_takes_the_body_as_the_arguments(on: TestClient) -> None:
    profile = on.get("/v1/profiles/coder", headers=BOX).json()
    weights = {axis: value for axis, value in profile["weights"].items()}
    done = call(on, BOX, "set_weights", {"name": "coder", **weights})
    assert done["isError"] is False, done
    assert done["structuredContent"]["weights"] == weights


def test_mcp_needs_a_key(on: TestClient) -> None:
    answer = on.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert answer.status_code == 401
    assert answer.json()["error"]["code"] == "sign_in"


def test_off_the_path_is_sieves_own_404(off: TestClient) -> None:
    answer = off.post("/v1/mcp", headers=BOX, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert answer.status_code == 404
    assert answer.json() == {"error": {"code": "not_found", "message": "no /v1/mcp endpoint"}}
