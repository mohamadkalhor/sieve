"""The box migrates itself.

Sieve reached one router, named twice in `sieve.toml`: `[inventories.gateway]`
to read and `[targets.gateway]` to write. Those blocks still work -- this is
somebody's running machine and a rewrite that needs a config edit to keep
serving is a rewrite that broke it -- but on the first start after this lands,
they become a connector, and from then on the connector is what the loop uses.

Two rules keep the two worlds from doing the same work twice:

- seeding happens **once**, only while the table is empty, so a connector
  deleted on purpose does not grow back every hour;
- a TOML inventory or target whose name a connector already carries is
  **shadowed** -- skipped by the pull and the apply -- so one gateway is read
  once and written once, no matter that it is described in two places.

A `[inventories.*]` block of kind `list` is not a router and is never seeded. It
keeps working exactly as it did.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from sieve.config import Config
from sieve.connectors.registry import KINDS
from sieve.contracts import Connector
from sieve.store import Store

#: The TOML target kind that is really a router Sieve can both read and write.
NINEROUTER = "ninerouter"

#: The TOML inventory kind that is really a router Sieve can read.
OPENAI_COMPAT = "openai_compat"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def from_toml(cfg: Config) -> list[Connector]:
    """The connectors the config describes, without touching the store."""
    made: list[Connector] = []
    at = datetime.now(UTC)

    # A ninerouter target first: it is the only kind that both reads and
    # writes, and an inventory of the same name is the same box described
    # from the other side.
    for name, target_cfg in sorted(cfg.targets.items()):
        if target_cfg.kind != NINEROUTER:
            continue
        inventory = cfg.inventories.get(name)
        base_url = str(target_cfg.url or (inventory.base_url if inventory else "") or "").strip()
        if not base_url:
            continue
        options: dict[str, object] = {}
        admin = target_cfg.options.get("token_env")
        if admin:
            options["admin_token_env"] = str(admin)
        made.append(
            Connector(
                id=new_id(),
                name=name,
                kind=NINEROUTER,
                base_url=base_url,
                token_env=inventory.token_env if inventory else None,
                # Only what the TOML already did. A target Sieve wrote to and
                # never read from does not start being read because of a
                # migration nobody asked for.
                read=inventory is not None,
                write=True,
                options=options,
                created_at=at,
            )
        )

    seeded = {c.name for c in made}
    for name, inventory in sorted(cfg.inventories.items()):
        if inventory.kind != OPENAI_COMPAT or name in seeded or not inventory.base_url:
            continue
        made.append(
            Connector(
                id=new_id(),
                name=name,
                kind=OPENAI_COMPAT,
                base_url=str(inventory.base_url),
                token_env=inventory.token_env,
                read=True,
                write=False,
                created_at=at,
            )
        )
    return [c for c in made if c.kind in KINDS]


def seed_from_toml(cfg: Config, store: Store, owner_id: str | None = None) -> list[Connector]:
    """Seed once, on an empty table. Returns what it created, usually nothing.

    Cheap enough to call on every start and at the top of every loop: one
    `SELECT` when the table already has rows, which after the first start it
    always does.

    The TOML describes the box's own router, so what it seeds belongs to the
    gate owner -- `owner_id` here is theirs. A member never gets one: they add
    their own connector on the Connectors page, and the check is scoped so an
    empty seat does not re-seed the owner's gateway under their name.
    """
    if store.has_connectors():
        return []
    made: list[Connector] = []
    for connector in from_toml(cfg):
        try:
            store.add_connector(connector.model_copy(update={"owner_id": owner_id}))
        except sqlite3.IntegrityError:
            # Another process seeded between the check and the insert. Its row
            # is as good as this one; there is nothing to repair.
            continue
        made.append(connector)
    return made
