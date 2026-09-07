"""The `manual` source: CSV or JSON dropped in `data/observations/`.

For a private benchmark, or a public one with no connector yet. Columns are the
fields of `Observation`:

    model_id, modality, source, field, value, unit, n, ci95, observed_at

`source` names where the number came from -- `my_eval`, `vellum`, whatever -- so
an axis can weight it like any other source. A row that does not parse is a
warning naming the file and line; the rest of the file still loads.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from sieve.contracts import HttpClient, Modality, ModelRef, Observation, PullResult, SourceConfig
from sieve.sources.base import as_float, as_int, utcnow

REQUIRED = ("model_id", "modality", "field", "value")


def _rows_from(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8", newline="") as handle:
            for number, row in enumerate(csv.DictReader(handle), start=2):
                yield number, dict(row)
        return
    body = json.loads(path.read_text(encoding="utf-8"))
    rows = body.get("observations", []) if isinstance(body, dict) else body
    if isinstance(rows, list):
        for number, row in enumerate(rows, start=1):
            if isinstance(row, dict):
                yield number, row


def _stamp(raw: Any, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=fallback.tzinfo)


class ManualSource:
    name = "manual"
    modality: ClassVar[list[Modality]] = []
    needs_key = False

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        """Reads the drop directory. `http` is unused: nothing here is remote."""
        at = utcnow()
        result = PullResult(source=self.name)
        directory = Path(cfg.dir or "data/observations")
        if not directory.is_dir():
            result.warnings.append(f"manual: {directory} does not exist; nothing to read")
            return result

        files = sorted(
            p for p in directory.iterdir() if p.suffix.lower() in (".csv", ".json") and p.is_file()
        )
        if not files:
            result.warnings.append(f"manual: no .csv or .json files in {directory}")

        seen: set[tuple[str, str]] = set()
        for file in files:
            try:
                rows = list(_rows_from(file))
            except (OSError, ValueError, csv.Error) as exc:
                result.warnings.append(f"manual: {file.name} could not be read: {exc}")
                continue

            for line, row in rows:
                observation = self._observation(row, at)
                if observation is None:
                    missing = [k for k in REQUIRED if not row.get(k)]
                    result.warnings.append(
                        f"manual: {file.name} line {line} skipped"
                        + (f" (missing {', '.join(missing)})" if missing else " (unreadable)")
                    )
                    continue
                result.observations.append(observation)
                key = (observation.model_id, observation.modality)
                if key not in seen:
                    seen.add(key)
                    result.models.append(
                        ModelRef(
                            id=observation.model_id,
                            modality=observation.modality,
                            name=observation.model_id.rsplit("/", 1)[-1],
                            creator=observation.model_id.split("/", 1)[0],
                        )
                    )

        return result

    def _observation(self, row: dict[str, Any], at: datetime) -> Observation | None:
        if any(not row.get(key) for key in REQUIRED):
            return None
        value = as_float(row.get("value"))
        if value is None:
            return None
        try:
            return Observation(
                model_id=str(row["model_id"]).strip(),
                modality=str(row["modality"]).strip(),  # type: ignore[arg-type]
                source=str(row.get("source") or self.name).strip(),
                field=str(row["field"]).strip(),
                value=value,
                unit=str(row.get("unit") or "fraction").strip(),  # type: ignore[arg-type]
                n=as_int(row.get("n")),
                ci95=as_float(row.get("ci95")),
                observed_at=_stamp(row.get("observed_at"), at),
                pulled_at=at,
            )
        except Exception:
            return None
