"""The loop as three named steps, each one callable alone and each one recorded.

`sieve run` used to be a single indivisible verb that only a systemd timer
called. That made three ordinary questions unanswerable: which part of the loop
is slow, which part failed, and how do I re-run only that part. So the loop is
named:

- **pull_sources** -- fetch the benchmark sources into the store.
- **harvest_connectors** -- ask every connector what it serves, refresh the
  inventory rows, and re-extract the id prefixes that carry cost multipliers.
- **ship_profiles** -- rank every profile against the current inventory, decide
  the lists, apply the combos of profiles with auto-apply on, write decisions.
- **full** -- harvest, then pull, then ship. Harvest first, because ranking
  against an inventory that was listed eight hours ago is how a model that the
  gateway dropped stays at the top of a list.

Every step writes a `runs` row: who asked, when it started and finished,
whether it worked, one line of summary, and the path to its log. One run at a
time, process-wide *and* box-wide: the in-flight row is the lock, so a run
started from the shell and a run started from the web page cannot overlap.

The cadence lives in the `schedules` table -- one row per step -- and a thread
inside the service fires due steps through exactly the code path `Run now`
uses. Nothing reads `[schedule]` out of `sieve.toml` any more.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import traceback
import uuid
from collections.abc import Iterable
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TextIO
from zoneinfo import ZoneInfo

from sieve.config import DEFAULT_CONFIG, Config
from sieve.store import Store

#: The three steps a person can ask for, and the composite.
SINGLE_STEPS: tuple[str, ...] = ("pull_sources", "harvest_connectors", "ship_profiles")
STEPS: tuple[str, ...] = (*SINGLE_STEPS, "full")

#: What `full` means, in order. Harvest before pull so that a model the gateway
#: stopped serving is out of the inventory before anything is ranked against it.
FULL_ORDER: tuple[str, ...] = ("harvest_connectors", "pull_sources", "ship_profiles")

MODES: tuple[str, ...] = ("off", "hourly", "daily")

Mode = Literal["off", "hourly", "daily"]

#: The timer this replaced fired at 04:30 in the box's own timezone.
SEED_DAILY_AT = "04:30"
FALLBACK_TZ = "Europe/Amsterdam"

_LOG_TAIL = 400_000


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #


def local_timezone() -> str:
    """The timezone the box keeps, so a schedule reads as the wall clock does.

    A systemd `OnCalendar` is local time, so a replacement that silently used
    UTC would move the 04:30 run by several hours the first night.
    """
    named = os.environ.get("SIEVE_TZ", "").strip()
    candidates = [named] if named else []
    link = Path("/etc/localtime")
    try:
        if link.is_symlink():
            target = os.readlink(link)
            if "zoneinfo/" in target:
                candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if text:
            candidates.append(text)
    except OSError:
        pass
    for candidate in candidates:
        try:
            ZoneInfo(candidate)
        except Exception:  # an unknown or unreadable zone is not a zone
            continue
        return candidate
    return FALLBACK_TZ


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(FALLBACK_TZ)


def _hhmm(at_time: str) -> tuple[int, int]:
    try:
        hour, _, minute = at_time.partition(":")
        return max(0, min(23, int(hour))), max(0, min(59, int(minute)))
    except ValueError:
        return 4, 30


def now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# schedules
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Schedule:
    step: str
    mode: str
    at_minute: int
    at_time: str
    timezone: str
    last_fired: datetime | None

    def json(self, at: datetime | None = None) -> dict[str, Any]:
        upcoming = next_fire(self, at)
        return {
            "step": self.step,
            "mode": self.mode,
            "at_minute": self.at_minute,
            "at_time": self.at_time,
            "timezone": self.timezone,
            "last_fired": _iso(self.last_fired) if self.last_fired else None,
            "next_fire": _iso(upcoming) if upcoming else None,
        }


def _schedule(row: Any) -> Schedule:
    return Schedule(
        step=row["step"],
        mode=row["mode"],
        at_minute=int(row["at_minute"]),
        at_time=row["at_time"],
        timezone=row["timezone"],
        last_fired=_dt(row["last_fired"]),
    )


def ensure_schedules(store: Store) -> None:
    """Seed the four rows if they are not there: `full` daily at 04:30 local --
    what the retired timer did -- and the three single steps off."""
    zone = local_timezone()
    with store.tx() as db:
        for step in STEPS:
            db.execute(
                "INSERT OR IGNORE INTO schedules(step,mode,at_minute,at_time,timezone,last_fired)"
                " VALUES(?,?,?,?,?,NULL)",
                (
                    step,
                    "daily" if step == "full" else "off",
                    0,
                    SEED_DAILY_AT,
                    zone,
                ),
            )


def schedules(store: Store) -> list[Schedule]:
    ensure_schedules(store)
    # The composite first: it is the row the status box leads with.
    order = {step: index for index, step in enumerate(("full", *SINGLE_STEPS))}
    rows = [_schedule(r) for r in store.db.execute("SELECT * FROM schedules")]
    return sorted(rows, key=lambda s: order.get(s.step, 99))


def schedule_for(store: Store, step: str) -> Schedule | None:
    for row in schedules(store):
        if row.step == step:
            return row
    return None


def put_schedule(
    store: Store,
    step: str,
    *,
    mode: str,
    at_minute: int | None = None,
    at_time: str | None = None,
) -> Schedule:
    """Change one step's cadence. Raises `ValueError` on anything unusable."""
    if step not in STEPS:
        raise ValueError(f"no such step {step!r}; have {', '.join(STEPS)}")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
    if at_minute is not None and not 0 <= at_minute <= 59:
        raise ValueError("at_minute must be between 0 and 59")
    if at_time is not None:
        hour, minute = _hhmm(at_time)
        if f"{hour:02d}:{minute:02d}" != at_time.strip():
            raise ValueError("at_time must be HH:MM in 24-hour clock")
    current = schedule_for(store, step)
    assert current is not None  # ensure_schedules made it
    with store.tx() as db:
        db.execute(
            "UPDATE schedules SET mode=?, at_minute=?, at_time=? WHERE step=?",
            (
                mode,
                current.at_minute if at_minute is None else at_minute,
                current.at_time if at_time is None else at_time.strip(),
                step,
            ),
        )
    changed = schedule_for(store, step)
    assert changed is not None
    return changed


