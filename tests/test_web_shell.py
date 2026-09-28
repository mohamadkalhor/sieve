"""The static web build behind FastAPI: one address per page, and scripts
that are either scripts or a 404 -- never the HTML shell."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config
from tests.test_api_acceptance import workspace  # noqa: F401  (the fixture)


def _build(root: Path) -> Path:
    web = root / "webbuild"
    (web / "_app" / "immutable").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html>shell", encoding="utf-8")
    (web / "seats.html").write_text(
        '<!doctype html><script src="./_app/immutable/a.js">', encoding="utf-8"
    )
    (web / "_app" / "immutable" / "a.js").write_text("export {}", encoding="utf-8")
    return web


def _client(workspace: Config, tmp_path: Path) -> TestClient:  # noqa: F811
    workspace.server.web = str(_build(tmp_path))
    return TestClient(create_app(workspace))


def test_a_trailing_slash_redirects_to_the_one_address(workspace: Config, tmp_path: Path) -> None:  # noqa: F811
    with _client(workspace, tmp_path) as client:
        response = client.get("/seats/?seat=build", follow_redirects=False)
        assert response.status_code == 308
        assert response.headers["location"] == "/seats?seat=build"

        page = client.get("/seats")
        assert page.status_code == 200
        assert "a.js" in page.text, "the prerendered page, not the shell"


def test_a_missing_script_is_a_404_not_the_shell(workspace: Config, tmp_path: Path) -> None:  # noqa: F811
    with _client(workspace, tmp_path) as client:
        assert client.get("/_app/immutable/a.js").text == "export {}"
        # what `/seats/` used to resolve `./_app/...` to
        nested = client.get("/seats/_app/immutable/a.js")
        assert nested.status_code == 404
        assert client.get("/_app/immutable/gone.js").status_code == 404
        # a deep link still gets the shell
        deep = client.get("/rankings/coder")
        assert deep.status_code == 200
        assert "shell" in deep.text
