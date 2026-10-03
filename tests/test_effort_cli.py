"""EFFORT.md section 5, outside the API: `sieve score --effort`, the `sieve
check` warning, and the MCP settings tool passing `effort` through."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import anyio
import pytest

from sieve import cli
from sieve.api.app import create_app
from sieve.config import Config
from sieve.contracts import Chain
from sieve.mcp.server import TOOLS, Bridge
from sieve.profiles import control
from sieve.store import Store
from tests.test_api_acceptance import workspace  # noqa: F401  (the fixture)

SOL = "openai/gpt-5-6-sol"


def test_score_takes_an_effort(
    workspace: Config,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_config", lambda args: workspace)
    assert cli.main(["score", "--profile", "coder", "--effort", "medium", "--limit", "50"]) == 0
    out = capsys.readouterr().out
    assert "effort medium" in out
    assert f"scored as {SOL}-medium (medium, exact)" in out

    assert cli.main(["score", "--profile", "coder", "--limit", "50"]) == 0
    assert "scored as" not in capsys.readouterr().out


def test_check_warns_on_a_seat_with_no_effort_shipping_a_multi_effort_model(
    workspace: Config,  # noqa: F811
) -> None:
    store = Store(workspace.db_path)
    try:
        store.put_chain(Chain(profile="coder", computed_at=datetime.now(UTC), primary=SOL))
        control.seed(store, workspace.profiles_dir)
        profiles = control.profiles(store)
        notes = cli._seats_without_effort(store, profiles)
        assert len(notes) == 1
        assert "'coder'" in notes[0] and SOL in notes[0]
        coder = next(p for p in profiles if p.name == "coder")
        fixed = [coder.model_copy(update={"effort": "medium"})]
        assert cli._seats_without_effort(store, fixed) == []
    finally:
        store.close()


def test_the_mcp_settings_tool_passes_effort_through(workspace: Config) -> None:  # noqa: F811
    assert "effort" in TOOLS["set_ship"]["schema"]
    bridge = Bridge.__new__(Bridge)
    bridge.app = create_app(workspace)
    bridge.token = "s3cret"

    async def go() -> Any:
        return await bridge.call("set_ship", {"name": "coder", "ship": 3, "effort": "high"})

    answer = anyio.run(go)
    assert answer.get("ship") == 3, answer
    assert answer["effort"] == "high"
