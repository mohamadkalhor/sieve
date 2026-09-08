"""The `litellm` target: a `router_settings.fallbacks` block in a YAML file.

LiteLLM reads its own config file and expects fallbacks as a list of one-key
maps -- `[{"primary": ["first", "second"]}, ...]`. Sieve owns that one block and
nothing else in the file: `model_list`, `general_settings` and every other key
belong to whoever wrote them, and a target that rewrites a config it does not
own has broken more than it fixed.

The file is read, the block replaced, and the whole thing written back through a
temporary file and renamed, so LiteLLM never reads a half-written config.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from sieve.contracts import Chain, TargetConfig, TargetResult
from sieve.targets.base import write_atomic

#: The one key this target owns.
BLOCK = "router_settings"
KEY = "fallbacks"


class LiteLLMError(RuntimeError):
    """Misconfiguration, or a file that is not a LiteLLM config."""


def fallbacks_for(chains: list[Chain]) -> list[dict[str, list[str]]]:
    """LiteLLM's shape: one map per primary, its value the ordered alternatives.

    Local ids, not canonical ones: LiteLLM routes to the names in its own
    `model_list`. A chain whose primary has no local id is skipped -- naming a
    model the router has never heard of would make every request through that
    primary fail at run time rather than here.
    """
    out: list[dict[str, list[str]]] = []
    for chain in chains:
        primaries = chain.local.get(chain.primary, [])
        if not primaries:
            continue
        alternatives: list[str] = []
        for model_id in chain.fallbacks:
            for local in chain.local.get(model_id, []):
                if local not in alternatives and local != primaries[0]:
                    alternatives.append(local)
        if alternatives:
            out.append({primaries[0]: alternatives})
    return out


class LiteLLMTarget:
    name = "litellm"

    def plan(self, cfg: TargetConfig, chains: list[Chain]) -> dict[str, list[str]]:
        """Keyed by primary local id, exactly as `current()` reads the file back.

        LiteLLM's format has no idea what a profile is, so the diff is by
        primary. Planning in canonical ids while reading back local ones would
        report a change on every run.
        """
        out: dict[str, list[str]] = {}
        for entry in fallbacks_for(chains):
            out.update(entry)
        return out

    def _path(self, cfg: TargetConfig) -> Path:
        raw = cfg.options.get("path") or cfg.dir
        if not raw:
            raise LiteLLMError(
                f"target {cfg.name!r}: set `path` to the LiteLLM config YAML this should write"
            )
        return Path(str(raw))

    def _load(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise LiteLLMError(f"{path} is not a LiteLLM config: the top level is not a mapping")
        return body

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]:
        """What the config file says now, keyed by primary rather than by profile.

        The file does not record which profile a fallback list came from -- it
        is LiteLLM's format, not ours -- so the diff is by primary. That is
        still a real comparison against the file that is actually in use.
        """
        path = self._path(cfg)
        block = self._load(path).get(BLOCK) or {}
        rows = block.get(KEY) if isinstance(block, dict) else None
        out: dict[str, list[str]] = {}
        for entry in rows or []:
            if isinstance(entry, dict):
                for primary, alternatives in entry.items():
                    if isinstance(alternatives, list):
                        out[str(primary)] = [str(a) for a in alternatives]
        return out

    def write(self, cfg: TargetConfig, chains: list[Chain], dry_run: bool) -> TargetResult:
        path = self._path(cfg)
        config = self._load(path)
        rows = fallbacks_for(chains)

        skipped = [c.profile for c in chains if not c.local.get(c.primary)]
        detail: dict[str, Any] = {
            "path": str(path),
            "fallbacks": len(rows),
            "skipped": skipped,
        }

        if dry_run:
            detail["written"] = False
            return TargetResult(
                target=cfg.name,
                written=[f"{path} ({len(rows)} fallback group(s))"],
                dry_run=True,
                detail=detail,
            )

        if path.is_file():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            backup = path.with_suffix(path.suffix + f".sieve-{stamp}.bak")
            shutil.copy2(path, backup)
            detail["backup"] = str(backup)

        block = config.get(BLOCK)
        if not isinstance(block, dict):
            block = {}
        block[KEY] = rows
        config[BLOCK] = block

        write_atomic(path, yaml.safe_dump(config, sort_keys=False, default_flow_style=False))
        detail["written"] = True
        return TargetResult(
            target=cfg.name,
            written=[str(path)],
            dry_run=False,
            detail=detail,
        )
