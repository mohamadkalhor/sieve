"""Targets: where chains go.

The protocol lives in `sieve/contracts.py`; this module re-exports it so a
third-party target can import one name, and holds the small helpers every
target shares.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sieve.contracts import Chain, Target, TargetConfig, TargetResult

__all__ = ["Chain", "Target", "TargetConfig", "TargetResult", "chain_rows", "write_atomic"]


def chain_rows(chains: list[Chain]) -> list[tuple[str, int, str, str]]:
    """One row per position: (profile, position, model_id, local ids)."""
    rows: list[tuple[str, int, str, str]] = []
    for chain in sorted(chains, key=lambda c: c.profile):
        for position, model_id in enumerate([chain.primary, *chain.fallbacks]):
            rows.append(
                (chain.profile, position, model_id, " ".join(chain.local.get(model_id, [])))
            )
    return rows


def write_atomic(path: Path, text: str) -> None:
    """Write through a temporary file and rename, so a reader never sees half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
