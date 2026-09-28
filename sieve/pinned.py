"""Where an outbound connector call may go, and to which address it goes.

A connector's `base_url` is a host somebody typed. A name that resolves to
`127.0.0.1`, to a link-local address or to the cloud metadata service turns this
box into a proxy for its own network, and a name that resolves one way when it is
checked and another way when it is dialled (DNS rebinding) does the same thing a
moment later. Two rules close both:

- `[connectors.hosts]` in `sieve.toml` lists the `host:port` a connector kind
  may be pointed at. A host that is not on the list for its kind is refused on
  the way in -- 422 `host_not_allowed` from the connector routes, and from the
  bundle import -- so it is never stored.
- every call resolves the name **once**, refuses an address this box must not
  dial, and then connects to that exact address. The hostname is kept for the
  `Host` header, for SNI and for certificate verification: pinning an address
  must not turn TLS into something that accepts any certificate.

The one exception is deliberate: a `host:port` on the list may be loopback or
private -- the gateway this box runs itself is on `127.0.0.1:20128`, and a rule
that could not name it would be a rule nobody could keep. The cloud metadata
address is refused whatever the list says; nothing Sieve talks to is there.

Nothing here holds a value. It decides where a request may go, not what it says.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

#: The cloud metadata service. Never dialled, listed or not.
METADATA = ipaddress.ip_address("169.254.169.254")

#: What a connector may speak. Anything else is not a connector's business.
SCHEMES = ("http", "https")

DEFAULT_PORTS = {"http": 80, "https": 443}


class HostNotAllowed(ValueError):
    """A base_url names a host the config does not allow for its kind."""


class AddressRefused(ValueError):
    """A name resolved to an address this box must not dial."""


def split(url: str) -> tuple[str, int] | None:
    """`(host, port)` of an http(s) URL, with the scheme's default port.

    None when the URL is not one: the caller decides what to say about it, and
    the routes already answer a scheme that is not http(s) with their own
    sentence.
    """
    try:
        parsed = urlsplit(str(url).strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in SCHEMES or not parsed.hostname:
        return None
    return parsed.hostname, port or DEFAULT_PORTS[parsed.scheme]


def host_port(url: str) -> str:
    """A URL as the `host:port` a `[connectors.hosts]` entry is written as."""
    parts = split(url)
    if parts is None:
        raise HostNotAllowed(f"{url!r} is not an http(s) URL")
    return f"{parts[0].lower()}:{parts[1]}"


def normalize_entry(entry: str) -> str:
    """One `[connectors.hosts]` entry, as `host:port`.

    Written as `host:port` and nothing else -- no scheme, no path -- so a list
    entry and a `base_url` are compared as the same kind of thing, and an entry
    that could never match is refused where it is written rather than silently
    allowing nothing.
    """
    text = str(entry).strip()
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"{text!r} is not a host:port, e.g. \"127.0.0.1:20128\"")
    return f"{host.lower()}:{int(port)}"


class AllowedHosts:
    """`[connectors.hosts]` as the routes and the connectors ask for it."""

    def __init__(self, table: Mapping[str, Sequence[str]] | None = None) -> None:
        self.table: dict[str, list[str]] = {
            str(kind): [normalize_entry(entry) for entry in entries]
            for kind, entries in (table or {}).items()
        }

    def __len__(self) -> int:
        return len(self.table)

    def __bool__(self) -> bool:
        return bool(self.table)

    def kinds(self) -> list[str]:
        return sorted(self.table)

    def allowed(self, kind: str) -> list[str]:
        """The `host:port` entries this kind may be pointed at, in written order."""
        return list(self.table.get(kind, []))

    def problem(self, kind: str, base_url: str) -> str | None:
        """Why this kind may not be pointed at this URL, or None.

        The sentence is shown to whoever wrote the connector, so it names the
        kind and the list it is judged against -- the file to edit, in the
        refusal rather than in a document somewhere else.
        """
        parts = split(base_url)
        if parts is None:
            return None  # not an http(s) URL: the routes say that themselves
        listed = self.table.get(kind, [])
        if f"{parts[0].lower()}:{parts[1]}" in listed:
            return None
        named = ", ".join(listed) or "nothing"
        return (
            f"{base_url!r} is not one of the hosts kind {kind!r} may be pointed at: "
            f"[connectors.hosts] {kind} = [{named}] in sieve.toml"
        )

    def check(self, kind: str, base_url: str) -> None:
        """Refuse a base_url this kind may not use, or return."""
        reason = self.problem(kind, base_url)
        if reason:
            raise HostNotAllowed(reason)


def resolved_addresses(host: str, port: int) -> list[str]:
    """Every address this name resolves to, right now, once per call."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    found: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%")[0]
        if address not in found:
            found.append(address)
    if not found:
        raise AddressRefused(f"{host!r} resolved to no address at all")
    return found


def refusal(host: str, port: int, addresses: Iterable[str], allowed: Sequence[str]) -> str | None:
    """The address this call must not dial, or None when every one is fine.

    An address that is loopback, private, link-local, multicast, reserved or
    unspecified is refused unless this exact `host:port` is on the list -- which
    is what lets the gateway on `127.0.0.1:20128` be reached and nothing else on
    the box be. The metadata address is refused either way.
    """
    listed = f"{host.lower()}:{port}" in list(allowed)
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return f"{host}:{port} resolved to {address!r}, which is not an address"
        if parsed == METADATA:
            return (
                f"{host}:{port} resolves to {address}, the cloud metadata service; "
                "it is never dialled, listed or not"
            )
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
            or parsed.is_unspecified
        ) and not listed:
            return (
                f"{host}:{port} resolves to {address}, a private address, and "
                f"{host}:{port} is not in [connectors.hosts] in sieve.toml"
            )
    return None


class PinnedTransport(httpx.HTTPTransport):
    """Dial one checked address; keep the name for the Host header and the TLS.

    The `sni_hostname` extension is what httpcore hands to `ssl` as
    `server_hostname`, so SNI and certificate verification stay on the hostname
    while the socket goes to the address that was checked.
    """

    def __init__(self, *, address: str, hostname: str, port: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        #: the address that was resolved and checked, and the only one dialled
        self.pinned_address = address
        #: the name the certificate must be valid for, and the Host header
        self.server_hostname = hostname
        self.pinned_port = port

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.headers["host"] = request.url.netloc.decode("ascii")
        request.extensions = {**request.extensions, "sni_hostname": self.server_hostname}
        request.url = request.url.copy_with(host=self.pinned_address)
        return super().handle_request(request)


def pinned_client(
    base_url: str, allowed: Sequence[str] | None = None, timeout: float = 10.0
) -> httpx.Client:
    """An `httpx.Client` for `base_url` that connects to the address it checked.

    The name is resolved here, every address it answers with is judged against
    the list, and the first one is dialled by address. A second resolution never
    happens, so a name that changes its answer between the check and the call
    reaches nothing.

    Raises `HostNotAllowed` for a URL that is not http(s), and `AddressRefused`
    for an address this box must not dial.
    """
    parts = split(base_url)
    if parts is None:
        raise HostNotAllowed(f"{base_url!r} is not an http(s) URL")
    host, port = parts
    listed = [normalize_entry(entry) for entry in (allowed or [])]
    addresses = resolved_addresses(host, port)
    reason = refusal(host, port, addresses, listed)
    if reason:
        raise AddressRefused(reason)
    return httpx.Client(
        transport=PinnedTransport(address=addresses[0], hostname=host, port=port),
        timeout=timeout,
    )
