# A — scoring and axes (opus)

You own `sieve/axes/**`, `sieve/scoring/**`, `tests/test_axes*.py`,
`tests/test_scoring*.py`. Read CONTRACTS §1 and §3 first. Everything here
is pure: no I/O, no network, no SQLite. Inputs are pydantic models and plain
dicts; outputs are pydantic models.

## Build

1. `axes/load.py` — load every `data/axes/<modality>/*.yaml` into `Axis`;
   validate: weights > 0, known transforms, `min_coverage` in (0,1].
2. `axes/compute.py` — `axis_values(axis, obs_table, pool)`: for each field
   take the latest observation per model (respect `min_n`), apply the
   transform, percentile-normalise within `pool`, weight, and return
   `(value, coverage)` per model. `missing: renormalise` divides by the
   measured weight; `penalise` treats a missing field as the pool's 20th
   percentile and still reduces coverage. Below `min_coverage` the value is
   `None`.
3. `scoring/normalize.py` — `percentile()` mid-rank, `None`-safe, with
   `neg_log` / `log` / `invert` transforms.
4. `scoring/pareto.py` — prune among *reachable* models only; ties do not
   dominate unless strictly better on at least one weighted axis; return
   `{model_id: dominated_by}`.
5. `scoring/weigh.py` — `weigh(profile, axes_by_model)`: score, confidence,
   per-axis contributions; models with confidence below
   `policy.min_confidence` are ranked last with `excluded_by =
   "min_confidence"`.
6. `scoring/health.py` — `health(events, now)` per CONTRACTS §3; 24 h window
   for the number, 7 d kept for the sparkline series the API serves.
7. `scoring/policy.py` — `decide(profile, incumbent, ranking, now)`:
   `switch` / `hold` / `suspend` with the reason sentence formats:
   - `switch: <new> over <old> by +4.2, margin 3.0 (agentic_coding +0.31)`
   - `hold: challenger <id> +1.4 inside margin 3.0`
   - `switch: tenure 15 d > 14 d, margin waived, <new> leads by +0.1`
   - `suspend: <old> health 0.61 < 0.75, <new> takes primary`
8. `scoring/explain.py` — the flip line for #1: the smallest change of one
   weight (renormalising the rest) that makes #2 lead; format
   `raise cost to 0.31 and <id> leads`.
9. Cost per task: `cost_per_task(price, shape)` — llm: `in·price_in +
   out·price_out` with `cached` share at `cached_input` price when present;
   media: `images·per_unit` / `seconds·per_unit` / `chars/1e6·per_unit`.

## Tests (property + example)

- monotonicity: raising an axis weight never lowers the rank of the model
  best on that axis;
- percentile invariant under monotone transforms; `None` never changes
  other models' percentiles;
- pareto: a model equal on all axes is not dominated; strictly worse on one
  and equal elsewhere is;
- hysteresis: +1 holds at margin 3; +4 switches; tenure 15 d switches on
  +0.1; health 0.6 suspends; `hold` returns the incumbent chain unchanged;
- `tests/fixtures/rank_case.json` (owned by the integrator) reproduces the
  expected order exactly — this is the same case the web asserts;
- explain: on a two-model case the flip line is exact.

## Done when

`pytest tests/test_axes* tests/test_scoring*` green, `mypy --strict` clean
on your files, and `sieve score --profile coder` (integrator wires it)
prints contributions that sum to the score within 1e-6.
