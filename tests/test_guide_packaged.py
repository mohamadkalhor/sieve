"""The operating guide ships inside the package -- card D1.

`OPERATING.md` lives at `sieve/OPERATING.md`, so hatchling's wheel carries it
(`packages = ["sieve"]`); a file at the repo root would not be in an installed
sieve. These tests hold that down, plus the two documents that describe the
`/v1` surface: the guide itself, `docs/mcp.md`, and CONTRACTS' `/healthz` row.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import pytest
from fastapi.testclient import TestClient

import sieve
from sieve.api.app import create_app
from sieve.config import default_config

REPO = pathlib.Path(__file__).resolve().parent.parent
GUIDE = REPO / "sieve" / "OPERATING.md"
CONTRACTS = REPO / "CONTRACTS.md"


@pytest.fixture
def client(tmp_path: pathlib.Path) -> TestClient:
    """The app built the way `tests/test_foundation.py` builds it."""
    return TestClient(create_app(default_config(tmp_path)))


def test_the_guide_lives_inside_the_package() -> None:
    assert (pathlib.Path(sieve.__file__).parent / "OPERATING.md").is_file()


def test_get_guide_serves_the_packaged_file(client: TestClient) -> None:
    answer = client.get("/v1/guide")
    assert answer.status_code == 200
    assert answer.text.startswith("# Operating Sieve as an agent")


def test_the_wheel_would_carry_the_guide() -> None:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    packages = data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["sieve"]


def test_mcp_doc_lists_every_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docs/mcp.md`'s table is the only list of the hosted tools; a tool the
    registry serves but the doc does not name is a tool nobody calls."""
    monkeypatch.setenv("AGENT_V1", "1")
    from sieve.api import v1_tools

    names = sorted(tool.name for tool in v1_tools.registry().tools())
    assert len(names) == 15
    doc = (REPO / "docs" / "mcp.md").read_text()
    for name in names:
        assert f"`{name}`" in doc, f"docs/mcp.md does not list {name}"


def test_every_route_the_guide_names_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """The guide is the only context an arriving agent has: every route it
    names has to be a route the box answers. `AGENT_V1=1`, because that is the
    surface the section documents."""
    monkeypatch.setenv("AGENT_V1", "1")
    app = create_app(default_config(tmp_path))
    paths = app.openapi()["paths"]
    named = re.findall(r"(GET|POST|PUT|PATCH|DELETE) (/v1/[A-Za-z0-9_{}/.-]+)", GUIDE.read_text())
    assert named, "the guide names no /v1 route at all"
    for method, path in named:
        if path.startswith("/v1/_crash"):
            continue
        assert path in paths, f"the guide names {method} {path}, which is not a route"
        assert method.lower() in paths[path], f"{path} does not answer {method}"
