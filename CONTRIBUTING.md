# Contributing

## Setup

```bash
uv sync                       # Python 3.12, pinned by uv.lock
uv run pytest -q
uv run ruff check && uv run ruff format --check
uv run mypy                   # --strict, configured in pyproject.toml

cd web && pnpm install
pnpm check && pnpm lint && pnpm test
```

Nothing in the test suite touches the network. `SIEVE_FIXTURES=1` makes the CLI
and the API use the recorded payloads in `tests/fixtures/` too, so you can run
the whole pipeline offline:

```bash
SIEVE_FIXTURES=1 uv run sieve pull && uv run sieve score --profile coder
```

Two caveats while the bugs in the README's [Known issues](README.md#known-issues)
stand. `sieve` is not on your PATH after `uv sync` — it lives in `.venv`, so
either prefix with `uv run` as above or activate the venv. And `sieve pull`
still skips Artificial Analysis with fixtures on, because `sieve/cli.py` tests
`needs_key` before it tests `fixtures_enabled()`; until that is fixed, set any
non-empty value to get the recorded payloads, which never leave disk:

```bash
SIEVE_FIXTURES=1 ARTIFICIAL_ANALYSIS_API_KEY=fixture uv run sieve pull aa_llm
```

## The one idea worth knowing

**A missing measurement is missing, not zero.** Everything else follows from
it. An axis reports `(value, coverage)`; an unmeasured field costs coverage; a
model below the profile's `min_confidence` is set aside *by name* rather than
ranked low. If you find yourself writing `or 0` around a benchmark value, stop —
that is the bug this design exists to prevent.

## File ownership

The build was split across several people at once, and the map is still the
quickest orientation to the codebase:

| area | files |
|---|---|
| contracts and the seam | `sieve/contracts.py`, `cli.py`, `engine.py`, `api/**`, `mcp/**`, `store/**`, `http.py`, `plugins.py`, `typegen.py` |
| scoring | `sieve/axes/**`, `sieve/scoring/**` |
| sources and catalogue | `sieve/catalog/**`, `sources/**`, `inventory/**`, `targets/**`, `profiles/**` |
| web | `web/**` |
| data and docs | `data/**`, `profiles/**`, `docs/**`, `.github/**`, `deploy/**` |

`sieve/contracts.py` is the single source of types, and `CONTRACTS.md` is the
single source of truth about them. **Code follows that file; that file does not
follow code.** Change it in its own commit, and say why.

## Add a source

1. A class with `name`, `modality`, `needs_key` and
   `pull(cfg, http) -> PullResult`. Re-export the protocol from
   `sieve/sources/base.py`; do not redefine it.
2. All network through the `http` you are handed. It sets timeouts, retries with
   backoff and records rate limits, and in tests it is a fixture player.
3. Register it under `[project.entry-points."sieve.sources"]` in
   `pyproject.toml`.
4. Record a fixture: the response, trimmed to 60 models or fewer, **with every
   key and auth header removed**, saved as
   `tests/fixtures/<sieve.http.fixture_slug(url)>.json`.
5. Store a field you do not recognise anyway, and warn about it by name. A
   benchmark usually appears on a site before it appears in the docs.

## Add an axis

Add a file. `data/axes/<modality>/<name>.yaml`:

```yaml
name: agentic_coding
modality: llm
label: "Agentic coding"
describes: "end-to-end software tasks in a terminal or a repository"
fields:
  - {source: aa_llm, field: terminalbench_v2_1, weight: 0.5}
  - {source: livebench, field: coding, weight: 0.3, phase: 2}
missing: renormalise      # or `penalise`
min_coverage: 0.5
```

`phase: 2` means the field is ignored until that source publishes something —
and starts counting on its own the day it does, so an axis can name a benchmark
before the connector exists. `sieve check` validates the file; a profile that
names an axis which does not exist fails the same check.

## Add a target

`current(cfg) -> {profile: [model ids]}` so `sieve diff` can show the change,
and `write(cfg, chains, dry_run) -> TargetResult`. **A dry run must write
nothing.** Register it under `[project.entry-points."sieve.targets"]`.

Phase 1 ships `file` and `http` only. Do not point a target at a live gateway
in a test or an example; example configs use `localhost`.

## Pull request checklist

- [ ] `uv run ruff check`, `ruff format --check`, `mypy`, `pytest -q`
- [ ] `pnpm check && pnpm lint && pnpm test` if you touched `web/`
- [ ] `sieve export-types` run and `web/src/lib/types.ts` committed if you
      changed `contracts.py` — CI diffs it
- [ ] `sieve check` green if you touched `data/` or `profiles/`
- [ ] no key, hostname, person or account name anywhere in the diff
- [ ] a CONTRACTS.md change is its own commit, with the reason in the message
