"""A hand-written list of models, from `sieve.toml` or a YAML file.

The escape hatch: a provider with no `/v1/models`, or a deliberate pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from sieve.contracts import HttpClient, InventoryConfig, Reachable
from sieve.sources.base import utcnow


class StaticListInventory:
    name = "list"

    def list(self, cfg: InventoryConfig, http: HttpClient) -> list[Reachable]:
        """`http` is unused: this inventory never leaves the machine."""
        ids = list(cfg.models)

        path = cfg.options.get("path")
        if path:
            file = Path(str(path))
            if not file.is_file():
                raise FileNotFoundError(f"inventory {cfg.name!r}: no such file {file}")
            body: Any = yaml.safe_load(file.read_text(encoding="utf-8")) or []
            if isinstance(body, dict):
                body = body.get("models", [])
            if not isinstance(body, list):
                raise ValueError(f"inventory {cfg.name!r}: {file} must hold a list of model ids")
            ids += [str(item) for item in body]

        seen_at = utcnow()
        seen: set[str] = set()
        out: list[Reachable] = []
        for local_id in ids:
            clean = str(local_id).strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            out.append(Reachable(inventory=cfg.name, local_id=clean, seen_at=seen_at))
        return out