def mark_fired(store: Store, step: str, at: datetime) -> None:
    with store.tx() as db:
        db.execute("UPDATE schedules SET last_fired=? WHERE step=?", (_iso(at), step))


def next_fire(schedule: Schedule, at: datetime | None = None) -> datetime | None:
    """The next moment this schedule is due, in UTC, or None when it is off."""
    if schedule.mode not in ("hourly", "daily"):
        return None
    moment = (at or now()).astimezone(_zone(schedule.timezone))
    if schedule.mode == "hourly":
        minute = max(0, min(59, schedule.at_minute))
        candidate = moment.replace(minute=minute, second=0, microsecond=0)
        if candidate <= moment:
            candidate = candidate + timedelta(hours=1)
    else:
        hour, minute = _hhmm(schedule.at_time)
        candidate = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= moment:
            candidate = candidate + timedelta(days=1)
    return candidate.astimezone(UTC)


def previous_fire(schedule: Schedule, at: datetime | None = None) -> datetime | None:
    """The most recent moment this schedule was due, in UTC, or None when off."""
    if schedule.mode not in ("hourly", "daily"):
        return None
    moment = (at or now()).astimezone(_zone(schedule.timezone))
    if schedule.mode == "hourly":
        minute = max(0, min(59, schedule.at_minute))
        candidate = moment.replace(minute=minute, second=0, microsecond=0)
        if candidate > moment:
            candidate = candidate - timedelta(hours=1)
    else:
        hour, minute = _hhmm(schedule.at_time)
        candidate = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > moment:
            candidate = candidate - timedelta(days=1)
    return candidate.astimezone(UTC)


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Run:
    id: str
    step: str
    requested_by: str
    started: datetime
    finished: datetime | None = None
    ok: bool | None = None
    summary: str | None = None
    error: str | None = None
    log_path: str | None = None

    @property
    def running(self) -> bool:
        return self.finished is None

    def json(self, at: datetime | None = None) -> dict[str, Any]:
        end = self.finished or (at or now())
        return {
            "id": self.id,
            "step": self.step,
            "requested_by": self.requested_by,
            "started": _iso(self.started),
            "finished": _iso(self.finished) if self.finished else None,
            "running": self.running,
            "ok": self.ok,
            "summary": self.summary,
            "error": self.error,
            "seconds": round((end - self.started).total_seconds(), 1),
            "has_log": bool(self.log_path),
        }


def _run(row: Any) -> Run:
    started = _dt(row["started"])
    assert started is not None
    return Run(
        id=row["id"],
        step=row["step"],
        requested_by=row["requested_by"],
        started=started,
        finished=_dt(row["finished"]),
        ok=None if row["ok"] is None else bool(row["ok"]),
        summary=row["summary"],
        error=row["error"],
        log_path=row["log_path"],
    )


