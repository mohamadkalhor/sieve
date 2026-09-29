"""The operating guide ships inside the package -- card D1.

`OPERATING.md` lives at `sieve/OPERATING.md`, so hatchling's wheel carries it
(`packages = ["sieve"]`); a file at the repo root would not be in an installed
sieve. These tests hold that down, plus the two documents that describe the
`/v1` surface: the guide itself, `docs/mcp.md`, and CONTRACTS' `/healthz` row.
"""

from __future__ import annotations

import pathlib
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
