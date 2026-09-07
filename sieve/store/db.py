"""SQLite store (CONTRACTS section 4).

Observations, decisions and telemetry are append-only. Migrations are numbered
SQL files in `migrations/`, applied on start inside one transaction each.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sieve.contracts import (
    Capability,
    Chain,
    Decision,
    Modality,
    ModelRef,
    Observation,
    ObsTable,
    Price,
    Ranking,
    Reachable,
    TelemetryEvent,
)

MIGRATIONS = Path(__file__).parent / "migrations"

TELEMETRY_RETENTION_DAYS = 30


def _merge_capability(base: Capability, extra: Capability) -> Capability:
    """Fill the gaps in `base` from `extra`; what `base` states already wins."""
    update = {
        field: getattr(extra, field)
        for field in Capability.model_fields
        if getattr(base, field) in (None, []) and getattr(extra, field) not in (None, [])
    }
    return base.model_copy(update=update) if update else base


def now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _dt(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Store:
    """Everything that touches the database. Nothing else opens a connection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.migrate()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    def migrate(self) -> list[str]:
        """Apply every migration not yet recorded. Returns the ones applied."""
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        self.db.commit()
        done = {r["name"] for r in self.db.execute("SELECT name FROM schema_migrations")}
        applied: list[str] = []
        for sql_file in sorted(MIGRATIONS.glob("*.sql")):
            if sql_file.name in done:
                continue
            self.db.executescript(sql_file.read_text(encoding="utf-8"))
            self.db.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                (sql_file.name, _iso(now())),
            )
            self.db.commit()
            applied.append(sql_file.name)
        return applied

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.db
        except Exception:
            self.db.rollback()
            raise
        self.db.commit()

    # ------------------------------------------------------------------ #
    # catalog
    # ------------------------------------------------------------------ #

    def upsert_models(self, models: Iterable[ModelRef]) -> int:
        stamp = _iso(now())
        rows = 0
        with self.tx() as db:
            for m in models:
                db.execute(
                    "INSERT INTO models (id, modality, name, creator, release_date,"
                    " first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT (id, modality) DO UPDATE SET"
                    " name=excluded.name, creator=excluded.creator,"
                    " release_date=COALESCE(excluded.release_date, models.release_date),"
                    " last_seen_at=excluded.last_seen_at",
                    (
                        m.id,
                        m.modality,
                        m.name,
                        m.creator,
                        m.release_date.isoformat() if m.release_date else None,
                        stamp,
                        stamp,
                    ),
                )
                rows += 1
                for alias in m.aliases:
                    db.execute(
                        "INSERT OR IGNORE INTO aliases (alias, modality, model_id, origin)"
                        " VALUES (?,?,?,'source')",
                        (alias, m.modality, m.id),
                    )
        return rows

    def models(self, modality: Modality | None = None) -> list[ModelRef]:
        sql = "SELECT * FROM models"
        args: tuple[Any, ...] = ()
        if modality:
            sql += " WHERE modality = ?"
            args = (modality,)
        sql += " ORDER BY id"
        out: list[ModelRef] = []
        for r in self.db.execute(sql, args):
            aliases = [
                a["alias"]
                for a in self.db.execute(
                    "SELECT alias FROM aliases WHERE model_id=? AND modality=? ORDER BY alias",
                    (r["id"], r["modality"]),
                )
            ]
            out.append(
                ModelRef(
                    id=r["id"],
                    modality=r["modality"],
                    name=r["name"],
                    creator=r["creator"],
                    aliases=aliases,
                    release_date=(
                        datetime.fromisoformat(r["release_date"]).date()
                        if r["release_date"]
                        else None
                    ),
                )
            )
        return out

    def put_alias(self, alias: str, modality: Modality, model_id: str) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO aliases (alias, modality, model_id, origin) VALUES (?,?,?,'user')"
                " ON CONFLICT (alias, modality) DO UPDATE SET model_id=excluded.model_id,"
                " origin='user'",
                (alias, modality, model_id),
            )

    def aliases(self, modality: Modality | None = None) -> dict[str, str]:
        sql = "SELECT alias, model_id FROM aliases"
        args: tuple[Any, ...] = ()
        if modality:
            sql += " WHERE modality = ?"
            args = (modality,)
        return {r["alias"]: r["model_id"] for r in self.db.execute(sql, args)}

    # ------------------------------------------------------------------ #
    # observations and prices (append-only)
    # ------------------------------------------------------------------ #

    def add_observations(self, obs: Iterable[Observation], snapshot: str | None = None) -> int:
        """Insert observations. A repeat of the same measurement is ignored,
        never overwritten; a new `observed_at` is a new row."""
        added = 0
        with self.tx() as db:
            for o in obs:
                cur = db.execute(
                    "INSERT OR IGNORE INTO observations (model_id, modality, source, field,"
                    " value, unit, n, ci95, observed_at, pulled_at, snapshot)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        o.model_id,
                        o.modality,
                        o.source,
                        o.field,
                        o.value,
                        o.unit,
                        o.n,
                        o.ci95,
                        _iso(o.observed_at),
                        _iso(o.pulled_at),
                        snapshot,
                    ),
                )
                added += cur.rowcount if cur.rowcount > 0 else 0
        return added

    def count_observations(self, modality: Modality | None = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM observations"
        args: tuple[Any, ...] = ()
        if modality:
            sql += " WHERE modality = ?"
            args = (modality,)
        row = self.db.execute(sql, args).fetchone()
        return int(row["c"])

    def observations_for(self, model_id: str) -> list[Observation]:
        rows = self.db.execute(
            "SELECT * FROM observations WHERE model_id=? ORDER BY observed_at DESC",
            (model_id,),
        )
        return [self._observation(r) for r in rows]

    @staticmethod
    def _observation(r: sqlite3.Row) -> Observation:
        return Observation(
            model_id=r["model_id"],
            modality=r["modality"],
            source=r["source"],
            field=r["field"],
            value=r["value"],
            unit=r["unit"],
            n=r["n"],
            ci95=r["ci95"],
            observed_at=_dt(r["observed_at"]),
            pulled_at=_dt(r["pulled_at"]),
        )

    def add_prices(self, prices: Iterable[Price]) -> int:
        added = 0
        with self.tx() as db:
            for p in prices:
                cur = db.execute(
                    "INSERT OR IGNORE INTO prices (model_id, source, unit, input, output,"
                    " cached_input, per_unit, source_url, observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        p.model_id,
                        p.source,
                        p.unit,
                        p.input,
                        p.output,
                        p.cached_input,
                        p.per_unit,
                        p.source_url,
                        _iso(p.observed_at),
                    ),
                )
                added += cur.rowcount if cur.rowcount > 0 else 0
        return added

    def latest_prices(self, modality: Modality) -> dict[str, Price]:
        rows = self.db.execute(
            "SELECT p.* FROM prices p JOIN models m"
            " ON m.id = p.model_id AND m.modality = ?"
            " ORDER BY p.observed_at ASC",
            (modality,),
        )
        out: dict[str, Price] = {}
        for r in rows:
            out[r["model_id"]] = Price(
                model_id=r["model_id"],
                source=r["source"],
                unit=r["unit"],
                input=r["input"],
                output=r["output"],
                cached_input=r["cached_input"],
                per_unit=r["per_unit"],
                source_url=r["source_url"],
                observed_at=_dt(r["observed_at"]),
            )
        return out

    def obs_table(self, modality: Modality) -> ObsTable:
        """The latest observation per (model, source, field) for one modality."""
        table = ObsTable(modality=modality, prices=self.latest_prices(modality))
        rows = self.db.execute(
            "SELECT * FROM observations WHERE modality=? ORDER BY observed_at ASC",
            (modality,),
        )
        for r in rows:
            table.add(self._observation(r))
        return table

    # ------------------------------------------------------------------ #
    # capabilities (what a source says a model can do)
    # ------------------------------------------------------------------ #

    def set_capabilities(self, source: str, modality: Modality, caps: dict[str, Capability]) -> int:
        stamp = _iso(now())
        rows = 0
        with self.tx() as db:
            for model_id, capability in caps.items():
                db.execute(
                    "INSERT OR REPLACE INTO capabilities (model_id, modality, source,"
                    " capability, seen_at) VALUES (?,?,?,?,?)",
                    (model_id, modality, source, capability.model_dump_json(), stamp),
                )
                rows += 1
        return rows

    def capabilities(self, modality: Modality) -> dict[str, Capability]:
        """What every source says, merged; a later source fills only the gaps."""
        out: dict[str, Capability] = {}
        for r in self.db.execute(
            "SELECT model_id, capability FROM capabilities WHERE modality=? ORDER BY source",
            (modality,),
        ):
            found = Capability.model_validate_json(r["capability"])
            held = out.get(r["model_id"])
            out[r["model_id"]] = found if held is None else _merge_capability(held, found)
        return out

    # ------------------------------------------------------------------ #
    # inventory
    # ------------------------------------------------------------------ #

    def set_reachable(self, inventory: str, items: Iterable[Reachable]) -> int:
        rows = 0
        with self.tx() as db:
            db.execute("DELETE FROM reachable WHERE inventory=?", (inventory,))
            for it in items:
                db.execute(
                    "INSERT OR REPLACE INTO reachable (inventory, local_id, model_id,"
                    " capability, seen_at) VALUES (?,?,?,?,?)",
                    (
                        it.inventory,
                        it.local_id,
                        it.model_id,
                        it.capability.model_dump_json(),
                        _iso(it.seen_at),
                    ),
                )
                rows += 1
        return rows

    def reachable(self, *, unmatched: bool | None = None) -> list[Reachable]:
        sql = "SELECT * FROM reachable"
        if unmatched is True:
            sql += " WHERE model_id IS NULL"
        elif unmatched is False:
            sql += " WHERE model_id IS NOT NULL"
        sql += " ORDER BY inventory, local_id"
        return [
            Reachable(
                inventory=r["inventory"],
                local_id=r["local_id"],
                model_id=r["model_id"],
                capability=Capability.model_validate_json(r["capability"]),
                seen_at=_dt(r["seen_at"]),
            )
            for r in self.db.execute(sql)
        ]

    def local_ids(self) -> dict[str, list[str]]:
        """Canonical model id -> the local ids that serve it."""
        out: dict[str, list[str]] = {}
        for r in self.db.execute(
            "SELECT model_id, local_id FROM reachable WHERE model_id IS NOT NULL ORDER BY local_id"
        ):
            out.setdefault(r["model_id"], []).append(r["local_id"])
        return out

    # ------------------------------------------------------------------ #
    # snapshots, rankings, chains
    # ------------------------------------------------------------------ #

    def new_snapshot(self, source_rows: int = 0, detail: dict[str, Any] | None = None) -> str:
        sid = uuid.uuid4().hex[:12]
        with self.tx() as db:
            db.execute(
                "INSERT INTO snapshots (id, at, source_rows, detail) VALUES (?,?,?,?)",
                (sid, _iso(now()), source_rows, json.dumps(detail or {})),
            )
        return sid

    def latest_snapshot(self) -> str | None:
        row = self.db.execute("SELECT id FROM snapshots ORDER BY at DESC LIMIT 1").fetchone()
        return str(row["id"]) if row else None

    def put_ranking(self, ranking: Ranking) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO rankings (snapshot, profile, modality, at, json)"
                " VALUES (?,?,?,?,?)",
                (
                    ranking.snapshot,
                    ranking.profile,
                    ranking.modality,
                    _iso(ranking.computed_at),
                    ranking.model_dump_json(),
                ),
            )

    def ranking(self, profile: str, snapshot: str | None = None) -> Ranking | None:
        if snapshot:
            row = self.db.execute(
                "SELECT json FROM rankings WHERE profile=? AND snapshot=?",
                (profile, snapshot),
            ).fetchone()
        else:
            row = self.db.execute(
                "SELECT json FROM rankings WHERE profile=? ORDER BY at DESC LIMIT 1",
                (profile,),
            ).fetchone()
        return Ranking.model_validate_json(row["json"]) if row else None

    def put_chain(self, chain: Chain) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO chains (profile, computed_at, primary_model,"
                " incumbent, incumbent_since, json) VALUES (?,?,?,?,?,?)",
                (
                    chain.profile,
                    _iso(chain.computed_at),
                    chain.primary,
                    chain.incumbent,
                    _iso(chain.incumbent_since) if chain.incumbent_since else None,
                    chain.model_dump_json(),
                ),
            )

    def chain(self, profile: str) -> Chain | None:
        row = self.db.execute("SELECT json FROM chains WHERE profile=?", (profile,)).fetchone()
        return Chain.model_validate_json(row["json"]) if row else None

    def chains(self) -> list[Chain]:
        return [
            Chain.model_validate_json(r["json"])
            for r in self.db.execute("SELECT json FROM chains ORDER BY profile")
        ]

    # ------------------------------------------------------------------ #
    # decisions (append-only)
    # ------------------------------------------------------------------ #

    def add_decision(self, decision: Decision) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO decisions (id, at, profile, kind, actor, before, after,"
                " reason, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    decision.id,
                    _iso(decision.at),
                    decision.profile,
                    decision.kind,
                    decision.actor,
                    json.dumps(decision.before) if decision.before is not None else None,
                    json.dumps(decision.after) if decision.after is not None else None,
                    decision.reason,
                    json.dumps(decision.detail),
                ),
            )

    def decisions(
        self,
        profile: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[Decision]:
        sql = "SELECT * FROM decisions WHERE 1=1"
        args: list[Any] = []
        if profile:
            sql += " AND profile=?"
            args.append(profile)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if since:
            sql += " AND at >= ?"
            args.append(_iso(since))
        sql += " ORDER BY at DESC LIMIT ?"
        args.append(limit)
        return [
            Decision(
                id=r["id"],
                at=_dt(r["at"]),
                profile=r["profile"],
                kind=r["kind"],
                actor=r["actor"],
                before=json.loads(r["before"]) if r["before"] else None,
                after=json.loads(r["after"]) if r["after"] else None,
                reason=r["reason"],
                detail=json.loads(r["detail"]),
            )
            for r in self.db.execute(sql, args)
        ]

    # ------------------------------------------------------------------ #
    # telemetry (append-only, pruned)
    # ------------------------------------------------------------------ #

    def add_telemetry(self, events: Iterable[TelemetryEvent]) -> int:
        added = 0
        with self.tx() as db:
            for e in events:
                db.execute(
                    "INSERT INTO telemetry (model, profile, ok, status, latency_ms,"
                    " tokens_in, tokens_out, at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        e.model,
                        e.profile,
                        1 if e.ok else 0,
                        e.status,
                        e.latency_ms,
                        e.tokens_in,
                        e.tokens_out,
                        _iso(e.at),
                    ),
                )
                added += 1
        return added

    def telemetry(self, since: datetime | None = None) -> list[TelemetryEvent]:
        sql = "SELECT * FROM telemetry"
        args: tuple[Any, ...] = ()
        if since:
            sql += " WHERE at >= ?"
            args = (_iso(since),)
        sql += " ORDER BY at ASC"
        return [
            TelemetryEvent(
                model=r["model"],
                profile=r["profile"],
                ok=bool(r["ok"]),
                status=r["status"],
                latency_ms=r["latency_ms"],
                tokens_in=r["tokens_in"],
                tokens_out=r["tokens_out"],
                at=_dt(r["at"]),
            )
            for r in self.db.execute(sql, args)
        ]

    def prune_telemetry(self, days: int = TELEMETRY_RETENTION_DAYS) -> int:
        cutoff = _iso(now() - timedelta(days=days))
        with self.tx() as db:
            cur = db.execute("DELETE FROM telemetry WHERE at < ?", (cutoff,))
        return int(cur.rowcount)
