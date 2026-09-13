"""Which kinds exist.

Adding a kind is one file next to this one and one line in `KINDS`. It is a
plain dict rather than an entry-point group because a connector is chosen by a
row in the database at request time, and a lookup that can fail with
`ImportError` halfway through an hourly run is a worse trade than a list a
reader can see all of.
"""

from __future__ import annotations

from sieve.connectors.base import Adapter, ConnectorError
from sieve.connectors.ninerouter import NineRouterConnector
from sieve.connectors.openai_compat import OpenAICompatConnector
from sieve.contracts import Connector

KINDS: dict[str, type[Adapter]] = {
    OpenAICompatConnector.kind: OpenAICompatConnector,
    NineRouterConnector.kind: NineRouterConnector,
}


def kinds() -> list[str]:
    return sorted(KINDS)


def writing_kinds() -> list[str]:
    return sorted(name for name, cls in KINDS.items() if cls.writes)


def adapter_for(connector: Connector) -> Adapter:
    """The adapter for one connector, or a refusal naming the kinds there are."""
    cls = KINDS.get(connector.kind)
    if cls is None:
        raise ConnectorError(f"no connector kind {connector.kind!r}; have {', '.join(kinds())}")
    return cls(connector)
