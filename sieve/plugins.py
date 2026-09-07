"""Plugin lookup over the entry-point groups of CONTRACTS section 2.

Loading is lazy and tolerant: a group member whose module has not landed yet
is reported as missing rather than raising at import time, so every owner can
run the parts that do exist from the first hour.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

SOURCES = "sieve.sources"
INVENTORIES = "sieve.inventories"
TARGETS = "sieve.targets"


@dataclass(frozen=True)
class Missing:
    """A registered plugin whose module is not importable yet."""

    name: str
    group: str
    reason: str


def names(group: str) -> list[str]:
    return sorted(ep.name for ep in entry_points(group=group))


def load(group: str, name: str) -> Any:
    """Instantiate one plugin. Raises LookupError if it is not registered,
    and ImportError (with the entry point named) if its module is absent."""
    for ep in entry_points(group=group):
        if ep.name != name:
            continue
        try:
            obj = ep.load()
        except Exception as exc:
            raise ImportError(f"{group}:{name} is registered but not importable: {exc}") from exc
        return obj() if isinstance(obj, type) else obj
    raise LookupError(f"no {group} plugin named {name!r}; have {', '.join(names(group))}")


def load_all(group: str) -> tuple[dict[str, Any], list[Missing]]:
    """Every plugin in a group that imports, plus the ones that do not."""
    found: dict[str, Any] = {}
    missing: list[Missing] = []
    for name in names(group):
        try:
            found[name] = load(group, name)
        except ImportError as exc:
            missing.append(Missing(name=name, group=group, reason=str(exc)))
    return found, missing
