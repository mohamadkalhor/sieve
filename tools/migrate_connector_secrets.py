#!/usr/bin/env python3
"""Move connectors from environment-variable names to secret ids.

A connector used to carry `token_env`, the *name* of a variable this box sets,
and the API let anybody with `profiles:write` write it. From A02 a connector
carries `secret`, the id of an entry in `[secrets.<id>]` in `sieve.toml`, which
is edited on the box and never over the wire. This is the one-way move of the
rows that still carry names.

It maps by *name*: a row's `token_env` becomes the id whose `env` is that name
and whose `kinds` include the connector's kind. A name no entry registers is
printed as UNMAPPED and refuses `--apply` outright -- guessing an id would mean
guessing which token a host is given, and this script is not allowed to guess.

Dry run is the default. `--apply` copies the database to
`/srv/personal/backups/agent-io/sieve-<UTC timestamp>.db` first -- through
sqlite's own backup, so a WAL that has not been checkpointed is in the copy --
then writes every id and clears every name in one transaction. It never writes
to `/srv/sieve/data`: point `--db` at a copy, or at the live file from a card
that says so.

    python tools/migrate_connector_secrets.py --db /tmp/aio-sieve-copy.db
    python tools/migrate_connector_secrets.py --db /srv/sieve/data/sieve.db \
        --config /srv/sieve/sieve.toml --apply

Names only, never values: nothing here reads a token, and nothing prints one.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sieve.config import load_config
from sieve.secrets import Secrets

#: Where a backup goes before anything is written. Outside the repo, and outside
#: `/srv/sieve`, because a backup inside the thing being changed is not one.
BACKUPS = Path("/srv/personal/backups/agent-io")

#: The columns A02 adds. `--apply` needs them, which means the service has
#: started once on the new code and run migration 0017.
ADDED = ("secret", "admin_secret")

#: The option key that carried the second credential's name.
ADMIN_OPTION = "admin_token_env"


def credentials(row: sqlite3.Row) -> list[tuple[str, str]]:
    """The (field, environment-variable name) pairs this row still carries."""
    found: list[tuple[str, str]] = []
    if row["token_env"]:
        found.append(("token_env", str(row["token_env"])))
    try:
        options = json.loads(row["options"] or "{}")
    except json.JSONDecodeError:
        options = {}
    if isinstance(options, dict) and options.get(ADMIN_OPTION):
        found.append((ADMIN_OPTION, str(options[ADMIN_OPTION])))
    return found


def plan_for(secrets: Secrets, row: sqlite3.Row) -> list[dict[str, Any]]:
    """One row's move: every name it carries, and the id that name maps to."""
    kind = str(row["kind"])
    return [
        {"field": field, "env": name, "id": secrets.id_for_env(name, kind), "kind": kind}
        for field, name in credentials(row)
    ]


def where(row: sqlite3.Row) -> str:
    return f"owner {row['owner_id']}" if row["owner_id"] else "owner (shared)"


def describe(row: sqlite3.Row, planned: list[dict[str, Any]]) -> None:
    """Print one connector, its names, and the ids they map to."""
    print(f"{row['name']}  kind={row['kind']}  {where(row)}")
    if not planned:
        print("    nothing to move: no environment-variable name on this row")
        return
    width = max(len(item["field"]) for item in planned)
    for item in planned:
        target = "secret" if item["field"] == "token_env" else "admin_secret"
        if item["id"]:
            print(f"    {item['field']:<{width}}  {item['env']}  -> {target} {item['id']}")
        else:
            print(
                f"    {item['field']:<{width}}  {item['env']}  -> UNMAPPED "
                f"(no [secrets.*] entry names {item['env']} for kind {item['kind']})"
            )


