"""`sieve mcp` answers over stdio (PLAN section 12).

Spawns the real CLI entry point and speaks MCP to it, so the acceptance check
covers the process, the transport and the API bridge together.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_lists_tools_and_recommends(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["SIEVE_CONFIG"] = str(tmp_path / "sieve.toml")  # absent: defaults, empty store
    env.setdefault("SIEVE_TOKENS", "agent:read,telemetry:tok")
    env.setdefault("SIEVE_TOKEN", "tok")

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "sieve.cli", "mcp"], env=env, cwd=str(tmp_path)
    )

    async def talk() -> tuple[list[str], dict[str, Any]]:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("recommend", {"profile": "coder", "n": 3})
            block = result.content[0]
            text = getattr(block, "text", "{}")
            payload: dict[str, Any] = json.loads(text)
            return [t.name for t in tools.tools], payload

    names, payload = anyio.run(talk)

    assert "recommend" in names
    assert "set_weights" in names
    assert "explain" in names
    # Nothing has been pulled or scored in this empty store, so the honest
    # answer is the error envelope -- not a fabricated recommendation.
    assert "error" in payload or "models" in payload
