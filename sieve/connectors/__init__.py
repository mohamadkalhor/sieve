"""Connectors: a router is something you add at runtime, not something you edit.

Before this, Sieve reached exactly one router, described twice in `sieve.toml`
-- `[inventories.gateway]` for the ids it can reach and `[targets.gateway]` for
the combos it is given. Anyone with a second router, or without a shell on the
box, could not add one at all.

A connector is those same facts as a row: where the router is, the **name** of
the environment variable holding its token, and two switches -- `read` (its ids
join the inventory) and `write` (it is given the chains). You add one by URL
over the API, test it, and switch it on.

The TOML blocks still work. `seed.py` turns them into connectors on the first
start after this lands, and anything left in the file that no connector shadows
keeps running exactly as it did.
"""

from sieve.connectors.base import Adapter, ConnectorError
from sieve.connectors.loop import PullCount, build_registry, log_applied, refresh, ship
from sieve.connectors.ninerouter import NineRouterConnector
from sieve.connectors.openai_compat import OpenAICompatConnector
from sieve.connectors.registry import KINDS, adapter_for, kinds, writing_kinds
from sieve.connectors.seed import from_toml, seed_from_toml

__all__ = [
    "KINDS",
    "Adapter",
    "ConnectorError",
    "NineRouterConnector",
    "OpenAICompatConnector",
    "PullCount",
    "adapter_for",
    "build_registry",
    "from_toml",
    "kinds",
    "log_applied",
    "refresh",
    "seed_from_toml",
    "ship",
    "writing_kinds",
]