def statements(rows: list[sqlite3.Row], planned: dict[str, list[dict[str, Any]]]) -> list[tuple]:
    """The updates `--apply` runs, one per row that has something to move."""
    made: list[tuple] = []
    for row in rows:
        moved = {item["field"]: item["id"] for item in planned[row["id"]] if item["id"]}
        if not moved:
            continue
        sets, args = [], []
        if "token_env" in moved:
            sets.append("secret=?")
            args.append(moved["token_env"])
        if ADMIN_OPTION in moved:
            sets.append("admin_secret=?")
            args.append(moved[ADMIN_OPTION])
        # The name goes, always: the column stays for a reader of the old code,
        # empty, so a database that leaks leaks ids that mean nothing off this box.
        sets.append("token_env=NULL")
        if ADMIN_OPTION in moved:
            options = json.loads(row["options"] or "{}")
            if isinstance(options, dict):
                options.pop(ADMIN_OPTION, None)
                sets.append("options=?")
                args.append(json.dumps(options))
        args.append(row["id"])
        made.append((f"UPDATE connectors SET {', '.join(sets)} WHERE id=?", args))
    return made


def backup(db: sqlite3.Connection, target: Path, stamp: str) -> Path:
    """A consistent copy of the database, WAL included, before anything moves."""
    target.mkdir(parents=True, exist_ok=True)
    copy = target / f"sieve-{stamp}.db"
    with sqlite3.connect(copy) as made:
        db.backup(made)
    return copy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db", required=True, help="the SQLite file to read, and to write with --apply"
    )
    parser.add_argument("--config", default="sieve.toml", help="the file holding [secrets.*]")
    parser.add_argument(
        "--apply", action="store_true", help="write the plan (default: print it and stop)"
    )
    parser.add_argument("--dry-run", action="store_true", help="the default, spelled out")
    parser.add_argument("--backups", default=str(BACKUPS), help="where the copy goes with --apply")
    args = parser.parse_args(argv)
    write = args.apply and not args.dry_run

    config = Path(args.config)
    if not config.exists():
        print(f"no config at {config}; it is what [secrets.*] is read from", file=sys.stderr)
        return 2
    try:
        cfg = load_config(config)
    except ValueError as exc:
        print(f"{config} does not load: {exc}", file=sys.stderr)
        return 2
    secrets = cfg.secret_registry
    print(f"config {config}: {len(secrets)} secret(s) {', '.join(secrets.ids()) or '(none)'}")
    if not len(secrets):
        print(
            "no [secrets.*] entries: every name below will be UNMAPPED. Add the table first.",
            file=sys.stderr,
        )

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"no database at {db_path}", file=sys.stderr)
        return 2
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        columns = {row["name"] for row in db.execute("PRAGMA table_info(connectors)")}
        rows = db.execute(
            "SELECT id, name, owner_id, kind, token_env, options FROM connectors ORDER BY name"
        ).fetchall()
        planned = {row["id"]: plan_for(secrets, row) for row in rows}
        for row in rows:
            describe(row, planned[row["id"]])
        unmapped = [item for row in rows for item in planned[row["id"]] if not item["id"]]
        moves = sum(1 for row in rows for item in planned[row["id"]] if item["id"])
        print(f"{len(rows)} connector(s), {moves} credential(s) to move, {len(unmapped)} UNMAPPED")

        if not write:
            print("DRY RUN: nothing was written. Re-run with --apply to write this plan.")
            return 0
        if unmapped:
            print(
                "refusing to apply: every name needs an id, and "
                f"{len(unmapped)} have none. Add the [secrets.*] entries and run again.",
                file=sys.stderr,
            )
            return 2
        missing = [name for name in ADDED if name not in columns]
        if missing:
            print(
                f"refusing to apply: {db_path} has no {', '.join(missing)} column(s). "
                "Start the service once on the new code (migration 0017 adds them).",
                file=sys.stderr,
            )
            return 2
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        copy = backup(db, Path(args.backups), stamp)
        print(f"backed up to {copy}")
        with db:  # one transaction: every id written and every name cleared, or neither
            for statement, args_for in statements(rows, planned):
                db.execute(statement, args_for)
        print(
            f"applied {len(statements(rows, planned))} row(s); token_env and {ADMIN_OPTION} cleared"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
