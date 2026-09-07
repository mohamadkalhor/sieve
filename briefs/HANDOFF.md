# HANDOFF

Cross-owner notes. Nobody edits another owner's files: if you need a change in
one, write it here under **that owner's** heading and keep working.

---

## What actually happened

The fan-out did not run. The relay host allows **two concurrent workers** and
one was already taken, so only agent A started; B, C, D and E came back
`Busy: limit is 2`. Agent A was then killed by a hard **900-second job
timeout** at 15 minutes, having pushed nothing.

Every part was therefore built by the integrator, in the owners' own files and
to the same contract, in this order: foundation, B, A, E, C and D. The
ownership table below is still the right map of the codebase, and the next
person to pick up a letter should read it as "these files are yours".

If the two VPS limits are raised, a genuine fan-out is still the right shape
for the next phase. Briefs that fit a 15-minute budget are the other half of
that: each of A–E as written is several hours of work.

## Everything below is landed

- **`sieve/contracts.py`** holds every shared type of CONTRACTS §1 **and** the
  §2 protocols, plus `SourceConfig`, `InventoryConfig`, `TargetConfig`,
  `PullResult`, `TargetResult`, `RateLimit`, `HttpResponse`, `ObsTable`,
  `EngineResult`. `sieve/sources/base.py` re-exports them rather than defining
  a second copy.
- **`sieve/http.py`** is the one `HttpClient`. `FixturePlayer` serves
  recordings by URL; the filename is `fixture_slug(url)`, e.g.
  `openrouter.ai_api_v1_models.json`, and `SIEVE_FIXTURES=1` switches the CLI
  and the API onto it.
- **`sieve/plugins.py`** resolves the three entry-point groups lazily.
- **`sieve/engine.py`** owns the order of PLAN §5 and the constraint gate
  (`passes_constraints`), because that needs `Capability`, which is store-side.
  It also derives `price:per_task` per profile so a cost axis can rank it.
- **API** answers every route of CONTRACTS §6. `create_app()` builds config and
  store on demand, so an in-process ASGI call works without a lifespan.
- **`tests/fixtures/rank_case.json`** is final. Regenerate only with
  `make_rank_case.py`; `weigh.py` and `weigh.ts` both assert against it.

## Signatures the seam depends on

| module | function |
|---|---|
| `sieve.axes.load` | `load_axes(dir, modality)`, `load_all_axes(dir)` |
| `sieve.axes.compute` | `axis_values(axis, obs, pool) -> {model: (value\|None, coverage)}` |
| `sieve.scoring.pareto` | `pareto_prune(rows, weights) -> {model: dominated_by}` |
| `sieve.scoring.weigh` | `weigh(profile, axes_by_model) -> {model: (score, confidence, contributions)}` |
| `sieve.scoring.health` | `health(events, now) -> {model: health}` |
| `sieve.scoring.policy` | `decide(profile, incumbent, ranking, now) -> (Chain\|None, Decision\|None)` |
| `sieve.scoring.explain` | `explain(ranking, profile) -> str \| None` |
| `sieve.profiles.load` | `load_profiles(dir)` |
| `sieve.profiles.save` | `save_profile(dir, profile)` |
| `sieve.profiles.validate` | `validate_profile(profile, axis_names)` |

`engine.py` and `web/src/lib/rank/weigh.ts` both depend on these shapes.

---

## A · scoring and axes

Landed and green. Left for whoever takes this on:

- `health_series()` produces the 7-day sparkline the Rankings screen has a
  column for, but no route serves it yet and no screen draws it. It needs
  telemetry to exist first, which is phase 2.
- `pareto_prune` is unit-tested but never fires on the shipped fixture data —
  no model in it is strictly dominated. Worth re-checking against a real AA
  pull, where it should prune a great deal.

## B · catalog, sources, inventory, targets, profiles

Landed and green. Left:

- **The AA fixtures are hand-built, not recorded.** This build had no
  `ARTIFICIAL_ANALYSIS_API_KEY`, so `tests/fixtures/artificialanalysis_ai_*`
  are written to the documented shape with invented numbers. Replace them with
  real recordings (keys removed, ≤60 models) as soon as a key exists; the tests
  should keep passing unchanged. `tests/fixtures/README.md` says which file is
  which.
- `aa_llm` stamps `observed_at` with the pull time, because the fixture
  publishes no date. Against the real API, prefer whatever date AA publishes,
  or every hourly pull adds a full set of rows rather than being deduped.
- The AA free-tier music and speech-to-text endpoints are implemented behind
  `enabled` flags that default off, and have never been called. Their shape is
  a guess from the documentation.

## C · web scaffold, Field, Rankings, Sources

Landed and green. Left:

- **Lenis is not wired.** The brief asks for it; the app uses native scroll.
  It was dropped rather than left as an unused dependency.
- The Field scatter draws every point on each redraw. That is comfortably fast
  at the ~60 points the fixtures produce and should still be fine at 644, but
  the "draw once, redraw the hover only" split the brief describes is not
  implemented — only the hit index is.
- The virtualised list above 200 rows is not built; no profile currently ranks
  that many.
- **Lighthouse has not been run.** No Chrome with Lighthouse on this machine.

## D · web Profiles, Chains, client rank

Landed and green. `weigh.ts` matches `weigh.py` on the shared fixture to 1e-6,
FLIP animates the live list and collapses under reduce-motion. Left:

- The "New profile" clone-from-existing control on the Profiles index is not
  built.
- The Chains diff compares against the last `apply` decision rather than
  calling each target's `current()`. For the `file` target those agree; for a
  target that can drift underneath you they would not.
- The editor cannot change constraints, shape or policy — it shows them as
  chips and edits weights only. `PATCH /v1/profiles/{name}/policy` exists and
  is untouched by any screen.

## E · axes data, profiles data, docs, CI, deploy

Landed and green. Left:

- `data/aliases.yaml` is a small hand-written seed, not the pass over B's
  fixtures the brief asks for. That pass needs real AA data to be worth doing.
- The media category axes are generated from what the fixtures publish
  (`anime`, `nature`, `text`, `physics`, `moving_camera`, `photoreal`). A real
  pull will publish more, and each wants its own file.
- `text-to-image/photoreal` weights the `nature` category, because the source
  publishes no photorealism category for that modality today. The file says so
  in a comment. Revisit when one appears.
- **CI has never run on GitHub.** The workflow is written and every step in it
  passes locally; nothing has yet proved it green on a runner.
- `docs/api.md` is written by hand rather than generated from the OpenAPI at
  build time.
