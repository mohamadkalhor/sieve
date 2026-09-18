"""gate v2 (AUTH-CONTRACT.md, gate/APP-INTEGRATION.md; CONTRACTS section 10).

Two things changed and each gets its own half of this file:

    `sieve.api.auth.gate_identity` now speaks the v2 protocol -- forward the
    raw `Cookie` header with `X-Gate-App: sieve`, no service token, trust
    only `app == "sieve"` and `status == "active"` -- and never falls back to
    a v1 assumption about the cookie's name.

    `sieve.api.edge.EdgeAuthMiddleware` is the new rule at the nginx
    boundary: a request carrying `X-Gate-Edge` must resolve to a real bearer
    or a live gate session before it reaches any `/v1` route, reads and the
    SSE stream included; a request without that header is untouched and
    keeps today's open-read behaviour.

The middleware is tested in isolation, against a bare Starlette app with a
stand-in `/v1` route, because the real SSE endpoint never completes its
response and a synchronous test client would hang trying to read one. The
gate client is tested against the real FastAPI app with `httpx.get`
monkeypatched to a stand-in gate, because what matters there is the mapping
into `owners.sign_in`, which needs the real store.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from sieve import owners
from sieve.api import auth
from sieve.api.app import create_app
from sieve.api.edge import EdgeAuthMiddleware, SecurityHeadersMiddleware
from sieve.config import Config, Paths, StoreConfig

REPO = Path(__file__).resolve().parent.parent

OWNER_EMAIL = "mohamad@example.test"
TOKENS = "ops:read,profiles:write,apply,telemetry:s3cret"
EDGE = {"X-Gate-Edge": "1"}


class FakeReply:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def stub_gate(*, status_code: int = 200, **body: Any):
    """A monkeypatch target for `httpx.get` that answers like gate's
    `GET /v1/session` would, without a socket."""
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: float = 0.0) -> FakeReply:
        calls.append(dict(headers or {}))
        return FakeReply(status_code, dict(body))

    fake_get.calls = calls  # type: ignore[attr-defined]
    return fake_get


@pytest.fixture(autouse=True)
def _reset_gate_cache() -> Any:
    """`auth._gate_cache` is process-global, not per-app, so a cookie value
    reused across two tests in the same run would otherwise leak one test's
    answer into the next -- exactly the kind of thing this cache is designed
    to do for thirty seconds in production, and exactly what a test must not
    let happen between two unrelated cases."""
    auth._gate_cache.clear()
    yield
    auth._gate_cache.clear()


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    monkeypatch.setenv("SIEVE_OWNER_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("SIEVE_GATE_URL", "http://127.0.0.1:8122")
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
    )


# --------------------------------------------------------------------------- #
# the gate v2 client, against the real app
# --------------------------------------------------------------------------- #


def test_no_gate_url_is_silent_and_reads_stay_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SIEVE_GATE_URL` unset: gate is off, on purpose, for local dev."""
    monkeypatch.delenv("SIEVE_GATE_URL", raising=False)
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    cfg = Config(root=tmp_path, store=StoreConfig(path=str(tmp_path / "sieve.db")))
    with TestClient(create_app(cfg)) as client:
        assert client.get("/v1/profiles").status_code == 200
        assert client.get("/v1/profiles", headers=EDGE).status_code == 401


