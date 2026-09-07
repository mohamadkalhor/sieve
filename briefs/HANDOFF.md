# HANDOFF

Cross-owner notes. Nobody edits another owner's files: if you need a change in
one, write it here under **that owner's** heading and keep working. The
integrator routes it.

Format: one bullet per need — what you want, in which file, and why. Sign it
with your letter. Strike it through when it lands.

---

## negar-cl (integrator: contracts, cli, engine, api, mcp, store, fixtures)

Already in place, so nobody needs to build it twice:

- **`sieve/contracts.py`** holds every shared type of CONTRACTS §1 **and** the
  §2 protocols (`Source`, `Inventory`, `Target`, `HttpClient`), plus
  `SourceConfig`, `InventoryConfig`, `TargetConfig`, `PullResult`,
  `TargetResult`, `RateLimit`, `HttpResponse`, `ObsTable`, `EngineResult`.
  **B:** `sieve/sources/base.py` should *re-export* these rather than define a
  second copy.
- **`sieve/http.py`** is the one `HttpClient`: timeouts, backoff on
  408/425/429/5xx, `x-ratelimit-*` capture. `FixturePlayer` serves recorded
  payloads by URL — the filename is `fixture_slug(url)`, e.g.
  `openrouter.ai_api_v1_models.json`, and a file may be either the raw body or
  `{"status": .., "headers": {..}, "body": ..}`. `sieve.http.client()` returns
  the player whenever `SIEVE_FIXTURES` is set. **B:** drop your recordings in
  `tests/fixtures/` under those names and the player needs no code.
- **`sieve/plugins.py`** resolves the three entry-point groups lazily; a
  registered plugin whose module has not landed is reported, never raised at
  import. The four sources, two inventories and two targets are already
  declared in `pyproject.toml` — land the module at the path named there and it
  is wired.
- **`sieve/engine.py`** owns the order of PLAN §5 and calls your functions by
  the CONTRACTS §3 names. It also owns the **constraint gate**
  (`passes_constraints`: tools, reasoning, structured_output, context_min,
  input_modalities, min_axis, min_appearances) — **A:** do not duplicate it in
  `scoring/`; it needs `Capability`, which is store-side.
- **`sieve/cli.py`** wires every verb to the real path. A missing owner module
  fails as `not built yet: sieve.axes.load is owned by A ...`, exit 3.
- **API** answers 501 with the contract's JSON schema under `shape` for any
  route whose owner module is absent, so **C** and **D** can build against the
  live server today. `create_app()` builds config and store on demand, so an
  in-process ASGI call works without a lifespan.
- **`tests/fixtures/rank_case.json`** is final — see the `rules` block inside
  it. Regenerate only with `tests/fixtures/make_rank_case.py`.

Interfaces the engine expects from you (name and shape, not implementation):

| owner | module | function |
|---|---|---|
| A | `sieve.axes.load` | `load_axes(dir, modality) -> list[Axis]`, `load_all_axes(dir) -> Iterable[Axis]` |
| A | `sieve.axes.compute` | `axis_values(axis, obs: ObsTable, pool: list[str]) -> dict[str, tuple[float \| None, float]]` |
| A | `sieve.scoring.pareto` | `pareto_prune(rows, weights) -> dict[str, str]` |
| A | `sieve.scoring.weigh` | `weigh(profile, axes_by_model) -> dict[str, tuple[float, float, dict[str, float]]]` |
| A | `sieve.scoring.health` | `health(events, now) -> dict[str, float]` |
| A | `sieve.scoring.policy` | `decide(profile, incumbent, ranking, now) -> tuple[Chain, Decision \| None]` |
| A | `sieve.scoring.explain` | `explain(ranking, profile) -> str` |
| B | `sieve.profiles.load` | `load_profiles(dir) -> Iterable[Profile]` |
| B | `sieve.profiles.save` | `save_profile(dir, profile) -> None` |
| B | `sieve.profiles.validate` | `validate_profile(profile, axis_names: set[tuple[str, str]]) -> Iterable[str]` |

`axes_by_model` is `{model_id: {axis_name: (value | None, coverage)}}` — the
same shape as the `rank_case.json` rows. If a signature here does not suit
you, say so under this heading rather than changing it silently: `engine.py`
and `web/src/lib/rank/weigh.ts` both depend on it.

## A · scoring and axes

_(nothing yet)_

## B · catalog, sources, inventory, targets, profiles

_(nothing yet)_

## C · web scaffold, Field, Rankings, Sources

_(nothing yet)_

## D · web Profiles, Chains, client rank

_(nothing yet)_

## E · axes data, profiles data, docs, CI, deploy

_(nothing yet)_
