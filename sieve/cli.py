"""`sieve` — the command line.

Every verb is wired to the real code path from the first hour. Where the work
belongs to another owner, the verb fails with `NotImplementedError: <module> is
owned by <letter> and has not landed yet` instead of an import traceback.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sieve import plugins
from sieve.catalog.aliases import load_aliases
from sieve.catalog.registry import merge_pull
from sieve.config import DEFAULT_CONFIG, Config, default_config, load_config
from sieve.contracts import Profile, PullResult
from sieve.engine import COST_SOURCE, OwnerMissingError, apply_targets, run
from sieve.http import client as http_client
from sieve.http import fixtures_enabled
from sieve.store import Store

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_BUILT = 3


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _config(args: argparse.Namespace) -> Config:
    """The config the command runs against.

    A missing `sieve.toml` where none was asked for is normal -- a fresh
    install has no config and the defaults are usable. A `--config` naming a
    file that is not there is a mistake, and used to fall back to the defaults
    without a word: the command then ran against a different store and a
    different set of sources, and looked like the source published nothing.
    """
    path = Path(args.config)
    if path.exists():
        return load_config(path)
    if args.config != DEFAULT_CONFIG:
        raise ValueError(f"no such config file: {path}")
    return default_config()


def _profiles(cfg: Config, names: Sequence[str] | None = None) -> list[Profile]:
    from importlib import import_module

    try:
        loader = import_module("sieve.profiles.load")
    except ModuleNotFoundError as exc:
        raise OwnerMissingError("sieve.profiles.load", "B") from exc
    found: list[Profile] = list(loader.load_profiles(cfg.profiles_dir))
    if names:
        wanted = set(names)
        found = [p for p in found if p.name in wanted]
        missing = wanted - {p.name for p in found}
        if missing:
            raise SystemExit(f"no such profile: {', '.join(sorted(missing))}")
    return found


def _out(text: str = "") -> None:
    print(text)


# --------------------------------------------------------------------------- #
# verbs
# --------------------------------------------------------------------------- #


def cmd_pull(args: argparse.Namespace) -> int:
    cfg = _config(args)
    store = Store(cfg.db_path)
    http = http_client()
    wanted = args.source or [s.name for s in cfg.enabled_sources()]
    failures = 0
    # The hand-written alias file says it beats every rule in the matcher, and
    # until now it did not reach a pull at all: only the store table did, so an
    # alias written to join two sources -- the whole reason media has both a
    # score and a price -- was never consulted. Store entries still win, since
    # those were learned from a gateway that actually serves the id.
    file_aliases = load_aliases(cfg.aliases_file)

    for name in wanted:
        source_cfg = cfg.sources.get(name)
        if source_cfg is None:
            if name in cfg.inventories:
                continue  # an inventory name; handled below
            _out(f"{name}: no such source or inventory in sieve.toml")
            failures += 1
            continue
        try:
            source = plugins.load(plugins.SOURCES, name)
        except (LookupError, ImportError) as exc:
            _out(f"{name}: {exc}")
            failures += 1
            continue
        # A fixture run answers from disk, so a key it will never send is not
        # a reason to skip the source -- that silently emptied every AA pull
        # under SIEVE_FIXTURES=1.
        if getattr(source, "needs_key", False) and not source_cfg.key() and not fixtures_enabled():
            _out(f"{name}: needs {source_cfg.key_env}; skipped")
            continue
        result = source.pull(source_cfg, http)
        if not result.ok:
            # the endpoint could not be read. Not the same as an endpoint that
            # published nothing new, which is an ordinary quiet hour.
            failures += 1

        # two sources naming one model must land on one record
        result, folded = merge_pull(
            result, [m.id for m in store.models()], {**file_aliases, **store.aliases()}
        )
        result, unconfirmed = confirm_provisional(result, store)

        if unconfirmed:
            _out(
                f"  {unconfirmed} model(s) dropped: their category spans more than one"
                " modality and nothing that separates them recognised the id"
            )
        snapshot = store.new_snapshot(source_rows=len(result.observations))
        store.upsert_models(result.models)
        added = store.add_observations(result.observations, snapshot=snapshot)
        priced = store.add_prices(result.prices)
        for modality in {m.modality for m in result.models} or set(source_cfg.modalities):
            store.set_capabilities(
                name,
                modality,
                {
                    model_id: capability
                    for model_id, capability in result.capabilities.items()
                    if any(m.id == model_id and m.modality == modality for m in result.models)
                },
            )
        _out(
            f"{name}: {len(result.models)} models, {added} new observations "
            f"({len(result.observations)} seen), {priced} prices"
        )
        for was, now in sorted(folded.items())[:5]:
            _out(f"  merged: {was} -> {now}")
        if len(folded) > 5:
            _out(f"  ... and {len(folded) - 5} more merged into existing ids")
        for warning in result.warnings:
            _out(f"  warning: {warning}")
        if result.rate_limit.remaining is not None:
            _out(f"  rate limit remaining: {result.rate_limit.remaining}")

    if not args.source or any(n in cfg.inventories for n in args.source):
        failures += _refresh_inventories(cfg, store, http, args.source)
    return EXIT_ERROR if failures else EXIT_OK


def _refresh_inventories(
    cfg: Config, store: Store, http: Any, only: Sequence[str] | None = None
) -> int:
    """List every gateway, match each local id to the catalog, store the result.

    An id that cannot be matched confidently keeps `model_id` None and shows up
    on the Sources screen for a person to alias. It is never guessed at and
    never dropped.
    """
    from sieve.catalog.aliases import load_aliases
    from sieve.catalog.registry import Registry

    wanted = [n for n in (only or cfg.inventories) if n in cfg.inventories]
    if not wanted:
        return 0

    registry = Registry(load_aliases(cfg.aliases_file))
    registry.extend(store.models())

    failures = 0
    for name in wanted:
        inventory_cfg = cfg.inventories[name]
        try:
            inventory = plugins.load(plugins.INVENTORIES, inventory_cfg.kind)
            found = inventory.list(inventory_cfg, http)
        except Exception as exc:  # a gateway being down is not a crash
            _out(f"{name}: {exc}")
            failures += 1
            continue
        matched, unmatched = registry.attach(found)
        store.set_reachable(name, matched + unmatched)
        _out(f"{name}: {len(found)} reachable, {len(matched)} matched, {len(unmatched)} unmatched")
        for item in unmatched[:5]:
            _out(f"  unmatched: {item.local_id}")
        if len(unmatched) > 5:
            _out(f"  ... and {len(unmatched) - 5} more; see /v1/inventory?unmatched=true")
    return failures


def confirm_provisional(result: PullResult, store: Store) -> tuple[PullResult, int]:
    """Keep a claimed modality only where a source that separates them agrees.

    PLAN 2.2. A source whose own category spans several of our modalities --
    fal files music generation and sound effects together under `text-to-audio`
    -- cannot say which one a row is. So it offers the modality and this decides:
    the claim stands only if the id is one the catalogue already holds in that
    modality, which means something that *does* distinguish them measured it.

    Everything else is dropped and counted. It is not assigned to the nearest
    modality, because a list nobody can rank is dead weight on every screen, and
    it is not given a modality of its own, because nothing measures it.
    """
    if not result.provisional:
        return result, 0

    known = {m.id for m in store.models()}
    keep = {model_id for model_id in result.provisional if model_id in known}
    drop = result.provisional - keep
    if not drop:
        return result, 0

    kept = result.model_copy(deep=True)
    kept.models = [m for m in kept.models if m.id not in drop]
    kept.observations = [o for o in kept.observations if o.model_id not in drop]
    kept.prices = [p for p in kept.prices if p.model_id not in drop]
    kept.provisional = keep
    return kept, len(drop)


def cmd_check(args: argparse.Namespace) -> int:
    from importlib import import_module

    cfg = _config(args)
    problems: list[str] = []

    try:
        axes_load = import_module("sieve.axes.load")
    except ModuleNotFoundError as exc:
        raise OwnerMissingError("sieve.axes.load", "A") from exc
    try:
        validate = import_module("sieve.profiles.validate")
    except ModuleNotFoundError as exc:
        raise OwnerMissingError("sieve.profiles.validate", "B") from exc

    # a file that will not parse is one problem, reported like any other --
    # never a traceback, because the person fixing it is editing YAML.
    try:
        axes = list(axes_load.load_all_axes(cfg.axes_dir))
        _out(f"axes: {len(axes)} loaded from {cfg.axes_dir}")
    except ValueError as exc:
        _out(f"axes: could not load {cfg.axes_dir}")
        _out(f"  fail: {exc}")
        return EXIT_ERROR

    try:
        profiles = _profiles(cfg)
    except ValueError as exc:
        _out(f"profiles: could not load {cfg.profiles_dir}")
        _out(f"  fail: {exc}")
        return EXIT_ERROR

    axis_names = {(a.modality, a.name) for a in axes}
    for profile in profiles:
        problems += list(validate.validate_profile(profile, axis_names))

    # `/v1/chains/{profile}` and the chains table are keyed by name alone, so
    # two profiles sharing a name would silently share one chain.
    by_name: dict[str, list[str]] = {}
    for profile in profiles:
        by_name.setdefault(profile.name, []).append(profile.modality)
    for name, modalities in sorted(by_name.items()):
        if len(modalities) > 1:
            problems.append(
                f"profile name {name!r} is used by more than one modality "
                f"({', '.join(sorted(modalities))}); names address a profile on their "
                "own, so they must be unique across the whole profiles directory"
            )
    _out(f"profiles: {len(profiles)} loaded from {cfg.profiles_dir}")

    problems += _axes_against_the_data(cfg, axes)

    for problem in problems:
        _out(f"  fail: {problem}")
    _out("check: green" if not problems else f"check: {len(problems)} problem(s)")
    return EXIT_OK if not problems else EXIT_ERROR


def _axes_against_the_data(cfg: Config, axes: list[Any]) -> list[str]:
    """An axis whose field nothing publishes is an error, not coverage 0.

    Four shipped axes named a per-category Elo the API does not publish --
    `elo:photoreal` where the real category is `elo:photorealistic`. Nothing
    caught it: the axis was valid, the field simply never appeared, so the axis
    reported coverage 0 for every model and quietly stopped counting. That is
    the silent zero the rules forbid, arriving through the back door.

    Only modalities the store actually holds observations for are judged. On a
    fresh install nothing has been pulled, and an axis cannot be wrong about
    data that is not there yet.
    """
    store = Store(cfg.db_path)
    published: dict[str, set[str]] = {}
    for row in store.db.execute("SELECT DISTINCT modality, source, field FROM observations"):
        published.setdefault(row["modality"], set()).add(f"{row['source']}:{row['field']}")
    if not published:
        return []

    out: list[str] = []
    for axis in axes:
        known = published.get(axis.modality)
        if not known:
            continue
        for field in axis.fields:
            key = f"{field.source}:{field.field}"
            # a phase-2 field is declared before its source exists, on purpose
            if getattr(field, "phase", 1) > 1 or field.source == COST_SOURCE:
                continue
            if key not in known:
                near = sorted(k for k in known if k.split(":", 1)[0] == field.source)[:3]
                out.append(
                    f"axis {axis.name!r} ({axis.modality}) reads {key!r}, which "
                    f"{field.source} has never published in this store -- it would sit at "
                    f"coverage 0 for ever. Nearest fields it does publish: "
                    f"{', '.join(near) or 'none'}"
                )
    return out


def cmd_score(args: argparse.Namespace) -> int:
    cfg = _config(args)
    profiles = _profiles(cfg, [args.profile] if args.profile else None)
    if not profiles:
        _out("no profiles")
        return EXIT_ERROR
    result = run(cfg, profiles=profiles, dry_run=True)
    for ranking in result.rankings:
        _print_ranking(ranking, limit=args.limit)
    for warning in result.warnings:
        _out(f"warning: {warning}")
    return EXIT_OK


def _print_ranking(ranking: Any, *, limit: int) -> None:
    _out(f"\n{ranking.profile}  ({ranking.modality})  snapshot {ranking.snapshot}")
    if not ranking.ranks:
        _out("  (no models — pull a source first)")
        return
    header = f"{'#':>3}  {'model':<44} {'score':>7} {'conf':>6} {'health':>7} {'final':>7}  reach"
    _out(header)
    _out("-" * len(header))
    shown = 0
    for rank in ranking.ranks:
        if rank.position == 0:
            continue
        shown += 1
        if shown > limit:
            break
        _out(
            f"{rank.position:>3}  {rank.model_id:<44} {rank.score:>7.3f} "
            f"{rank.confidence:>6.2f} {rank.health:>7.2f} {rank.final:>7.3f}"
            f"  {'yes' if rank.reachable else '-'}"
        )
        contributions = ", ".join(
            f"{a.axis} {a.contribution:+.3f} (cov {a.coverage:.2f})" for a in rank.axes
        )
        if contributions:
            _out(f"       {contributions}")
        # say when a cost is an estimate. The profile's shape is the same for
        # every effort mode of a model, so a cost read from it cannot tell them
        # apart, and a reader deserves to know that before acting on it.
        if rank.cost_per_task is not None:
            basis = (
                "estimated from the profile shape"
                if rank.cost_from == "shape"
                else "from measured tokens"
            )
            _out(f"       cost ${rank.cost_per_task:.4f} per task, {basis}")
        if rank.flip:
            _out(f"       flip: {rank.flip}")
    for rank in ranking.ranks:
        if rank.dominated_by:
            _out(f"  --  {rank.model_id}: dominated by {rank.dominated_by}")
        elif rank.excluded_by:
            _out(f"  --  {rank.model_id}: excluded by {rank.excluded_by}")


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _config(args)
    profiles = _profiles(cfg, args.profile)
    result = run(cfg, profiles=profiles, dry_run=args.dry_run)
    for chain in result.chains:
        _out(f"{chain.profile}: {chain.primary} -> {', '.join(chain.fallbacks) or '(none)'}")
    for decision in result.decisions:
        _out(f"  {decision.reason}  [{decision.actor}]")
    if not result.chains and not result.decisions:
        _out("nothing to plan yet")
    for warning in result.warnings:
        _out(f"warning: {warning}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """The loop: pull every enabled source, evaluate every profile, decide, and
    apply **only** where the profile opted in with `policy.auto_apply`.

    This is what the timer calls. Three things make it safe to leave running:

    - **A source that is down does not stop the run.** Yesterday's observations
      are still in the store, and a ranking computed from them is far better
      than no ranking at all. The failure is counted, reported, and the exit
      code says so, so a monitor still sees it.
    - **Nothing ships unless a profile asked for it.** `auto_apply` is per
      profile and defaults to false, so adding a target does not silently put it
      in charge of every seat.
    - **Every profile writes a decision row every run**, including a hold. A
      run that changed nothing has to be as visible as one that changed
      everything, or "the schedule is working" and "the schedule is stuck" look
      identical in the log.
    """
    cfg = _config(args)
    store = Store(cfg.db_path)
    started = datetime.now(UTC)

    # -- pull ---------------------------------------------------------- #
    pull_failures = 0
    if not args.no_pull:
        pull_args = argparse.Namespace(**vars(args))
        pull_args.source = None
        pull_failures = cmd_pull(pull_args)
        if pull_failures:
            _out(f"run: {pull_failures} source(s) failed; carrying on with what is stored")

    # -- evaluate and decide -------------------------------------------- #
    profiles = _profiles(cfg, args.profile)
    result = run(cfg, profiles=profiles, dry_run=args.dry_run, actor=args.actor, store=store)

    for decision in result.decisions:
        _out(f"{decision.profile}: {decision.reason}  [{decision.actor}]")
    silent = [p.name for p in profiles if not any(d.profile == p.name for d in result.decisions)]
    for name in silent:
        _out(f"{name}: no decision recorded -- nothing ranked for it")
    for warning in result.warnings:
        _out(f"warning: {warning}")

    # -- apply, but only where the profile asked ------------------------ #
    opted_in = {p.name for p in profiles if p.policy.auto_apply}
    shipping = [c for c in result.chains if c.profile in opted_in]
    held_back = sorted({c.profile for c in result.chains} - opted_in)

    apply_failures = 0
    if not shipping:
        _out(
            "apply: no profile has auto_apply, so nothing shipped"
            + (f" ({len(held_back)} computed and held)" if held_back else "")
        )
    else:
        outcomes = apply_targets(
            cfg,
            shipping,
            targets=args.target,
            dry_run=args.dry_run,
            actor=args.actor,
            store=store,
        )
        verb = "would write" if args.dry_run else "wrote"
        for outcome in outcomes:
            if outcome.error:
                apply_failures += 1
                _out(f"{outcome.target}: {outcome.error}")
            else:
                _out(f"{outcome.target}: {verb} {', '.join(outcome.written) or '(nothing)'}")
        if held_back:
            _out(f"held (no auto_apply): {', '.join(held_back)}")

    took = (datetime.now(UTC) - started).total_seconds()
    _out(
        f"run: {len(result.rankings)} ranked, {len(result.decisions)} decided, "
        f"{len(shipping)} shipped, {took:.1f}s"
    )
    # A failed source is worth an exit code -- a timer that never fails is a
    # timer nobody checks -- but the run itself did its job with what it had.
    return EXIT_ERROR if (pull_failures or apply_failures) else EXIT_OK


def cmd_diff(args: argparse.Namespace) -> int:
    cfg = _config(args)
    profiles = _profiles(cfg, args.profile)
    result = run(cfg, profiles=profiles, dry_run=True)

    for name, target_cfg in cfg.targets.items():
        try:
            target = plugins.load(plugins.TARGETS, target_cfg.kind)
        except (LookupError, ImportError) as exc:
            _out(f"{name}: {exc}")
            continue
        _out(f"target {name} ({target_cfg.kind})")
        # what *this* target would write, in the vocabulary it reads back:
        # 9router holds the gateway's local ids, so comparing canonical ones
        # against them would report a change on every run.
        computed = target.plan(target_cfg, result.chains)
        try:
            current = target.current(target_cfg)
        except NotImplementedError as exc:
            # a one-way target. Printing an empty diff would read as
            # "everything changed", which is not what it said.
            _out(f"  ? cannot be read back: {exc}")
            continue
        except Exception as exc:
            _out(f"  ! {type(exc).__name__}: {exc}")
            continue
        for profile_name, chain in sorted(computed.items()):
            was = current.get(profile_name, [])
            if was == chain:
                _out(f"  = {profile_name}: unchanged")
            else:
                _out(f"  - {profile_name}: {', '.join(was) or '(nothing)'}")
                _out(f"  + {profile_name}: {', '.join(chain)}")
    return EXIT_OK


def cmd_apply(args: argparse.Namespace) -> int:
    cfg = _config(args)
    dry_run = bool(getattr(args, "dry_run", False))
    # `--yes` is the confirmation for writing outward. A dry run writes
    # nothing, so demanding confirmation for it only teaches people to type
    # --yes without reading it.
    if not args.yes and not dry_run:
        _out("apply writes to your targets; pass --yes to confirm, or --dry-run to see it")
        return EXIT_ERROR
    profiles = _profiles(cfg, args.profile)
    result = run(cfg, profiles=profiles, dry_run=dry_run, actor="cli")
    outcomes = apply_targets(cfg, result.chains, targets=args.target, dry_run=dry_run, actor="cli")
    failed = 0
    for outcome in outcomes:
        if outcome.error:
            failed += 1
            _out(f"{outcome.target}: {outcome.error}")
        else:
            verb = "would write" if dry_run else "wrote"
            _out(f"{outcome.target}: {verb} {', '.join(outcome.written) or '(nothing)'}")
    return EXIT_ERROR if failed else EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    cfg = _config(args)
    # uvicorn imports `sieve.api.app:app` in a fresh module, so --config has to
    # travel through the environment or the server would quietly read a
    # different sieve.toml than the one the command was given.
    config_path = Path(args.config)
    if config_path.exists():
        os.environ["SIEVE_CONFIG"] = str(config_path.resolve())

    uvicorn.run(
        "sieve.api.app:app",
        host=args.host or cfg.server.host,
        port=args.port or cfg.server.port,
        reload=args.reload,
    )
    return EXIT_OK


def cmd_mcp(args: argparse.Namespace) -> int:
    from sieve.mcp.server import main as mcp_main

    return mcp_main(transport=args.transport, host=args.host, port=args.port)


def cmd_export(args: argparse.Namespace) -> int:
    cfg = _config(args)
    profiles = _profiles(cfg, args.profile)
    result = run(cfg, profiles=profiles, dry_run=True)
    if args.format == "json":
        payload = {
            "computed_at": result.at.isoformat(),
            "snapshot": result.snapshot,
            "chains": [json.loads(c.model_dump_json()) for c in result.chains],
        }
        _out(json.dumps(payload, indent=2))
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["profile", "position", "model_id", "local_ids"])
        for chain in result.chains:
            for position, model_id in enumerate([chain.primary, *chain.fallbacks]):
                writer.writerow(
                    [chain.profile, position, model_id, " ".join(chain.local.get(model_id, []))]
                )
        sys.stdout.write(buffer.getvalue())
    return EXIT_OK


def cmd_export_types(args: argparse.Namespace) -> int:
    from sieve.typegen import write_types

    out = Path(args.out)
    changed = write_types(out)
    _out(f"{'wrote' if changed else 'unchanged'} {out}")
    if args.check and changed:
        _out("types.ts was stale; commit the regenerated file")
        return EXIT_ERROR
    return EXIT_OK


def cmd_migrate(args: argparse.Namespace) -> int:
    cfg = _config(args)
    store = Store(cfg.db_path)
    applied = store.migrate()
    _out(f"{cfg.db_path}: {', '.join(applied) if applied else 'already current'}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sieve", description="Weighted model selection.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="path to sieve.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pull", help="pull measurements from a source, and list inventories")
    p.add_argument("source", nargs="*", help="source or inventory names; default every enabled one")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("check", help="validate axes, profiles and aliases")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("score", help="print the ranking for a profile")
    p.add_argument("--profile", help="profile name; default every profile")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("plan", help="score every profile and decide its chain")
    p.add_argument("--profile", nargs="*")
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--store", dest="dry_run", action="store_false", help="store the result")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("diff", help="computed chains against each target")
    p.add_argument("--profile", nargs="*")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("apply", help="write chains to targets")
    p.add_argument("--profile", nargs="*")
    p.add_argument("--target", nargs="*")
    p.add_argument("--yes", action="store_true", help="required; apply writes outward")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would be written and write nothing; needs no --yes",
    )
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("run", help="the whole loop: pull, evaluate, decide, apply where auto_apply")
    p.add_argument("--profile", nargs="*")
    p.add_argument("--target", nargs="*")
    p.add_argument("--no-pull", action="store_true", help="evaluate on what is already stored")
    p.add_argument("--dry-run", action="store_true", help="decide and report, write nothing")
    p.add_argument("--actor", default="schedule", help="who this run is recorded as")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("serve", help="run the API and the web build")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="run the MCP server")
    p.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8111)
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("export", help="export chains")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--profile", nargs="*")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("export-types", help="regenerate web/src/lib/types.ts")
    p.add_argument("--out", default="web/src/lib/types.ts")
    p.add_argument("--check", action="store_true", help="fail if the file was stale")
    p.set_defaults(func=cmd_export_types)

    p = sub.add_parser("migrate", help="apply pending store migrations")
    p.set_defaults(func=cmd_migrate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result: int = args.func(args)
        return result
    except OwnerMissingError as exc:
        print(f"not built yet: {exc}", file=sys.stderr)
        return EXIT_NOT_BUILT
    except NotImplementedError as exc:
        print(f"not built yet: {exc}", file=sys.stderr)
        return EXIT_NOT_BUILT
    except ValueError as exc:
        # a bad config path or a malformed file: the person is editing YAML or
        # TOML, and deserves the sentence rather than a traceback
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
