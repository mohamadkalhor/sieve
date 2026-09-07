"""`sieve mcp` — the MCP server (CONTRACTS section 7).

The tools of PLAN section 7, one to one with the API. Every tool delegates to
the `/v1` layer over an in-process ASGI transport carrying `SIEVE_TOKEN`, so
scopes, validation and the decision log are exactly the API's -- there is no
second code path to keep in step.
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
        "description": "One profile: weights, constraints, shape and policy.",
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
    "set_policy": {
        "description": "Change a profile's switching policy. Needs the profiles:write scope.",
        "method": "PATCH",
        "path": "/v1/profiles/{name}/policy",
        "body": "policy",
        "schema": {"name": {"type": "string"}, "policy": {"type": "object"}},
        "required": ["name", "policy"],
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
        "path": "/v1/rankings/{profile}",
        "schema": {"profile": {"type": "string"}},
        "required": ["profile"],
        "explain": True,
    },
    "report_outcome": {
        "description": "Report call outcomes back as telemetry. Needs the telemetry scope.",
        "method": "POST",
        "path": "/v1/telemetry",
        "body": "events",
        "schema": {"events": {"type": "array", "items": {"type": "object"}}},
        "required": ["events"],
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
        params = {
            k: arguments[k] for k in spec.get("query", []) if arguments.get(k) is not None
        }
        body: Any = None
        key = spec.get("body")
        if key == "*":
            body = {k: v for k, v in arguments.items() if k in spec.get("schema", {})}
        elif key:
            body = arguments.get(key)

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://sieve.local"
        ) as client:
            response = await client.request(
                spec["method"], path, params=params, json=body, headers=self._headers()
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"code": "bad_response", "message": response.text[:400]}}

        if spec.get("explain") and isinstance(payload, dict) and payload.get("ranks"):
            return _explanation(payload)
        return payload


def _explanation(ranking: dict[str, Any]) -> dict[str, Any]:
    """`explain` is `get_ranking` reduced to why the leader leads."""
    ranked = [r for r in ranking.get("ranks", []) if r.get("position")]
    if not ranked:
        return {"profile": ranking.get("profile"), "explanation": "nothing ranked yet"}
    top = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    return {
        "profile": ranking.get("profile"),
        "leader": top["model_id"],
        "final": top["final"],
        "gap": (top["final"] - runner["final"]) if runner else None,
        "runner_up": runner["model_id"] if runner else None,
        "contributions": top.get("axes", []),
        "confidence": top.get("confidence"),
        "flip": top.get("flip"),
    }


def build_server(bridge: Bridge | None = None) -> Any:
    """An `MCPServer` carrying the ten tools of PLAN section 7."""
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
        """One profile: weights, constraints, shape and policy."""
        return await hub.call("get_profile", {"name": name})

    async def set_weights(name: str, weights: dict[str, float]) -> Any:
        """Replace a profile's axis weights (they must sum to 1). Needs profiles:write."""
        return await hub.call("set_weights", {"name": name, "weights": weights})

    async def set_policy(name: str, policy: dict[str, Any]) -> Any:
        """Change a profile's switching policy. Needs profiles:write."""
        return await hub.call("set_policy", {"name": name, "policy": policy})

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

    async def apply(
        profiles: list[str] | None = None, targets: list[str] | None = None
    ) -> Any:
        """Write the current chains to their targets. Needs the apply scope."""
        return await hub.call("apply", {"profiles": profiles, "targets": targets})

    for fn in (
        list_profiles,
        get_profile,
        set_weights,
        set_policy,
        evaluate,
        recommend,
        get_ranking,
        explain,
        report_outcome,
        apply,
    ):
        server.add_tool(fn, name=fn.__name__, description=(fn.__doc__ or "").strip())

    return server


def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8111) -> int:
    """Run the server: `stdio` for an editor, `http` for streamable HTTP."""
    server = build_server()
    if transport == "http":
        server.settings.host = host  # type: ignore[attr-defined]
        server.settings.port = port  # type: ignore[attr-defined]
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")
    return 0

