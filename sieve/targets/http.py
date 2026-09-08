"""The `http` target: nothing is pushed; the chain is served by the API.

A caller reads `GET /v1/recommend` or `GET /v1/chains/<profile>` when it needs
a model. `write()` therefore stores nothing and touches no network -- it only
records that these chains are the ones the API will serve.
"""

from __future__ import annotations

from sieve.contracts import Chain, TargetConfig, TargetResult
from sieve.targets.base import planned_ids


class HttpTarget:
    name = "http"

    def plan(self, cfg: TargetConfig, chains: list[Chain]) -> dict[str, list[str]]:
        """Canonical ids: this target writes the chain as the engine computed it."""
        return planned_ids(chains)

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]:
        """Nothing is held here: the API serves whatever the store holds, so a
        diff against this target is always empty by construction."""
        return {}

    def write(self, cfg: TargetConfig, chains: list[Chain], dry_run: bool) -> TargetResult:
        return TargetResult(
            target=cfg.name,
            written=[f"/v1/chains/{chain.profile}" for chain in chains],
            dry_run=dry_run,
            detail={
                "served_by": "GET /v1/recommend and GET /v1/chains/{profile}",
                "pushed": False,
            },
        )
