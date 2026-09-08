"""The `ninerouter` target: a 9router combo per profile.

9router serves an OpenAI-compatible catalogue in which each **combo** appears as
a model with `owned_by: "combo"`, and keeps its state in a SQLite file whose
`combos` table is `(id, name, kind, models, createdAt, updatedAt)` -- `models`
being a JSON array of local model ids **in fallback order**. That array is
exactly what a Sieve chain is, which is why this target can read the live chain
back and give `sieve diff` something real to compare against.

**Two ways in, and a refusal.** The admin API answers `{"error":"Unauthorized"}`
without a token, so: the HTTP API when a token is configured, the SQLite file
when a path is given, and a clear refusal naming both when neither is. Guessing
would mean writing to a database nobody asked this to touch.

**Never against a live gateway from a build.** The file path is backed up before
it is written, and `write()` refuses a path it was not given explicitly.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sieve.contracts import Chain, TargetConfig, TargetResult

#: What 9router calls a combo in its own catalogue.
COMBO_OWNER = "combo"

#: The prefix a Sieve-managed combo carries, so a hand-made combo is never
#: touched: this target only edits what it created.
MANAGED_PREFIX = "sieve-"


class NineRouterError(RuntimeError):
    """Misconfiguration or a refusal. Never a silent no-op."""


def combo_name(profile: str) -> str:
    """The combo a profile owns. Stable, prefixed, and its own namespace."""
    return f"{MANAGED_PREFIX}{profile}"


def chain_models(chain: Chain) -> list[str]:
    """The local ids for one chain, in fallback order.

    9router routes to *its own* model ids, not to canonical ones, so a chain
    whose models it does not serve becomes a shorter combo rather than a broken
    one. A chain with no reachable local id at all is refused by the caller.
    """
    out: list[str] = []
    for model_id in (chain.primary, *chain.fallbacks):
        for local in chain.local.get(model_id, []):
            if local not in out:
                out.append(local)
    return out


class NineRouterTarget:
    name = "ninerouter"

    def plan(self, cfg: TargetConfig, chains: list[Chain]) -> dict[str, list[str]]:
        """Local ids, because that is what a combo holds and what `current()` reads.

        Without this the diff compares canonical ids against 9router's own and
        reports a change on every run, which teaches everyone to ignore it.
        """
        return {chain.profile: models for chain in chains if (models := chain_models(chain))}

    # -- how to reach it ------------------------------------------------- #

    def _mode(self, cfg: TargetConfig) -> str:
        token_env = str(cfg.options.get("token_env") or "")
        import os

        if token_env and os.environ.get(token_env):
            return "http"
        if cfg.options.get("sqlite_path"):
            return "sqlite"
        raise NineRouterError(
            f"target {cfg.name!r}: no way in. Set `token_env` to the environment "
            "variable holding a 9router admin token (the admin API answers "
            '{"error":"Unauthorized"} without one), or `sqlite_path` to its '
            "database file. Both are missing, and this will not guess."
        )

    # -- reading the live chain ------------------------------------------ #

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]:
        """`{profile: [local ids]}` as 9router holds it *now*.

        This is the whole reason the target exists in this shape. A diff against
        the last `apply` decision only says what Sieve believes it wrote; a diff
        against this says what is actually routing traffic, which is the thing
        that drifts.
        """
        mode = self._mode(cfg)
        rows = self._read_http(cfg) if mode == "http" else self._read_sqlite(cfg)
        out: dict[str, list[str]] = {}
        for name, models in rows.items():
            if name.startswith(MANAGED_PREFIX):
                out[name[len(MANAGED_PREFIX) :]] = models
        return out

    def _read_http(self, cfg: TargetConfig) -> dict[str, list[str]]:
        import os

        import httpx

        base = str(cfg.url or "").rstrip("/")
        if not base:
            raise NineRouterError(f"target {cfg.name!r}: `url` is needed to use the admin API")
        token = os.environ[str(cfg.options["token_env"])]
        response = httpx.get(
            f"{base}/v1/models",
            headers={"authorization": f"Bearer {token}"},
            timeout=float(cfg.options.get("timeout", 10.0)),
        )
        if response.status_code == 401:
            raise NineRouterError(
                f"target {cfg.name!r}: 9router answered Unauthorized -- "
                f"{cfg.options['token_env']} is set but not accepted"
            )
        if response.status_code != 200:
            raise NineRouterError(f"target {cfg.name!r}: HTTP {response.status_code} from {base}")

        body = response.json()
        out: dict[str, list[str]] = {}
        for entry in body.get("data", []):
            if not isinstance(entry, dict) or entry.get("owned_by") != COMBO_OWNER:
                continue
            name = str(entry.get("id") or "")
            models = entry.get("models")
            if name and isinstance(models, list):
                out[name] = [str(m) for m in models]
        return out

    def _sqlite_path(self, cfg: TargetConfig) -> Path:
        path = Path(str(cfg.options["sqlite_path"]))
        if not path.is_file():
            raise NineRouterError(f"target {cfg.name!r}: no such 9router database: {path}")
        return path

    def _read_sqlite(self, cfg: TargetConfig) -> dict[str, list[str]]:
        path = self._sqlite_path(cfg)
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT name, models FROM combos").fetchall()
        except sqlite3.DatabaseError as exc:
            raise NineRouterError(
                f"target {cfg.name!r}: {path} is not a 9router database: {exc}"
            ) from exc
        finally:
            db.close()

        out: dict[str, list[str]] = {}
        for row in rows:
            try:
                models = json.loads(row["models"] or "[]")
            except ValueError:
                continue
            if isinstance(models, list):
                out[str(row["name"])] = [str(m) for m in models]
        return out

    # -- writing ---------------------------------------------------------- #

    def write(self, cfg: TargetConfig, chains: list[Chain], dry_run: bool) -> TargetResult:
        mode = self._mode(cfg)
        planned: dict[str, list[str]] = {}
        skipped: dict[str, str] = {}

        for chain in chains:
            models = chain_models(chain)
            if not models:
                skipped[chain.profile] = "no reachable local id in the chain"
                continue
            planned[combo_name(chain.profile)] = models

        detail: dict[str, Any] = {
            "mode": mode,
            "combos": sorted(planned),
            "skipped": skipped,
        }
        if dry_run:
            detail["written"] = False
            return TargetResult(
                target=cfg.name,
                written=[
                    f"{name}: {' -> '.join(models)}" for name, models in sorted(planned.items())
                ],
                dry_run=True,
                detail=detail,
            )

        if mode == "sqlite":
            backup = self._write_sqlite(cfg, planned)
            detail["backup"] = str(backup)
        else:
            self._write_http(cfg, planned)

        return TargetResult(
            target=cfg.name,
            written=[f"{name}: {' -> '.join(models)}" for name, models in sorted(planned.items())],
            dry_run=False,
            detail=detail,
        )

    def _write_sqlite(self, cfg: TargetConfig, planned: dict[str, list[str]]) -> Path:
        """Upsert each managed combo. Everything else in the table is left alone.

        The file is copied first. It belongs to another program, and a target
        that corrupts a gateway's database has done far more damage than one
        that failed to write.
        """
        path = self._sqlite_path(cfg)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = path.with_suffix(path.suffix + f".sieve-{stamp}.bak")
        shutil.copy2(path, backup)

        now = datetime.now(UTC).isoformat()
        db = sqlite3.connect(path)
        try:
            db.row_factory = sqlite3.Row
            held = {
                str(r["name"]): str(r["id"])
                for r in db.execute("SELECT id, name FROM combos").fetchall()
            }
            with db:
                for name, models in sorted(planned.items()):
                    blob = json.dumps(models)
                    if name in held:
                        db.execute(
                            "UPDATE combos SET models=?, updatedAt=? WHERE id=?",
                            (blob, now, held[name]),
                        )
                    else:
                        db.execute(
                            "INSERT INTO combos (id, name, kind, models, createdAt, updatedAt)"
                            " VALUES (?,?,?,?,?,?)",
                            (uuid.uuid4().hex, name, "fallback", blob, now, now),
                        )
        finally:
            db.close()
        return backup

    def _write_http(self, cfg: TargetConfig, planned: dict[str, list[str]]) -> None:
        import os

        import httpx

        base = str(cfg.url or "").rstrip("/")
        if not base:
            raise NineRouterError(f"target {cfg.name!r}: `url` is needed to use the admin API")
        token = os.environ[str(cfg.options["token_env"])]
        timeout = float(cfg.options.get("timeout", 10.0))

        for name, models in sorted(planned.items()):
            response = httpx.put(
                f"{base}/admin/combos/{name}",
                json={"name": name, "kind": "fallback", "models": models},
                headers={"authorization": f"Bearer {token}"},
                timeout=timeout,
            )
            if response.status_code == 401:
                raise NineRouterError(
                    f"target {cfg.name!r}: 9router answered Unauthorized writing {name}"
                )
            if not 200 <= response.status_code < 300:
                raise NineRouterError(
                    f"target {cfg.name!r}: HTTP {response.status_code} writing {name}"
                )