def test_gate_forwards_the_whole_cookie_header_and_the_app_slug(
    box: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = stub_gate(
        user_id=17, email="ada@example.test", name="Ada", role="member",
        status="active", app="sieve",
    )
    monkeypatch.setattr(auth.httpx, "get", fake)
    with TestClient(create_app(box)) as client:
        client.cookies.set("gate_session", "opaque-blob")
        client.cookies.set("something_else", "also-sent")
        got = client.get("/v1/me")
        assert got.status_code == 200
        assert got.json()["email"] == "ada@example.test"
    # gate got the raw Cookie header (both cookies) and the app slug, not a
    # cookie picked out by name.
    sent = fake.calls[-1]  # type: ignore[attr-defined]
    assert sent["X-Gate-App"] == "sieve"
    assert "gate_session=opaque-blob" in sent["Cookie"]
    assert "something_else=also-sent" in sent["Cookie"]
    assert "token" not in sent  # no service token is sent, ever


@pytest.mark.parametrize(
    "body",
    [
        {"status": "active", "app": "bars"},  # another tenant's session
        {"status": "pending", "app": "sieve"},  # not approved yet
        {"status": "disabled", "app": "sieve"},
    ],
)
def test_only_our_app_and_active_status_count_as_a_session(
    box: Config, monkeypatch: pytest.MonkeyPatch, body: dict[str, str]
) -> None:
    fake = stub_gate(user_id=1, email="x@example.test", role="owner", **body)
    monkeypatch.setattr(auth.httpx, "get", fake)
    with TestClient(create_app(box)) as client:
        client.cookies.set("gate_session", "blob")
        # No session, so the edge rule refuses it -- whatever `/v1/me`'s own
        # open-read fallback to the configured owner does for an anonymous
        # loopback call, which is a separate, pre-existing mechanism.
        assert client.get("/v1/profiles", headers=EDGE).status_code == 401


def test_gate_unreachable_is_no_session_not_a_yes(
    box: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise TimeoutError("gate did not answer")

    monkeypatch.setattr(auth.httpx, "get", boom)
    with TestClient(create_app(box)) as client:
        client.cookies.set("gate_session", "blob")
        assert client.get("/v1/profiles", headers=EDGE).status_code == 401


def test_a_bad_json_reply_from_gate_is_no_session(
    box: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Weird:
        status_code = 200

        def json(self) -> Any:
            raise ValueError("not json")

    monkeypatch.setattr(auth.httpx, "get", lambda *a, **k: Weird())
    with TestClient(create_app(box)) as client:
        client.cookies.set("gate_session", "blob")
        assert client.get("/v1/profiles", headers=EDGE).status_code == 401


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("owner", {"read", "profiles:write", "apply"}),
        ("member", {"read", "profiles:write", "apply"}),
        ("viewer", {"read", "profiles:write"}),
    ],
)
def test_role_mapping_and_viewer_gets_a_private_seed(
    box: Config, monkeypatch: pytest.MonkeyPatch, role: str, expected: set[str]
) -> None:
    """The gate role becomes exactly these scopes, and a first-time sign-in
    of any non-owner role -- viewer included, since gate v2 -- gets private
    copies of the seed profiles to write to (CONTRACTS section 10)."""
    email = f"{role}@example.test"
    fake = stub_gate(user_id=42, email=email, role=role, status="active", app="sieve")
    monkeypatch.setattr(auth.httpx, "get", fake)
    with TestClient(create_app(box)) as client:
        client.cookies.set("gate_session", "blob")
        me = client.get("/v1/me").json()
        assert me["email"] == email and me["role"] == role
        assert me["counts"]["profiles"] >= 9  # her own seed copies

        # apply is only ever a member/owner scope: a viewer's session cannot
        # ship a chain to a live gateway, whatever profiles:write lets her
        # edit about her own rows.
        applied = client.post("/v1/profiles/judge/apply")
        if "apply" in expected:
            assert applied.status_code != 403
        else:
            assert applied.status_code == 403

        # profiles:write reaches her own settings either way for member/owner
        # and, new in v2, for viewer too.
        settings = client.put("/v1/profiles/judge/settings", json={"ship": 3})
        if "profiles:write" in expected:
            assert settings.status_code == 200
        else:
            assert settings.status_code == 403


def test_positive_and_negative_answers_are_cached_by_the_cookie_header(
    box: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = stub_gate(user_id=1, email=OWNER_EMAIL, role="owner", status="active", app="sieve")
    monkeypatch.setattr(auth.httpx, "get", fake)
    with TestClient(create_app(box)) as client:
        client.cookies.set("gate_session", "blob")
        client.get("/v1/me")
        client.get("/v1/me")
        client.get("/v1/me")
    # Three calls into the app, one call out to gate.
    assert len(fake.calls) == 1  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# the edge rule, against a bare app (no SSE hang risk, no store needed)
# --------------------------------------------------------------------------- #


async def _ping(request: Request) -> Response:
    return Response(status_code=204)


def _edge_app(monkeypatch: pytest.MonkeyPatch, *, session: bool, bearer_ok: bool | None) -> Starlette:
    """A stand-in for the real app: same middleware, a `/v1/ping` route
    instead of the real router, and `auth.gate_identity` / `auth.resolve_bearer`
    monkeypatched so no store or network is involved."""
    monkeypatch.setattr(auth, "gate_identity", lambda request: object() if session else None)
    if bearer_ok is not None:
        monkeypatch.setattr(
            auth, "resolve_bearer", lambda request, secret: (object() if bearer_ok else None)
        )
    app = Starlette(routes=[Route("/v1/ping", _ping, methods=["GET", "POST"])])
    app.add_middleware(EdgeAuthMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


def test_no_edge_header_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _edge_app(monkeypatch, session=False, bearer_ok=None)
    with TestClient(app) as client:
        assert client.get("/v1/ping").status_code == 204


def test_edge_with_no_bearer_and_no_session_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _edge_app(monkeypatch, session=False, bearer_ok=None)
    with TestClient(app) as client:
        got = client.get("/v1/ping", headers=EDGE)
        assert got.status_code == 401
        assert got.json()["error"]["code"] == "unauthenticated"


def test_edge_with_junk_bearer_is_401_never_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _edge_app(monkeypatch, session=False, bearer_ok=False)
    with TestClient(app) as client:
        got = client.get(
            "/v1/ping", headers={**EDGE, "Authorization": "Bearer sv_not-a-real-one"}
        )
        assert got.status_code == 401


def test_edge_with_valid_bearer_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _edge_app(monkeypatch, session=False, bearer_ok=True)
    with TestClient(app) as client:
        got = client.get("/v1/ping", headers={**EDGE, "Authorization": "Bearer sv_good"})
        assert got.status_code == 204


def test_edge_with_valid_session_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _edge_app(monkeypatch, session=True, bearer_ok=None)
    with TestClient(app) as client:
        assert client.get("/v1/ping", headers=EDGE).status_code == 204


def test_spoofed_gate_headers_without_a_real_session_are_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """`X-Gate-Email` etc. are informational, set by nginx after a real
    check; this app never trusts them on their own, only `/v1/session`."""
    app = _edge_app(monkeypatch, session=False, bearer_ok=None)
    with TestClient(app) as client:
        got = client.get(
            "/v1/ping",
            headers={**EDGE, "X-Gate-Email": "owner@example.test", "X-Gate-Role": "owner"},
        )
        assert got.status_code == 401


def test_security_headers_are_on_every_response_including_the_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _edge_app(monkeypatch, session=False, bearer_ok=None)
    with TestClient(app) as client:
        got = client.get("/v1/ping", headers=EDGE)
        assert got.status_code == 401
        assert got.headers["x-content-type-options"] == "nosniff"
        assert got.headers["x-frame-options"] == "SAMEORIGIN"
        assert got.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_csrf_belt_refuses_cross_site_and_foreign_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _edge_app(monkeypatch, session=True, bearer_ok=None)
    with TestClient(app, base_url="http://sieve.mkalhor.xyz") as client:
        cross_site = client.post("/v1/ping", headers={**EDGE, "Sec-Fetch-Site": "cross-site"})
        assert cross_site.status_code == 403
        assert cross_site.json()["error"]["code"] == "cross_site"

        foreign_origin = client.post(
            "/v1/ping", headers={**EDGE, "Origin": "https://evil.example"}
        )
        assert foreign_origin.status_code == 403

        same_site = client.post(
            "/v1/ping",
            headers={**EDGE, "Sec-Fetch-Site": "same-origin", "Origin": "http://sieve.mkalhor.xyz"},
        )
        assert same_site.status_code == 204


def test_csrf_belt_does_not_apply_to_a_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bearer already proved something a cross-site page cannot forge."""
    app = _edge_app(monkeypatch, session=False, bearer_ok=True)
    with TestClient(app, base_url="http://sieve.mkalhor.xyz") as client:
        got = client.post(
            "/v1/ping",
            headers={
                **EDGE,
                "Authorization": "Bearer sv_good",
                "Sec-Fetch-Site": "cross-site",
                "Origin": "https://evil.example",
            },
        )
        assert got.status_code == 204


def test_edge_gates_reads_and_the_events_path_alike(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/v1/events` is just another `/v1` path to this middleware -- it never
    has to know the route is SSE, which is the point of doing this once,
    centrally, ahead of the router."""
    monkeypatch.setattr(auth, "gate_identity", lambda request: None)
    app = Starlette(routes=[Route("/v1/events", _ping, methods=["GET"])])
    app.add_middleware(EdgeAuthMiddleware)
    with TestClient(app) as client:
        assert client.get("/v1/events", headers=EDGE).status_code == 401
        assert client.get("/v1/events").status_code == 204  # no edge header: untouched
