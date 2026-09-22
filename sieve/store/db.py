"""SQLite store (CONTRACTS section 4).

Observations, decisions and telemetry are append-only. Migrations are numbered
SQL files in `migrations/`, applied on start inside one transaction each.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sieve.contracts import (
    Capability,
    Chain,
    Connector,
    Decision,
    Modality,
    ModelRef,
    Observation,
    ObsTable,
    Price,
    Rank,
    Ranking,
    Reachable,
    TelemetryEvent,
    unit_fits_modality,
)
from sieve.store.cache import SnapshotCache

MIGRATIONS = Path(__file__).parent / "migrations"

TELEMETRY_RETENTION_DAYS = 30


@dataclass(frozen=True)
class PriceIntake:
    """What `add_prices` stored, and what it would not.

    A bare count cannot say the difference between "the source published two
    prices" and "it published three and one of them was nonsense", and a
    refusal nobody is told about is the same as a silent drop.
    """

    added: int
    refused: list[str]


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


def read_ranking(raw: str) -> Ranking:
    """One stored ranking, however old the shape it was written in.

    A ranking is a cache of a computation, and this box holds months of them:
    every row written before the rebuild carries `dominated_by` and
    `excluded_by`, the two reasons a model could be taken out of a list that no
    longer exist. A strict read turns each of those rows into a 500 on the
    first preview after a deploy -- which is exactly what it did -- so keys the
    contract no longer has are dropped on the way in, like everywhere else.
    """
    try:
        return Ranking.model_validate_json(raw)
    except ValidationError:
        body = json.loads(raw)
        keep = set(Rank.model_fields)
        body["ranks"] = [
            {key: value for key, value in row.items() if key in keep}
            for row in body.get("ranks", [])
        ]
        return Ranking.model_validate(body)


class Store:
    """Everything that touches the database. Nothing else opens a connection.

    One connection per thread. FastAPI runs a sync endpoint in a threadpool, so
    several requests really do read at the same time, and a single sqlite3
    connection shared between them interleaves statements: the symptoms are
    `InterfaceError: bad parameter or other API misuse` and rows that come back
    empty. WAL lets those readers run concurrently without a lock.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._shared = str(self.path) == ":memory:"
        if not self._shared:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._memory: sqlite3.Connection | None = None
        self._all: list[sqlite3.Connection] = []
        self._write_lock = threading.RLock()
        # Built views of the current snapshot, so a ranking costs the ranking
        # and not another scan of the observation table. See `store/cache.py`.
        self.cache = SnapshotCache(self)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        self._all.append(connection)
        return connection

    @property
    def db(self) -> sqlite3.Connection:
        """This thread's connection. An in-memory store keeps one, since a
        second connection would be a second, empty database."""
        if self._shared:
            if self._memory is None:
                self._memory = self._connect()
            return self._memory
        held: sqlite3.Connection | None = getattr(self._local, "db", None)
        if held is None:
            held = self._connect()
            self._local.db = held
        return held

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

    def invalidate_views(self) -> None:
        """Drop the built views. A pull writes a new snapshot and needs no
        call; a harvest changes rows under the same snapshot and does."""
        self.cache.invalidate()

    def close(self) -> None:
        self.cache.invalidate()
        for connection in self._all:
            with suppress(sqlite3.Error):
                connection.close()
        self._all.clear()
        self._memory = None
        self._local = threading.local()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write, on this thread's connection. Writers serialise on WAL."""
        connection = self.db
        with self._write_lock:
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            connection.commit()

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
                    " effort, family, first_seen_at, last_seen_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT (id, modality) DO UPDATE SET"
                    " name=excluded.name, creator=excluded.creator,"
                    " release_date=COALESCE(excluded.release_date, models.release_date),"
                    " effort=COALESCE(excluded.effort, models.effort),"
                    " family=COALESCE(excluded.family, models.family),"
                    " last_seen_at=excluded.last_seen_at",
                    (
                        m.id,
                        m.modality,
                        m.name,
                        m.creator,
                        m.release_date.isoformat() if m.release_date else None,
                        m.effort,
                        m.family,
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

    def model_names(self, modality: Modality | None = None) -> dict[str, str]:
        """Canonical id -> the name a person reads, in one query.

        `models()` costs an alias lookup per row, which is most of a second on
        this catalogue. A list that only has to say "GPT 6 Astra" next to an id
        does not need the aliases, the release date or the effort ladder.
        """
        sql = "SELECT id, name FROM models"
        args: tuple[Any, ...] = ()
        if modality:
            sql += " WHERE modality = ?"
            args = (modality,)
        return {r["id"]: r["name"] for r in self.db.execute(sql, args)}

    def models(self, modality: Modality | None = None) -> list[ModelRef]:
        sql = "SELECT * FROM models"
        args: tuple[Any, ...] = ()
        if modality:
            sql += " WHERE modality = ?"
            args = (modality,)
        sql += " ORDER BY id"
        return [self._model_ref(r) for r in self.db.execute(sql, args).fetchall()]

    def _model_ref(self, r: sqlite3.Row) -> ModelRef:
        aliases = [
            a["alias"]
            for a in self.db.execute(
                "SELECT alias FROM aliases WHERE model_id=? AND modality=? ORDER BY alias",
                (r["id"], r["modality"]),
            )
        ]
        return ModelRef(
            id=r["id"],
            modality=r["modality"],
            name=r["name"],
            creator=r["creator"],
            aliases=aliases,
            release_date=(
                datetime.fromisoformat(r["release_date"]).date() if r["release_date"] else None
            ),
            effort=r["effort"],
            family=r["family"],
        )

    def model(self, model_id: str, modality: Modality | None = None) -> ModelRef | None:
        """One model by id, straight off the primary key.

        `GET /v1/models/{id}` used to walk `models()` -- every model of every
        modality, with an alias query each -- and compare ids. An id is unique
        per modality; without one named, the lowest modality wins, which is the
        row `models()` would have reached first as well.
        """
        sql = "SELECT * FROM models WHERE id = ?"
        args: tuple[Any, ...] = (model_id,)
        if modality:
            sql += " AND modality = ?"
            args = (model_id, modality)
        row = self.db.execute(sql + " ORDER BY modality LIMIT 1", args).fetchone()
        return None if row is None else self._model_ref(row)

    def put_alias(self, alias: str, modality: Modality, model_id: str) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO aliases (alias, modality, model_id, origin) VALUES (?,?,?,'user')"
                " ON CONFLICT (alias, modality) DO UPDATE SET model_id=excluded.model_id,"
                " origin='user'",
                (alias, modality, model_id),
            )

    def alias_rows(self) -> list[dict[str, str]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT alias, modality, model_id, origin FROM aliases ORDER BY alias, modality"
            )
        ]

    def delete_alias(self, alias: str, modality: Modality) -> bool:
        with self.tx() as db:
            row = db.execute(
                "SELECT model_id FROM aliases WHERE alias=? AND modality=?", (alias, modality)
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM aliases WHERE alias=? AND modality=?", (alias, modality))
            # Undo the inventory attachment made by PUT, without clearing a
            # different mapping or another modality's surviving alias.
            db.execute(
                "UPDATE reachable SET model_id=NULL WHERE local_id=? AND model_id=?"
                " AND NOT EXISTS (SELECT 1 FROM aliases WHERE alias=? AND model_id=?)",
                (alias, row["model_id"], alias, row["model_id"]),
            )
        return True

    def delete_cost_multiplier(self, prefix: str, owner_id: str | None = None) -> bool:
        with self.tx() as db:
            return (
                db.execute(
                    "DELETE FROM cost_multipliers WHERE prefix=? AND owner_id IS ?",
                    (prefix, owner_id),
                ).rowcount
                > 0
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

    def add_prices(self, prices: Iterable[Price]) -> PriceIntake:
        """Store the prices that could be true, and name the ones that could not.

        Every source funnels through here, so this is the only placement where
        the check actually holds -- a guard one ingest path can walk around is
        not a guard. See `unit_fits_modality`: it refuses one thing only, a
        picture unit on a model that emits sound.
        """
        added = 0
        refused: list[str] = []
        with self.tx() as db:
            for p in prices:
                if not unit_fits_modality(p.modality, p.unit):
                    refused.append(f"{p.model_id} ({p.source}): {p.unit} on {p.modality}")
                    continue
                cur = db.execute(
                    "INSERT OR IGNORE INTO prices (model_id, source, modality, unit, input,"
                    " output, cached_input, per_unit, source_url, tier, observed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        p.model_id,
                        p.source,
                        p.modality,
                        p.unit,
                        p.input,
                        p.output,
                        p.cached_input,
                        p.per_unit,
                        p.source_url,
                        p.tier,
                        _iso(p.observed_at),
                    ),
                )
                added += cur.rowcount if cur.rowcount > 0 else 0
        return PriceIntake(added=added, refused=refused)

    def latest_prices(
        self, modality: Modality, only: Collection[str] | None = None
    ) -> dict[str, Price]:
        """One price per model: the latest pull, and its cheapest tier.

        A tiered model publishes several prices and a ranking needs one, so the
        choice has to be declared rather than left to insertion order. It is the
        cheapest -- which on fal's catalogue is always the lowest resolution
        offered, checked against every tiered model in the recordings -- and the
        row carries its `tier`, so the screen can say which one it is rather
        than presenting a floor as the price.

        Rows are visited dearest first and the dict keeps the last, so the
        cheapest wins. `COALESCE(per_unit, output, input)` compares a flat rate
        and a token rate on the same expression; for a token price the output
        rate is the one that decides, which is why it comes before input.

        Only the winner of each model is turned into a `Price`. The ordering
        visits 150,718 rows to decide 982 of them on the live store, and
        building the 149,736 that lose was most of the 3.6 s this once took.

        `only` prices the models named and no others, which is what a page of
        the catalogue needs: the price rows of five models rather than of the
        whole modality.
        """
        sql = (
            "SELECT p.* FROM prices p JOIN models m"
            " ON m.id = p.model_id AND m.modality = ?"
            " WHERE (p.modality IS NULL OR p.modality = ?)"
        )
        args: list[Any] = [modality, modality]
        if only is not None:
            wanted = list(only)
            if not wanted:
                return {}
            sql += f" AND p.model_id IN ({','.join('?' * len(wanted))})"
            args.extend(wanted)
        rows = self.db.execute(
            sql + " ORDER BY (p.modality IS NULL), p.observed_at ASC,"
            " COALESCE(p.per_unit, p.output, p.input) DESC",
            tuple(args),
        )
        winners: dict[str, sqlite3.Row] = {}
        for r in rows:
            winners[r["model_id"]] = r
        out: dict[str, Price] = {}
        for r in winners.values():
            out[r["model_id"]] = Price(
                model_id=r["model_id"],
                source=r["source"],
                modality=r["modality"],
                unit=r["unit"],
                input=r["input"],
                output=r["output"],
                cached_input=r["cached_input"],
                per_unit=r["per_unit"],
                source_url=r["source_url"],
                tier=r["tier"],
                observed_at=_dt(r["observed_at"]),
            )
        return out

    def obs_table(self, modality: Modality) -> ObsTable:
        """The latest observation per (model, source, field) for one modality.

        SQLite returns, for a bare column beside `MAX()`, the value from the row
        that holds the maximum, so one grouped pass over `observations_lookup`
        answers "the latest of each" without reading the history that is not the
        latest. Observations are append-only: every earlier measurement of the
        same (model, source, field) is still there, and on the live store that
        is 1,046,838 llm rows to arrive at 7,536. Measured read-only on
        2026-09-14: 34 s and 786 MB of rows before, 0.55 s after, the same table
        out of both.

        `MAX(observed_at)` is named, so the extra column cannot collide with
        `observations.observed_at`, which is the one `_observation` reads.
        """
        table = ObsTable(modality=modality, prices=self.latest_prices(modality))
        rows = self.db.execute(
            "SELECT *, MAX(observed_at) AS latest_observed_at FROM observations"
            " WHERE modality=? GROUP BY source, field, model_id",
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

    def capabilities_by_source(
        self, model_id: str, modality: Modality
    ) -> list[tuple[str, Capability]]:
        """What each source says about one model, one row per source, in name order.

        `capabilities()` answers "what can this model do" by merging the
        statements and losing who made them. A model card has to show the claim
        *and its author*, so this keeps them apart: the caller applies
        `sieve.scoring.select.has()` to each observation and lists the sources
        that said yes and the ones that said no. The order is by source name so
        a screen draws the same card twice the same way.
        """
        return [
            (r["source"], Capability.model_validate_json(r["capability"]))
            for r in self.db.execute(
                "SELECT source, capability FROM capabilities"
                " WHERE model_id=? AND modality=? ORDER BY source",
                (model_id, modality),
            )
        ]

    # ------------------------------------------------------------------ #
    # inventory
    # ------------------------------------------------------------------ #

    def set_reachable(
        self, inventory: str, items: Iterable[Reachable], connector_id: str | None = None
    ) -> int:
        """Record what one router is serving *now*, and retire what it is not.

        `connector_id` is the stable identity behind the name: renaming a
        connector does not orphan its rows, and "reachable via gateway A, not
        B" becomes a question the store can answer.

        A pull is the whole truth about that inventory at that moment, so
        anything it did not list stops being routable the instant it lands. The
        row is not deleted -- an id that was reachable last week is a fact worth
        keeping -- it is marked `stale`, and every reader that asks "where can I
        send traffic" reads only the fresh rows. Before this, a provider that
        went dark left its ids sitting in the inventory looking alive, and the
        hourly run kept seating them in combos.
        """
        rows = 0
        with self.tx() as db:
            db.execute("UPDATE reachable SET stale=1 WHERE inventory=?", (inventory,))
            for it in items:
                db.execute(
                    "INSERT OR REPLACE INTO reachable (inventory, local_id, model_id,"
                    " capability, seen_at, connector_id, stale) VALUES (?,?,?,?,?,?,0)",
                    (
                        it.inventory,
                        it.local_id,
                        it.model_id,
                        it.capability.model_dump_json(),
                        _iso(it.seen_at),
                        connector_id,
                    ),
                )
                rows += 1
        return rows

    def reachable_for(self, connector_id: str, *, include_stale: bool = False) -> list[Reachable]:
        """What one connector was last seen serving. No network, ever."""
        sql = "SELECT * FROM reachable WHERE connector_id=?"
        if not include_stale:
            sql += " AND stale=0"
        return [
            self._reachable(r) for r in self.db.execute(sql + " ORDER BY local_id", (connector_id,))
        ]

    @staticmethod
    def _reachable(r: sqlite3.Row) -> Reachable:
        return Reachable(
            inventory=r["inventory"],
            local_id=r["local_id"],
            model_id=r["model_id"],
            capability=Capability.model_validate_json(r["capability"]),
            seen_at=_dt(r["seen_at"]),
            stale=bool(r["stale"]),
        )

    def reachable(
        self, *, unmatched: bool | None = None, include_stale: bool = False
    ) -> list[Reachable]:
        """The inventory. Only the last successful pull, unless asked otherwise."""
        where = [] if include_stale else ["stale=0"]
        if unmatched is True:
            where.append("model_id IS NULL")
        elif unmatched is False:
            where.append("model_id IS NOT NULL")
        sql = "SELECT * FROM reachable"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY inventory, local_id"
        return [self._reachable(r) for r in self.db.execute(sql)]

    def local_ids(
        self, owner_id: str | None = None, modality: Modality | None = None
    ) -> dict[str, list[str]]:
        """Canonical model id -> the local ids that serve it, right now.

        Stale rows are left out on purpose: this map is what a chain resolves
        through, and a chain is a routing instruction, not a history.

        With an `owner_id`, only what *that person's* connectors serve: a chain
        is seated on their routers, and a local id nobody they own can reach is
        not somewhere they can send traffic.

        With a `modality`, only ids the catalogue holds in that modality. A
        router id is matched to a catalogue id with no modality, so a row here
        says the box can call a model and nothing about what it makes; without
        this an image seat ranked, and shipped, every llm the box can reach.
        """
        sql = (
            "SELECT r.model_id AS model_id, r.local_id AS local_id FROM reachable r"
            " LEFT JOIN connectors c ON c.id = r.connector_id"
            " WHERE r.model_id IS NOT NULL AND r.stale=0"
        )
        args: tuple[Any, ...] = ()
        if owner_id is not None:
            sql += " AND IFNULL(c.owner_id,'') = IFNULL(?,'')"
            args = (owner_id,)
        if modality is not None:
            sql += " AND EXISTS (SELECT 1 FROM models m WHERE m.id = r.model_id AND m.modality = ?)"
            args = (*args, modality)
        out: dict[str, list[str]] = {}
        for r in self.db.execute(sql + " ORDER BY r.local_id", args):
            out.setdefault(r["model_id"], []).append(r["local_id"])
        return out

    # ------------------------------------------------------------------ #
    # connectors (the routers, as data)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _connector(r: sqlite3.Row) -> Connector:
        return Connector(
            id=r["id"],
            name=r["name"],
            kind=r["kind"],
            base_url=r["base_url"],
            token_env=r["token_env"],
            read=bool(r["read"]),
            write=bool(r["write"]),
            poll_minutes=int(r["poll_minutes"]),
            last_pull_at=_dt(r["last_pull_at"]) if r["last_pull_at"] else None,
            last_push_at=_dt(r["last_push_at"]) if r["last_push_at"] else None,
            last_error=r["last_error"],
            options=json.loads(r["options"] or "{}"),
            created_at=_dt(r["created_at"]) if r["created_at"] else None,
            owner_id=r["owner_id"],
        )

    @staticmethod
    def _owned(owner_id: str | None, column: str = "owner_id") -> str:
        """`WHERE` fragment for one owner's rows, NULL included."""
        return f"IFNULL({column},'') = IFNULL(?,'')"

    def has_connectors(self, owner_id: str | None = None) -> bool:
        """Whether anything has been seeded yet. One row is enough to know."""
        sql = "SELECT 1 FROM connectors"
        args: tuple[Any, ...] = ()
        if owner_id is not None:
            sql += f" WHERE {self._owned(owner_id)}"
            args = (owner_id,)
        return self.db.execute(sql + " LIMIT 1", args).fetchone() is not None

    def connectors(self, owner_id: str | None = None) -> list[Connector]:
        """Every connector, or only this owner's. A connector is never shared:
        it is somebody's router, holding their token, and a combo written to it
        is written with their credentials."""
        sql = "SELECT * FROM connectors"
        args: tuple[Any, ...] = ()
        if owner_id is not None:
            sql += f" WHERE {self._owned(owner_id)}"
            args = (owner_id,)
        return [self._connector(r) for r in self.db.execute(sql + " ORDER BY name", args)]

    def connector(self, connector_id: str) -> Connector | None:
        row = self.db.execute("SELECT * FROM connectors WHERE id=?", (connector_id,)).fetchone()
        return self._connector(row) if row else None

    def connector_named(self, name: str, owner_id: str | None = None) -> Connector | None:
        row = self.db.execute(
            f"SELECT * FROM connectors WHERE name=? AND {self._owned(owner_id)}",
            (name, owner_id),
        ).fetchone()
        return self._connector(row) if row else None

    def add_connector(self, connector: Connector) -> Connector:
        """Insert one. A duplicate name raises, because a name addresses it."""
        with self.tx() as db:
            db.execute(
                "INSERT INTO connectors (id, name, kind, base_url, token_env, read, write,"
                " poll_minutes, last_pull_at, last_push_at, last_error, options, created_at,"
                " owner_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._connector_row(connector),
            )
        return connector

    def put_connector(self, connector: Connector) -> Connector:
        """Insert or replace by id. What the API's PUT writes."""
        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO connectors (id, name, kind, base_url, token_env,"
                " read, write, poll_minutes, last_pull_at, last_push_at, last_error,"
                " options, created_at, owner_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._connector_row(connector),
            )
        return connector

    @staticmethod
    def _connector_row(c: Connector) -> tuple[Any, ...]:
        return (
            c.id,
            c.name,
            c.kind,
            c.base_url,
            c.token_env,
            1 if c.read else 0,
            1 if c.write else 0,
            c.poll_minutes,
            _iso(c.last_pull_at) if c.last_pull_at else None,
            _iso(c.last_push_at) if c.last_push_at else None,
            c.last_error,
            json.dumps(c.options),
            _iso(c.created_at) if c.created_at else _iso(now()),
            c.owner_id,
        )

    def delete_connector(self, connector_id: str) -> bool:
        """Forget a connector, and the inventory rows that only it served."""
        with self.tx() as db:
            db.execute("DELETE FROM reachable WHERE connector_id=?", (connector_id,))
            cur = db.execute("DELETE FROM connectors WHERE id=?", (connector_id,))
        return bool(cur.rowcount)

    def touch_connector(
        self,
        connector_id: str,
        *,
        pulled_at: datetime | None = None,
        pushed_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        """Record what just happened to a connector.

        A timestamp left None is left alone; `error` is always written, so a
        successful call clears the last failure rather than leaving a sentence
        on the screen that stopped being true an hour ago.
        """
        sets = ["last_error=?"]
        args: list[Any] = [error]
        if pulled_at is not None:
            sets.append("last_pull_at=?")
            args.append(_iso(pulled_at))
        if pushed_at is not None:
            sets.append("last_push_at=?")
            args.append(_iso(pushed_at))
        args.append(connector_id)
        with self.tx() as db:
            db.execute(f"UPDATE connectors SET {', '.join(sets)} WHERE id=?", args)

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

    def latest_snapshot_at(self) -> datetime | None:
        """When a source was last pulled, whether or not it brought anything new.

        Not `MAX(observations.pulled_at)`: an observation that is already stored
        is not written again, so a pull that found nothing new leaves that
        maximum where it was, and a loop running perfectly every hour would read
        as stuck. Every pull makes a snapshot, changed data or not.
        """
        row = self.db.execute("SELECT at FROM snapshots ORDER BY at DESC LIMIT 1").fetchone()
        return _dt(row["at"]) if row else None

    def put_ranking(self, ranking: Ranking, owner_id: str | None = None) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO rankings (snapshot, profile, modality, at, json, owner_id)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(snapshot,profile,IFNULL(owner_id,''))"
                " DO UPDATE SET modality=excluded.modality, at=excluded.at, json=excluded.json",
                (
                    ranking.snapshot,
                    ranking.profile,
                    ranking.modality,
                    _iso(ranking.computed_at),
                    ranking.model_dump_json(),
                    owner_id,
                ),
            )

    def ranking(
        self, profile: str, snapshot: str | None = None, owner_id: str | None = None
    ) -> Ranking | None:
        owned = self._owned(owner_id)
        if snapshot:
            row = self.db.execute(
                f"SELECT json FROM rankings WHERE profile=? AND snapshot=? AND {owned}",
                (profile, snapshot, owner_id),
            ).fetchone()
        else:
            row = self.db.execute(
                f"SELECT json FROM rankings WHERE profile=? AND {owned} ORDER BY at DESC LIMIT 1",
                (profile, owner_id),
            ).fetchone()
        return read_ranking(row["json"]) if row else None

    def put_chain(self, chain: Chain, owner_id: str | None = None) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO chains (profile, computed_at, primary_model,"
                " incumbent, incumbent_since, json, owner_id) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(IFNULL(owner_id,''),profile) DO UPDATE SET"
                " computed_at=excluded.computed_at, primary_model=excluded.primary_model,"
                " incumbent=excluded.incumbent, incumbent_since=excluded.incumbent_since,"
                " json=excluded.json",
                (
                    chain.profile,
                    _iso(chain.computed_at),
                    chain.primary,
                    chain.incumbent,
                    _iso(chain.incumbent_since) if chain.incumbent_since else None,
                    chain.model_dump_json(),
                    owner_id,
                ),
            )

    def chain(self, profile: str, owner_id: str | None = None) -> Chain | None:
        row = self.db.execute(
            f"SELECT json FROM chains WHERE profile=? AND {self._owned(owner_id)}",
            (profile, owner_id),
        ).fetchone()
        return Chain.model_validate_json(row["json"]) if row else None

    def chains(self, owner_id: str | None = None) -> list[Chain]:
        sql = "SELECT json FROM chains"
        args: tuple[Any, ...] = ()
        if owner_id is not None:
            sql += f" WHERE {self._owned(owner_id)}"
            args = (owner_id,)
        return [
            Chain.model_validate_json(r["json"])
            for r in self.db.execute(sql + " ORDER BY profile", args)
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
