"""The `file` target: `out/<profile>.json` and `out/chains.csv`.

The one target phase 1 may write to. Both files are written through a temporary
file and renamed, so a gateway reading them never sees a half-written chain.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from sieve.contracts import Chain, TargetConfig, TargetResult
from sieve.targets.base import chain_rows, planned_ids, write_atomic

CSV_HEADER = ("profile", "position", "model_id", "local_ids")


class FileTarget:
    name = "file"

    def plan(self, cfg: TargetConfig, chains: list[Chain]) -> dict[str, list[str]]:
        """Canonical ids: this target writes the chain as the engine computed it."""
        return planned_ids(chains)

    def _dir(self, cfg: TargetConfig) -> Path:
        return Path(cfg.dir or "out")

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]:
        """What is on disk now, so `sieve diff` can show the change."""
        out: dict[str, list[str]] = {}
        directory = self._dir(cfg)
        if not directory.is_dir():
            return out
        for file in sorted(directory.glob("*.json")):
            try:
                body = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(body, dict) and "primary" in body:
                out[body.get("profile", file.stem)] = [
                    body["primary"],
                    *body.get("fallbacks", []),
                ]
        return out

    def write(self, cfg: TargetConfig, chains: list[Chain], dry_run: bool) -> TargetResult:
        directory = self._dir(cfg)
        written: list[str] = []

        for chain in chains:
            path = directory / f"{chain.profile}.json"
            written.append(str(path))
            if not dry_run:
                write_atomic(path, chain.model_dump_json(indent=2) + "\n")

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        writer.writerows(chain_rows(chains))
        csv_path = directory / "chains.csv"
        written.append(str(csv_path))
        if not dry_run:
            write_atomic(csv_path, buffer.getvalue())

        return TargetResult(
            target=cfg.name,
            written=written,
            dry_run=dry_run,
            detail={"dir": str(directory), "chains": len(chains)},
        )