def recent(store: Store, limit: int = 20, step: str | None = None) -> list[Run]:
    sql = "SELECT * FROM runs"
    params: list[Any] = []
    if step:
        sql += " WHERE step = ?"
        params.append(step)
    sql += " ORDER BY started DESC LIMIT ?"
    params.append(max(1, min(limit, 200)))
    return [_run(r) for r in store.db.execute(sql, params)]


def run_by_id(store: Store, run_id: str) -> Run | None:
    row = store.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _run(row) if row else None


def running_run(store: Store) -> Run | None:
    row = store.db.execute(
        "SELECT * FROM runs WHERE finished IS NULL ORDER BY started DESC LIMIT 1"
    ).fetchone()
    return _run(row) if row else None


def last_finished(store: Store, step: str | None = None) -> Run | None:
    sql = "SELECT * FROM runs WHERE finished IS NOT NULL"
    params: list[Any] = []
    if step:
        sql += " AND step = ?"
        params.append(step)
    sql += " ORDER BY finished DESC LIMIT 1"
    row = store.db.execute(sql, params).fetchone()
    return _run(row) if row else None


def reap(store: Store) -> int:
    """Close out rows left in flight by a restart.

    Without this one killed run makes the status box say "running" for ever and
    the 409 guard refuses every later run.
    """
    with store.tx() as db:
        cursor = db.execute(
            "UPDATE runs SET finished=?, ok=0, error=? WHERE finished IS NULL",
            (_iso(now()), "interrupted: the service restarted while this run was in flight"),
        )
    return int(cursor.rowcount or 0)


