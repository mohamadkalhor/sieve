"""A03 -- a connector may only call a host the config named, at the address it checked.

`base_url` is data. A row that says `http://127.0.0.1:20128` is the gateway this
box happens to run, and the same row with `169.254.169.254` is the cloud
metadata service; nothing about the string tells them apart, so the config says
which `host:port` a kind may be pointed at and the call is made to the address
that was checked rather than to whatever the name says when the socket layer
asks a second time.

What these tests hold to:

* `[connectors.hosts]` is read from `sieve.toml`, compared as `host:port` with
  the scheme's default port filled in, and an entry that could never match is
  refused where it is written;
* the connector routes and the whole-configuration bundle give the same answer
  -- 422 `host_not_allowed`, naming the list to edit -- and store nothing;
* an adapter built without a list still cannot dial loopback or a private
  address, and the metadata address is refused even when it is on the list;
* the name is resolved exactly once per call, so a name that answers differently
  the second time (DNS rebinding) reaches nothing;
* the socket goes to the checked address while the `Host` header, SNI and
  certificate verification stay on the name.

Everything here runs against stubs on loopback. Nothing reaches a real box.
"""

from __future__ import annotations

import json
import shutil
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config, ConnectorConfig, Paths, StoreConfig, load_config
from sieve.connectors.base import ConnectorError
from sieve.connectors.openai_compat import OpenAICompatConnector
from sieve.contracts import Connector
from sieve.pinned import (
    AllowedHosts,
    AddressRefused,
    HostNotAllowed,
    PinnedTransport,
    host_port,
    pinned_client,
)
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent

AUTH = {"authorization": "Bearer root-secret"}
TOKENS = "root:read,profiles:write,apply,admin:root-secret"

#: Nothing ever listens on port 1, and it is loopback: the address this box must
#: not dial without being told to.
UNLISTED = "http://127.0.0.1:1"


# --------------------------------------------------------------------------- #
# stubs
# --------------------------------------------------------------------------- #


class Catalogue(BaseHTTPRequestHandler):
    """A gateway that records what reached it."""

    seen: ClassVar[list[tuple[str, str]]] = []

    def do_GET(self) -> None:  # noqa: N802 -- http.server's spelling
        Catalogue.seen.append((self.headers.get("host", ""), self.path))
        body = json.dumps({"object": "list", "data": [{"id": "openai/gpt-5", "object": "model"}]})
        raw = body.encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: Any) -> None:
        """Silence: the stub's own log is not this test's business."""


def _serve(handler: type[BaseHTTPRequestHandler], tls: tuple[str, str] | None = None) -> Any:
    server = HTTPServer(("127.0.0.1", 0), handler)
    if tls is not None:
        cert, key = tls
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def router() -> Any:
    """A stub catalogue on a loopback port, reset between tests."""
    Catalogue.seen = []
    server = _serve(Catalogue)
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def resolving(monkeypatch: pytest.MonkeyPatch, **names: str) -> list[tuple[str, int]]:
    """A name that answers with the address this test says it does.

    Nothing here looks a public name up: a test that depends on somebody else's
    DNS is a test that fails on somebody else's Tuesday.
    """
    real = socket.getaddrinfo
    asked: list[tuple[str, int]] = []

    def answering(host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        asked.append((host, port))
        return real(names.get(host, host), port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", answering)
    return asked


def connector(base_url: str, **over: Any) -> Connector:
    body: dict[str, Any] = {
        "id": "c1",
        "name": "gateway",
        "kind": "openai_compat",
        "base_url": base_url,
        "read": True,
        "created_at": "2026-09-27T00:00:00Z",
    }
    body.update(over)
    return Connector(**body)


@pytest.fixture
def box(tmp_path: Path, router: str) -> Config:
    """A box whose `[connectors.hosts]` names the stub and nothing else."""
    profiles = tmp_path / "profiles"
    axes = tmp_path / "axes"
    shutil.copytree(REPO / "profiles", profiles)
    shutil.copytree(REPO / "data" / "axes", axes)
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(axes), profiles=str(profiles)),
        connectors=ConnectorConfig(hosts={"openai_compat": [host_port(router)]}),
    )


