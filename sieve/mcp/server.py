"""`sieve mcp` — the MCP server (CONTRACTS section 7).

The tools of PLAN section 7, one to one with the API. Every tool delegates to
the `/v1` layer over an in-process ASGI transport carrying `SIEVE_TOKEN`, so
scopes, validation and the decision log are exactly the API's -- there is no
second code path to keep in step.

This is the local door: a process on the caller's own machine, which keeps
`SIEVE_TOKEN` as its one bearer. The hosted door is `POST /v1/mcp`, served by
the kit from the same tool names (`sieve/api/v1_tools.py`), where each call
carries the caller's own API key instead.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from sieve.api.app import create_app

TOKEN_ENV = "SIEVE_TOKEN"

TOOLS: dict[str, dict[str, Any]] = {
    "list_profiles": {
        "description": "Every profile, optionally for one modality.",
        "method": "GET",
        "path": "/v1/profiles",
        "query": ["modality"],
        "schema": {"modality": {"type": "string"}},
    },
    "get_profile": {
        "description": "One profile: its weights and how many models it ships.",
        "method": "GET",
        "path": "/v1/profiles/{name}",
        "schema": {"name": {"type": "string"}},
        "required": ["name"],
    },
    "set_weights": {
        "description": "Replace a profile's axis weights. Needs the profiles:write scope.",
        "method": "PATCH",
        "path": "/v1/profiles/{name}/weights",
        "body": "weights",
        "schema": {
            "name": {"type": "string"},
            "weights": {"type": "object", "additionalProperties": {"type": "number"}},
        },
        "required": ["name", "weights"],
    },
    "set_ship": {
        "description": "How many models a profile ships (1-10). Needs the profiles:write scope.",
        "method": "PUT",
        "path": "/v1/profiles/{name}/settings",
        "body": "*",
        "schema": {"name": {"type": "string"}, "ship": {"type": "integer"}},
        "required": ["name", "ship"],
    },
    "evaluate": {
        "description": "Dry run: the ranking, chain and decision a profile would produce now.",
        "method": "POST",
        "path": "/v1/profiles/{name}/evaluate",
        "schema": {"name": {"type": "string"}},
        "required": ["name"],
    },
    "recommend": {
        "description": "The top n reachable models for a profile.",
        "method": "GET",
        "path": "/v1/recommend",
        "query": ["profile", "n", "reachable_only"],
        "schema": {
            "profile": {"type": "string"},
            "n": {"type": "integer", "default": 3},
            "reachable_only": {"type": "boolean", "default": True},
        },
        "required": ["profile"],
    },
    "get_ranking": {
        "description": "The latest full ranking for a profile, with per-axis contributions.",
        "method": "GET",
        "path": "/v1/rankings/{profile}",
        "schema": {"profile": {"type": "string"}},
        "required": ["profile"],
    },
    "explain": {
        "description": "Why #1 leads: contributions, coverage and the smallest weight flip.",
        "method": "GET",
        "path": "/v1/rankings/{profile}/explain",
        "schema": {"profile": {"type": "string"}},
        "required": ["profile"],
    },
    "report_outcome": {
        "description": "Report call outcomes back as telemetry. Needs the telemetry scope.",
        "method": "POST",
        "path": "/v1/telemetry",
        "body": "events",
        "schema": {"events": {"type": "array", "items": {"type": "object"}}},
        "required": ["events"],
    },
    "list_models": {
        "description": "One page of the catalogue, optionally for one modality.",
        "method": "GET",
        "path": "/v1/models",
        "query": ["modality", "reachable", "q", "limit", "cursor"],
        "schema": {
            "modality": {"type": "string"},
            "reachable": {"type": "boolean"},
            "q": {"type": "string"},
            "limit": {"type": "integer", "default": 100},
            "cursor": {"type": "string"},
        },
    },
    "leaderboard": {
        "description": "The ranking for one modality, best first.",
        "method": "GET",
        "path": "/v1/leaderboard",
        "query": ["modality", "metric"],
        "schema": {"modality": {"type": "string"}, "metric": {"type": "string"}},
        "required": ["modality"],
    },
    "status": {
        "description": "When Sieve last pulled and ran, and what is scheduled.",
        "method": "GET",
        "path": "/v1/status",
        "query": ["sections"],
        "schema": {"sections": {"type": "string"}},
    },
    "list_runs": {
        "description": "The most recent runs of the loop's steps.",
        "method": "GET",
        "path": "/v1/runs",
        "query": ["limit", "step"],
        "schema": {"limit": {"type": "integer", "default": 20}, "step": {"type": "string"}},
    },
    "export_config": {
        "description": "This account's configuration as a bundle (no secrets).",
        "method": "GET",
        "path": "/v1/config",
        "query": ["sections"],
        "schema": {"sections": {"type": "string"}},
    },
    "apply": {
        "description": "Write the current chains to their targets. Needs the apply scope.",
        "method": "POST",
        "path": "/v1/apply",
        "body": "*",
        "schema": {
            "profiles": {"type": "array", "items": {"type": "string"}},
            "targets": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def input_schema(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": spec.get("schema", {}),
        "required": spec.get("required", []),
    }


class Bridge:
    """Calls the API in-process. Nothing here re-implements a route."""

    def __init__(self, token: str | None = None) -> None:
        self.app = create_app()
        self.token = token if token is not None else os.environ.get(TOKEN_ENV)

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"} if self.token else {}

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        spec = TOOLS.get(name)
        if spec is None:
            return {"error": {"code": "no_such_tool", "message": name}}
        path = spec["path"].format(**{k: arguments.get(k, "") for k in ("name", "profile")})
        params = {k: arguments[k] for k in spec.get("query", []) if arguments.get(k) is not None}
        body: Any = None
        key = spec.get("body")
        if key == "*":
            body = {k: v for k, v in arguments.items() if k in spec.get("schema", {})}
        elif key:
            body = arguments.get(key)

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sieve.local") as client:
            response = await client.request(
                spec["method"], path, params=params, json=body, headers=self._headers()
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"code": "bad_response", "message": response.text[:400]}}

        return payload


def build_server(bridge: Bridge | None = None) -> Any:
    """An `MCPServer` carrying the ten tools of PLAN section 7, and five reads."""
    from mcp.server.mcpserver import MCPServer

    from sieve import __version__

    hub = bridge or Bridge()
    server = MCPServer(
        name="sieve",
        title="Sieve",
        version=__version__,
        instructions=(
            "Weighted model selection. Read a profile, move its weights, evaluate the "
            "effect, and ship the chain. Every write is logged as a decision with your "
            "token name as the actor."
        ),
    )

    async def list_profiles(modality: str | None = None) -> Any:
        """Every profile, optionally for one modality."""
        return await hub.call("list_profiles", {"modality": modality})

    async def get_profile(name: str) -> Any:
        """One profile: its weights and how many models it ships."""
        return await hub.call("get_profile", {"name": name})

    async def set_weights(name: str, weights: dict[str, float]) -> Any:
        """Replace a profile's axis weights (they must sum to 1). Needs profiles:write."""
        return await hub.call("set_weights", {"name": name, "weights": weights})

    async def set_ship(name: str, ship: int) -> Any:
        """How many models a profile ships, 1 to 10. Needs profiles:write."""
        return await hub.call("set_ship", {"name": name, "ship": ship})

    async def evaluate(name: str) -> Any:
        """Dry run: the ranking, chain and decision this profile would produce now."""
        return await hub.call("evaluate", {"name": name})

    async def recommend(profile: str, n: int = 3, reachable_only: bool = True) -> Any:
        """The top n models for a profile, reachable ones only by default."""
        return await hub.call(
            "recommend", {"profile": profile, "n": n, "reachable_only": reachable_only}
        )

    async def get_ranking(profile: str) -> Any:
        """The latest full ranking for a profile, with per-axis contributions."""
        return await hub.call("get_ranking", {"profile": profile})

    async def explain(profile: str) -> Any:
        """Why #1 leads: contributions, coverage, the gap to #2 and the flip line."""
        return await hub.call("explain", {"profile": profile})

    async def report_outcome(events: list[dict[str, Any]]) -> Any:
        """Report call outcomes back as telemetry. Needs the telemetry scope."""
        return await hub.call("report_outcome", {"events": events})

    async def list_models(
        modality: str | None = None,
        reachable: bool | None = None,
        q: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Any:
        """One page of the catalogue, optionally for one modality."""
        args = {"modality": modality, "reachable": reachable, "q": q, "limit": limit, "cursor": cursor}
        return await hub.call("list_models", args)

    async def leaderboard(modality: str, metric: str | None = None) -> Any:
        """The ranking for one modality, best first."""
        return await hub.call("leaderboard", {"modality": modality, "metric": metric})

    async def status(sections: str | None = None) -> Any:
        """When Sieve last pulled and ran, and what is scheduled."""
        return await hub.call("status", {"sections": sections})

    async def list_runs(limit: int = 20, step: str | None = None) -> Any:
        """The most recent runs of the loop's steps."""
        return await hub.call("list_runs", {"limit": limit, "step": step})

    async def export_config(sections: str | None = None) -> Any:
        """This account's configuration as a bundle (no secrets)."""
        return await hub.call("export_config", {"sections": sections})

    async def apply(profiles: list[str] | None = None, targets: list[str] | None = None) -> Any:
        """Write the current chains to their targets. Needs the apply scope."""
        return await hub.call("apply", {"profiles": profiles, "targets": targets})

    @server.resource(
        "sieve://operating-guide",
        name="Sieve operating guide",
        description="Complete operating guide for an autonomous Sieve agent.",
        mime_type="text/markdown",
    )
    def operating_guide() -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "OPERATING.md").read_text()

    for fn in (
        list_profiles,
        get_profile,
        set_weights,
        set_ship,
        evaluate,
        recommend,
        get_ranking,
        explain,
        report_outcome,
        apply,
        list_models,
        leaderboard,
        status,
        list_runs,
        export_config,
    ):
        server.add_tool(fn, name=fn.__name__, description=(fn.__doc__ or "").strip())

    return server


def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8111) -> int:
    """Run the server: `stdio` for an editor, `http` for streamable HTTP."""
    server = build_server()
    if transport == "http":
        server.run(transport="streamable-http", host=host, port=port)
    else:
        server.run(transport="stdio")
    return 0