class RunBusyError(RuntimeError):
    """A run was asked for while another is in flight."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"a run is already going: {run_id}")
        self.run_id = run_id


@dataclass(frozen=True)
class Outcome:
    summary: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# --------------------------------------------------------------------------- #
# the steps themselves
# --------------------------------------------------------------------------- #


def _count(store: Store, table: str) -> int:
    row = store.db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"]) if row else 0


def _pull_sources(cfg: Config, store: Store, config_path: str) -> Outcome:
    """Every enabled source, into the store. Inventories are a different step."""
    from sieve import cli

    names = [s.name for s in cfg.enabled_sources() if s.name not in cfg.inventories]
    if not names:
        return Outcome("no source is enabled")
    before_obs, before_prices = _count(store, "observations"), _count(store, "prices")
    code = cli.cmd_pull(argparse.Namespace(config=config_path, source=names))
    added = _count(store, "observations") - before_obs
    priced = _count(store, "prices") - before_prices
    summary = f"{len(names)} sources pulled, {added} rows pulled, {priced} prices"
    if code:
        # The same sentence the loop has always printed, and it means the same
        # thing: yesterday's observations are still here, and a ranking from
        # them beats no ranking at all. The run is still marked failed.
        print(f"run: {code} source(s) failed; carrying on with what is stored")
        return Outcome(summary, "at least one source could not be read; see the log")
    return Outcome(summary)


def _harvest_connectors(cfg: Config, store: Store, config_path: str) -> Outcome:
    """Ask every connector what it serves, then re-extract the cost prefixes."""
    from sieve import cli
    from sieve.http import client as http_client
    from sieve.profiles import control as profile_control

    failures = cli._refresh_inventories(cfg, store, http_client(), None)
    # A gateway that started serving `newvendor/...` has to get a multiplier
    # row, or its models are priced as though the negotiated rate were 1.0.
    profile_control.sync_prefixes(store)
    rows = store.reachable()
    matched = sum(1 for r in rows if r.model_id)
    prefixes = len(profile_control.multipliers(store))
    summary = (
        f"{len(store.connectors())} connectors, {len(rows)} models found "
        f"({matched} matched), {prefixes} cost prefixes"
    )
    if failures:
        return Outcome(summary, f"{failures} connector(s) could not be listed; see the log")
    return Outcome(summary)


def _ship_profiles(cfg: Config, store: Store, actor: str) -> Outcome:
    """Rank, decide, and ship the combos of profiles that asked to be shipped.

    The same three rules `sieve run` always had: a source being down does not
    stop it, nothing ships unless the profile set `auto_apply`, and every
    profile writes a decision row -- a hold included -- so "ran and changed
    nothing" is distinct from "did not run".
    """
    from sieve.engine import apply_targets
    from sieve.engine import run as engine_run
    from sieve.profiles.load import load_profiles

    profiles = list(load_profiles(cfg.profiles_dir))
    result = engine_run(cfg, profiles=profiles, dry_run=False, actor=actor, store=store)
    for decision in result.decisions:
        print(f"{decision.profile}: {decision.reason}  [{decision.actor}]")
    for warning in result.warnings:
        print(f"warning: {warning}")

    opted_in = {p.name for p in profiles if p.policy.auto_apply}
    shipping = [c for c in result.chains if c.profile in opted_in]
    held = sorted({c.profile for c in result.chains} - opted_in)

    failures = 0
    if not shipping:
        print("apply: no profile has auto_apply, so nothing shipped")
    else:
        for outcome in apply_targets(cfg, shipping, dry_run=False, actor=actor, store=store):
            if outcome.error:
                failures += 1
                print(f"{outcome.target}: {outcome.error}")
            else:
                print(f"{outcome.target}: wrote {', '.join(outcome.written) or '(nothing)'}")
        if held:
            print(f"held (no auto_apply): {', '.join(held)}")

    summary = (
        f"{len(result.rankings)} ranked, {len(result.decisions)} decided, "
        f"{len(shipping)} combos shipped"
    )
    if failures:
        return Outcome(summary, f"{failures} target(s) refused the write; see the log")
    return Outcome(summary)


def execute(cfg: Config, store: Store, step: str, *, actor: str, config_path: str) -> Outcome:
    """Run one step (or `full`) here, on this thread, printing to stdout."""
    if step == "full":
        parts: list[str] = []
        errors: list[str] = []
        for one in FULL_ORDER:
            print(f"--- {one} ---")
            outcome = execute(cfg, store, one, actor=actor, config_path=config_path)
            parts.append(f"{one}: {outcome.summary}")
            if outcome.error:
                errors.append(f"{one}: {outcome.error}")
            print(outcome.summary)
        return Outcome(" · ".join(parts), "; ".join(errors) or None)
    if step == "pull_sources":
        return _pull_sources(cfg, store, config_path)
    if step == "harvest_connectors":
        return _harvest_connectors(cfg, store, config_path)
    if step == "ship_profiles":
        return _ship_profiles(cfg, store, actor)
    raise ValueError(f"no such step {step!r}; have {', '.join(STEPS)}")


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #


class Runner:
    """Starts runs, one at a time, and remembers where their logs went.

    A step runs on a background thread inside this process. It was measured
    first: `/usr/bin/time -v uv run sieve run` peaks at 106 MiB, the service
    sits at about 142 MiB, and the unit's cap is 400 MiB -- so a thread stays
    well inside it and there is no transient unit to supervise.
    """

    def __init__(
        self,
        cfg: Config,
        store: Store,
        *,
        config_path: str | None = None,
        reap_orphans: bool = False,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.config_path = str(config_path or os.environ.get("SIEVE_CONFIG") or DEFAULT_CONFIG)
        self.log_dir = Path(cfg.db_path).parent / "runs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        ensure_schedules(store)
        if reap_orphans:
            reap(store)

    # -- reading ---------------------------------------------------------- #

    def current(self) -> Run | None:
        return running_run(self.store)

    # -- starting --------------------------------------------------------- #

    def _open(self, step: str, requested_by: str, run_id: str | None) -> Run:
        """Claim the one in-flight slot, or raise `RunBusyError`."""
        if step not in STEPS:
            raise ValueError(f"no such step {step!r}; have {', '.join(STEPS)}")
        with self._lock:
            held = running_run(self.store)
            if held is not None:
                raise RunBusyError(held.id)
            run = Run(
                id=run_id or uuid.uuid4().hex[:12],
                step=step,
                requested_by=requested_by,
                started=now(),
                log_path=None,
            )
            log_path = self.log_dir / f"{run.id}.log"
            with self.store.tx() as db:
                db.execute(
                    "INSERT INTO runs(id,step,requested_by,started,log_path) VALUES(?,?,?,?,?)",
                    (run.id, run.step, run.requested_by, _iso(run.started), str(log_path)),
                )
        return replace(run, log_path=str(log_path))

    def _finish(self, run: Run, outcome: Outcome) -> None:
        with self.store.tx() as db:
            db.execute(
                "UPDATE runs SET finished=?, ok=?, summary=?, error=? WHERE id=?",
                (_iso(now()), 1 if outcome.ok else 0, outcome.summary, outcome.error, run.id),
            )

    def _work(self, run: Run, mirror: TextIO | None = None) -> Outcome:
        """The body of a run: everything it prints lands in its own log."""
        path = Path(run.log_path) if run.log_path else self.log_dir / f"{run.id}.log"
        outcome: Outcome
        with path.open("w", encoding="utf-8") as handle:
            header = f"{run.step} · requested by {run.requested_by} · {_iso(run.started)}\n\n"
            handle.write(header)
            handle.flush()
            try:
                with redirect_stdout(_Tee(handle, mirror)):
                    outcome = execute(
                        self.cfg,
                        self.store,
                        run.step,
                        actor=run.requested_by,
                        config_path=self.config_path,
                    )
            except Exception as exc:  # a step that raises is a failed run, not a dead service
                handle.write("\n" + traceback.format_exc())
                outcome = Outcome(f"{run.step} failed", f"{type(exc).__name__}: {exc}")
            handle.write(f"\n--\n{outcome.summary}\n")
            if outcome.error:
                handle.write(f"error: {outcome.error}\n")
        self._finish(run, outcome)
        return outcome

    def start(self, step: str, requested_by: str, run_id: str | None = None) -> Run:
        """Begin a run on a background thread and return its row immediately."""
        run = self._open(step, requested_by, run_id)
        thread = threading.Thread(
            target=self._work, args=(run,), name=f"sieve-run-{run.id}", daemon=True
        )
        self._thread = thread
        thread.start()
        return run

    def run_now(
        self, step: str, requested_by: str, run_id: str | None = None
    ) -> tuple[Run, Outcome]:
        """Begin a run and wait for it. What the command line calls.

        The output is mirrored to the real stdout as well as the log, so
        `sieve run` in a terminal -- or in the journal -- still says what it is
        doing rather than going quiet for six minutes.
        """
        run = self._open(step, requested_by, run_id)
        outcome = self._work(run, mirror=sys.stdout)
        return run, outcome


class _Tee:
    """stdout for a step: into the log, and flushed as it goes so the log route
    shows a run that is still going rather than an empty file."""

    def __init__(self, handle: TextIO, mirror: TextIO | None = None) -> None:
        self._handle = handle
        self._mirror = mirror

    def write(self, text: str) -> int:
        written = self._handle.write(text)
        self._handle.flush()
        if self._mirror is not None:
            self._mirror.write(text)
            self._mirror.flush()
        return written

    def flush(self) -> None:
        self._handle.flush()
        if self._mirror is not None:
            self._mirror.flush()

    def isatty(self) -> bool:
        return False


def read_log(run: Run, tail: int = _LOG_TAIL) -> str:
    if not run.log_path:
        return ""
    path = Path(run.log_path)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= tail else "… earlier output trimmed …\n" + text[-tail:]


# --------------------------------------------------------------------------- #
# the scheduler
# --------------------------------------------------------------------------- #


class Scheduler(threading.Thread):
    """One thread, awake every 30 seconds, firing whatever is due.

    It fires through `Runner.start`, which is the same path `Run now` takes, so
    a scheduled run and a hand-pressed one are the same thing with a different
    `requested_by`.
    """

    def __init__(self, runner: Runner, interval: float = 30.0) -> None:
        super().__init__(name="sieve-scheduler", daemon=True)
        self.runner = runner
        self.interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - the loop itself is a timer
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                traceback.print_exc()

    def tick(self, at: datetime | None = None) -> list[str]:
        """Fire every step whose moment has passed since it last fired."""
        moment = at or now()
        fired: list[str] = []
        for schedule in schedules(self.runner.store):
            if schedule.mode == "off":
                continue
            # A schedule that has never fired starts counting now rather than
            # firing at once: a restart is not a reason to run the loop.
            if schedule.last_fired is None:
                mark_fired(self.runner.store, schedule.step, moment)
                continue
            due = previous_fire(schedule, moment)
            if due is None or schedule.last_fired >= due:
                continue
            if self.runner.current() is not None:
                # Something is running. Leave `last_fired` alone so this is
                # still due on the next wake rather than silently skipped.
                break
            mark_fired(self.runner.store, schedule.step, moment)
            try:
                self.runner.start(schedule.step, "schedule")
                fired.append(schedule.step)
            except RunBusyError:
                break
        return fired


def status_block(store: Store, at: datetime | None = None) -> dict[str, Any]:
    """What `/v1/status` publishes about runs: what is going, and what last was."""
    going = running_run(store)
    last = last_finished(store)
    return {
        "running": going.json(at) if going else None,
        "last": last.json(at) if last else None,
        "last_by_step": {
            step: (row.json(at) if (row := last_finished(store, step)) else None) for step in STEPS
        },
    }


def schedules_block(store: Store, at: datetime | None = None) -> list[dict[str, Any]]:
    return [s.json(at) for s in schedules(store)]


def cadence_of(rows: Iterable[dict[str, Any]], step: str = "full") -> str:
    for row in rows:
        if row["step"] == step:
            mode: str = row["mode"]
            return mode
    return "off"