@pytest.fixture
def client(box: Config, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SIEVE_TOKENS", TOKENS)
    with TestClient(create_app(box)) as opened:
        yield opened


# --------------------------------------------------------------------------- #
# the config
# --------------------------------------------------------------------------- #


def test_the_hosts_table_is_read_from_the_config_file(tmp_path: Path) -> None:
    (tmp_path / "sieve.toml").write_text(
        """
[connectors.hosts]
openai_compat = ["127.0.0.1:20128", "LOCALHOST:20128"]
ninerouter = ["gateway.internal:443"]
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path / "sieve.toml")

    # Lower-cased on the way in, and in the order they were written: the screen
    # shows the list back to whoever edits the file.
    assert cfg.allowed_hosts.allowed("openai_compat") == ["127.0.0.1:20128", "localhost:20128"]
    assert cfg.allowed_hosts.kinds() == ["ninerouter", "openai_compat"]
    # A kind nobody named is not an error; it simply allows nothing.
    assert cfg.allowed_hosts.allowed("telepathy") == []
    assert cfg.connectors.hosts["ninerouter"] == ["gateway.internal:443"]


def test_an_entry_that_could_never_match_is_refused_where_it_is_written(
    tmp_path: Path,
) -> None:
    for bad in (
        "127.0.0.1",  # no port: not the thing a base_url is compared against
        "127.0.0.1:0",
        "127.0.0.1:70000",
        "127.0.0.1:http",
        "http://127.0.0.1:20128/x",  # a URL, not an entry
    ):
        path = tmp_path / "sieve.toml"
        path.write_text(f'[connectors.hosts]\nopenai_compat = ["{bad}"]\n', encoding="utf-8")
        with pytest.raises(ValueError, match="host:port"):
            load_config(path)


def test_a_default_port_is_a_port() -> None:
    # `https://box` and `box:443` are the same host:port, so an entry written
    # the short way still matches the long way.
    assert host_port("https://box/v1") == "box:443"
    assert host_port("http://box:80/v1") == "box:80"
    assert AllowedHosts({"openai_compat": ["box:443"]}).problem(
        "openai_compat", "https://box"
    ) is None

    with pytest.raises(HostNotAllowed):
        host_port("box:20128")


def test_the_sentence_names_the_list_to_edit() -> None:
    hosts = AllowedHosts({"openai_compat": ["127.0.0.1:20128"], "ninerouter": ["127.0.0.1:1"]})

    reason = hosts.problem("openai_compat", "http://gateway.internal:8080")
    assert reason is not None
    assert "gateway.internal:8080" in reason
    assert "openai_compat" in reason
    assert "127.0.0.1:20128" in reason  # what *is* allowed, in the same breath
    assert "127.0.0.1:1" not in reason  # ...and not what another kind is allowed
    assert "sieve.toml" in reason

    # The kind's own list is the one that counts, not the union of them all.
    assert hosts.problem("ninerouter", "http://127.0.0.1:1") is None
    with pytest.raises(HostNotAllowed):
        hosts.check("openai_compat", "http://127.0.0.1:1")

    # An empty table says so rather than printing an empty bracket.
    assert "nothing" in (AllowedHosts().problem("openai_compat", UNLISTED) or "")


# --------------------------------------------------------------------------- #
# the two doors into the table
# --------------------------------------------------------------------------- #


def test_the_connector_routes_refuse_a_host_the_config_does_not_name(
    client: TestClient, router: str
) -> None:
    refused = client.post(
        "/v1/connectors",
        json={"name": "metadata", "kind": "openai_compat", "base_url": "http://169.254.169.254"},
        headers=AUTH,
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "host_not_allowed"
    assert "169.254.169.254" in refused.json()["error"]["message"]

    # 422 is the code the connector routes already use for "this body is
    # well-formed and must not be stored", and nothing was stored.
    assert client.get("/v1/connectors").json() == []

    loopback = client.post(
        "/v1/connectors",
        json={"name": "spare", "kind": "openai_compat", "base_url": UNLISTED},
        headers=AUTH,
    )
    assert loopback.status_code == 422
    assert loopback.json()["error"]["code"] == "host_not_allowed"
    assert client.get("/v1/connectors").json() == []

    # The host the config *did* name goes through as it always did.
    allowed = client.post(
        "/v1/connectors",
        json={"name": "gateway", "kind": "openai_compat", "base_url": router},
        headers=AUTH,
    )
    assert allowed.status_code == 200, allowed.text
    assert [c["name"] for c in client.get("/v1/connectors").json()] == ["gateway"]


def test_the_bundle_door_gives_the_same_answer(client: TestClient, router: str) -> None:
    """A bundle travels; `[connectors.hosts]` does not. Same 422 either way."""
    document = client.get("/v1/config", headers=AUTH).json()

    def bundle(base_url: str) -> dict[str, Any]:
        carried = dict(document)
        carried["connectors"] = [
            {
                "name": "gateway",
                "kind": "openai_compat",
                "base_url": base_url,
                "secret": None,
                "admin_secret": None,
                "read": True,
                "write": False,
                "poll_minutes": 60,
            }
        ]
        return carried

    refused = client.put("/v1/config", json=bundle(UNLISTED), headers=AUTH)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "host_not_allowed"
    assert client.get("/v1/connectors", headers=AUTH).json() == []

    applied = client.put("/v1/config", json=bundle(router), headers=AUTH)
    assert applied.status_code == 200, applied.text
    assert client.get("/v1/connectors", headers=AUTH).json()[0]["base_url"] == router


def test_a_row_that_predates_the_rule_is_refused_when_it_is_called(
    box: Config, client: TestClient, router: str
) -> None:
    """The list gates the call as well as the write.

    A row can be in the database from before this rule existed, or written by a
    migration; `test` and `pull` are where it would otherwise reach out.
    """
    store = Store(box.store.path)
    made = store.add_connector(connector(UNLISTED))
    store.close()

    tested = client.post(f"/v1/connectors/{made.id}/test", headers=AUTH)
    assert tested.status_code == 200, tested.text
    outcome = tested.json()
    assert outcome["ok"] is False
    assert "not in [connectors.hosts]" in (outcome["error"] or "")

    # And the connector the config names is still reachable.
    allowed = Store(box.store.path).add_connector(connector(router, id="c2", name="named"))
    assert client.post(f"/v1/connectors/{allowed.id}/test", headers=AUTH).json()["ok"] is True


# --------------------------------------------------------------------------- #
# the adapter
# --------------------------------------------------------------------------- #


def test_an_adapter_built_without_a_list_dials_nothing_private() -> None:
    """The fail-safe default: a caller who forgot the table gets no loopback."""
    adapter = OpenAICompatConnector(connector(UNLISTED))
    assert adapter.allowed == []
    outcome = adapter.test()
    assert outcome.ok is False
    assert "not in [connectors.hosts]" in (outcome.error or "")
    with pytest.raises(ConnectorError, match="connectors.hosts"):
        adapter.client(UNLISTED)

    # The table is not a list of everything callable: a public address is
    # nothing this box has to be told about, list or no list.
    with pinned_client("http://93.184.216.34:8080", []) as public:
        assert public.is_closed is False


def test_the_metadata_address_is_refused_even_when_it_is_listed() -> None:
    with pytest.raises(AddressRefused, match="metadata"):
        pinned_client("http://169.254.169.254/latest/meta-data", ["169.254.169.254:80"])


def test_a_listed_name_that_answers_with_the_metadata_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list gets a name past the list check; the address is judged after.

    A name the operator wrote down is not a promise about what it resolves to,
    so `gateway.internal:80` being on the list does not make 169.254.169.254
    a place this box may call.
    """
    resolving(monkeypatch, **{"gateway.internal": "169.254.169.254"})
    with pytest.raises(AddressRefused, match="metadata"):
        pinned_client("http://gateway.internal:80", ["gateway.internal:80"])


def test_one_private_answer_among_public_ones_is_enough_to_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every address the name answered with is judged, not the first alone.

    The first answer here is a public address, which on its own this box would
    walk out to; the second is loopback, and one of those is enough when the
    name and port were not named in the config.
    """
    real = socket.getaddrinfo

    def two_answers(host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        return [
            *real("93.184.216.34", port, *args, **kwargs),
            *real("127.0.0.1", port, *args, **kwargs),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", two_answers)
    with pytest.raises(AddressRefused, match="127.0.0.1"):
        pinned_client("http://gateway.internal:80", [])

    # ...and the same name, named in the config, is the case the rule is for:
    # a name the operator wrote down may answer with an address on this box.
    with pinned_client("http://gateway.internal:80", ["gateway.internal:80"]) as allowed:
        assert allowed.is_closed is False


def test_a_base_url_that_is_not_http_is_refused_at_both_doors(
    client: TestClient, router: str
) -> None:
    """The scheme rule is older than the host list and still comes first."""
    body = {"name": "ftp", "kind": "openai_compat", "base_url": "ftp://gateway.internal:21"}
    refused = client.post("/v1/connectors", json=body, headers=AUTH)
    assert refused.status_code == 400, refused.text
    assert "http(s)" in refused.json()["error"]["message"]

    document = client.get("/v1/config", headers=AUTH).json()
    document["connectors"] = [
        {
            "name": "ftp",
            "kind": "openai_compat",
            "base_url": "ftp://gateway.internal:21",
            "secret": None,
            "admin_secret": None,
            "read": True,
            "write": False,
            "poll_minutes": 60,
        }
    ]
    assert client.put("/v1/config", json=document, headers=AUTH).status_code == 400

    # And `pinned_client` says the same thing for a URL that got past a door.
    with pytest.raises(HostNotAllowed, match="http"):
        pinned_client("ftp://gateway.internal:21", ["gateway.internal:21"])


def test_the_name_is_resolved_once_and_the_checked_address_is_dialled(
    router: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebinding case: the name is asked once, and the answer is kept."""
    port = int(router.rsplit(":", 1)[1])
    name = "gateway.internal"
    real = socket.getaddrinfo
    asked: list[tuple[str, int]] = []

    def answering(host: str, wanted: int, *args: Any, **kwargs: Any) -> Any:
        asked.append((host, wanted))
        if host != name:
            # Whoever asks again is asking about the address this call was
            # pinned to, and a literal is answered by the resolver itself.
            return real(host, wanted, *args, **kwargs)
        if [h for h, _ in asked].count(name) > 1:
            # What a name under somebody else's control would answer the second
            # time: a private address this box must not dial.
            return real("10.13.37.1", wanted, *args, **kwargs)
        return real("127.0.0.1", wanted, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", answering)

    with pinned_client(f"http://{name}:{port}", [f"{name}:{port}"]) as client:
        response = client.get(f"http://{name}:{port}/v1/models")

    assert response.status_code == 200
    # The address judged is the address dialled, and the name in the Host header
    # is the name in the URL -- the stub has no idea it was reached by address.
    assert Catalogue.seen == [(f"{name}:{port}", "/v1/models")]
    # The name was a question this call asked once, and the answer it kept was
    # the loopback stub rather than the private address a second answer held.
    assert asked[0] == (name, port)
    assert [host for host, _ in asked].count(name) == 1, asked


def test_the_name_is_kept_for_the_host_header_and_for_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning an address must not turn TLS into something that accepts anything.

    `sni_hostname` is the extension httpcore hands to `ssl` as
    `server_hostname`; the request itself is answered here so the assertion is
    about what the transport handed on, not about a socket.
    """
    resolving(monkeypatch, **{"gateway.internal": "127.0.0.1"})
    seen: dict[str, Any] = {}

    def answered(self: Any, request: httpx.Request) -> httpx.Response:
        seen["host"] = request.headers["host"]
        seen["dial"] = request.url.host
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, json={"object": "list", "data": []}, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", answered)

    with pinned_client("https://gateway.internal:8443/v1/models", ["gateway.internal:8443"]) as c:
        assert c.get("https://gateway.internal:8443/v1/models").status_code == 200

    assert seen["dial"] == "127.0.0.1"  # the address the name answered with
    assert seen["host"] == "gateway.internal:8443"
    assert seen["sni"] == "gateway.internal"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="no openssl to make a certificate")
def test_a_real_handshake_verifies_the_name_not_the_address(tmp_path: Path) -> None:
    """The one thing a stubbed transport cannot show.

    The certificate is for `localhost` and the socket is opened to `127.0.0.1`.
    A client that verified the address would fail; this one passes, because the
    name that was checked is the name the certificate is checked against.
    """
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    made = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        capture_output=True,
        text=True,
    )
    assert made.returncode == 0, made.stderr

    Catalogue.seen = []
    server = _serve(Catalogue, tls=(str(cert), str(key)))
    port = server.server_port
    context = ssl.create_default_context(cafile=str(cert))
    try:
        pinned = httpx.Client(
            transport=PinnedTransport(address="127.0.0.1", hostname="localhost", port=port, verify=context),
            timeout=5.0,
        )
        with pinned:
            assert pinned.get(f"https://localhost:{port}/v1/models").status_code == 200
        assert Catalogue.seen == [(f"localhost:{port}", "/v1/models")]

        # The control: hand it the address as the name and the same certificate
        # no longer fits, which is what makes the pass above mean something.
        wrong = httpx.Client(
            transport=PinnedTransport(
                address="127.0.0.1", hostname="127.0.0.1", port=port, verify=context
            ),
            timeout=5.0,
        )
        with wrong, pytest.raises(httpx.ConnectError):
            wrong.get(f"https://127.0.0.1:{port}/v1/models")
    finally:
        server.shutdown()
        server.server_close()
