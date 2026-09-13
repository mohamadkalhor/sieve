"""The loop, per connector: read from every `read`, ship to every `write`.

This is what replaced the single gateway code path. `sieve pull` and the hourly
`sieve run` walk connectors first and the `sieve.toml` blocks second, and a
block a connector already covers is skipped rather than done twice.

Every run leaves a mark on the row -- `last_pull_at`, `last_push_at`,
`last_error` -- so "the gateway is quiet" and "the gateway has been refusing us
since Tuesday" stop looking the same on the screen.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sieve.config import Config
from sieve.connectors.base import ConnectorError
from sieve.connectors.registry import adapter_for
from sieve.contracts import Chain, Connector, Decision, TargetResult
from sieve.store import Store
from sieve.targets.ninerouter import chain_models, combo_name


@dataclass(frozen=True)
class PullCount:
    """What one connector refresh found. `matched` is what a profile can use."""

    connector: str
    found: int
    matched: int
    unmatched: int


def build_registry(cfg: Config, store: Store) -> Any:
    """The catalogue matcher: the alias file, then everything the store knows."""
    from sieve.catalog.aliases import load_aliases
    from sieve.catalog.registry import Registry

    registry = Registry(load_aliases(cfg.aliases_file))
    registry.extend(store.models())
    return registry


def refresh(store: Store, connector: Connector, registry: Any) -> PullCount:
    """List one connector and store what it serves, matched to the catalogue.

    An id that cannot be matched confidently keeps `model_id` None and is listed
    for a person to alias. It is never guessed at, because a wrong match
    silently routes traffic to a different model.
    """
    adapter = adapter_for(connector)
    try:
        found = adapter.reachable()
    except ConnectorError as exc:
        store.touch_connector(connector.id, error=str(exc))
        raise
    matched, unmatched = registry.attach(found)
    store.set_reachable(connector.name, matched + unmatched, connector_id=connector.id)
    store.touch_connector(connector.id, pulled_at=datetime.now(UTC), error=None)
    return PullCount(
        connector=connector.name,
        found=len(found),
        matched=len(matched),
        unmatched=len(unmatched),
    )


def ship(
    store: Store,
    connector: Connector,
    chains: list[Chain],
    *,
    dry_run: bool = False,
) -> TargetResult:
    """Seat every chain on one connector, one combo per profile.

    A profile whose chain holds no id this router serves is skipped by name and
    counted -- a shorter combo is a routing decision, an empty one is a hole.
    One profile failing does not stop the others: the reason is kept beside the
    profile it is about.
    """
    adapter = adapter_for(connector)
    planned: dict[str, list[str]] = {}
    skipped: dict[str, str] = {}
    for chain in sorted(chains, key=lambda c: c.profile):
        models = chain_models(chain)
        if not models:
            skipped[chain.profile] = "no reachable local id in the chain"
            continue
        planned[combo_name(chain.profile)] = models

    detail: dict[str, Any] = {
        "connector": connector.id,
        "kind": connector.kind,
        "combos": sorted(planned),
        "skipped": skipped,
        "written": not dry_run,
    }
    written = [f"{name}: {' -> '.join(models)}" for name, models in sorted(planned.items())]

    if dry_run:
        return TargetResult(target=connector.name, written=written, dry_run=True, detail=detail)

    failures: list[str] = []
    done: list[str] = []
    for name, models in sorted(planned.items()):
        outcome = adapter.put_combo(name, models)
        if outcome.ok:
            done.append(f"{name}: {' -> '.join(models)}")
        else:
            failures.append(f"{name}: {outcome.error}")

    error = "; ".join(failures) or None
    store.touch_connector(
        connector.id,
        pushed_at=None if error and not done else datetime.now(UTC),
        error=error,
    )
    detail["failed"] = failures
    return TargetResult(
        target=connector.name, written=done, dry_run=False, detail=detail, error=error
    )


def log_applied(store: Store, connector: Connector, chains: list[Chain], actor: str) -> None:
    """One decision per profile, so a seat change is in the ledger either way."""
    for chain in chains:
        store.add_decision(
            Decision(
                id=uuid.uuid4().hex[:12],
                at=datetime.now(UTC),
                profile=chain.profile,
                kind="apply",
                actor=actor,
                before=None,
                after={"primary": chain.primary, "fallbacks": chain.fallbacks},
                reason=f"applied {chain.profile} to connector {connector.name}",
                detail={"target": connector.name, "connector": connector.id},
            )
        )
