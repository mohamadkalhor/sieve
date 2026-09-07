# negar-cl — read first

You are building Sieve, phase 1. `PLAN.md` says what it is; `CONTRACTS.md`
says how the parts fit; `briefs/A.md … E.md` are the five agents you fan
out. This file is your order of work. Do it in order; do not skip a step
because it looks small.

## 0. Ground rules

- Independent open-source project. Nothing organisation-specific in the
  tree: no hostnames, no key values, no people's names, no references to
  "brain", "hermes" or any other product. Example configs use `localhost`.
- Secrets from the environment only. `.env.example` lists names, never
  values. If you find a secret in a commit, rewrite it out before pushing.
- Python 3.12, `uv` for env and lock, `ruff` + `mypy --strict` + `pytest`.
  Node 22, `pnpm`, SvelteKit + TypeScript + Tailwind v4, `eslint` +
  `svelte-check` + `vitest` + one Playwright smoke.
- Commit per unit of work (a module that imports and has a test, a route
  that renders). Never one large save at the end. Push `main` at the end of
  every step below.
- If you or an agent hits quota: push what compiles, write `QUOTA REACHED`
  and the last completed step in `briefs/HANDOFF.md`, report, stop.

## 1. Step 1 — foundation (you, alone; ~1 h)

1. `git clone` the repo; `uv init`, `pyproject.toml` with the entry-point
   groups `sieve.sources`, `sieve.inventories`, `sieve.targets`, the CLI
   entry `sieve = sieve.cli:main`, and the dev tools above.
2. `sieve/contracts.py` exactly as CONTRACTS §1. Add the Protocols of §2.
3. `sieve/store/db.py` + `migrations/0001_init.sql` for the tables of §4.
4. `sieve/cli.py` with every verb wired to a stub that raises
   `NotImplementedError("owned by B")` etc., so agents can run their piece
   through the real entry point from the first hour.
5. `sieve/engine.py` skeleton: `run(cfg, *, profiles, dry_run) -> EngineResult`
   calling the functions named in CONTRACTS §3 in the stated order.
6. `sieve/api/app.py` with every route of §6 returning 501 + the route's
   contract shape, `auth.py` with scoped bearer tokens from `SIEVE_TOKENS`,
   `sse.py`.
7. `sieve/mcp/server.py` listing the tools of PLAN §7, each delegating to
   the API layer.
8. `sieve export-types` producing `web/src/lib/types.ts`; commit the output.
9. `tests/fixtures/rank_case.json`: 12 models × 6 axes with hand-set values
   and the expected ranking for the weights `{a:0.4,b:0.3,c:0.3}` — both A
   and D assert against it.
10. `briefs/HANDOFF.md` with the six owner headings, empty.
11. Push. Only now fan out.

## 2. Step 2 — fan out (parallel)

Start A–E at the same time, each with its brief file **and** CONTRACTS.md
**and** the ownership table. Tell each: own only your files; save per unit
of work; write anything you need from another owner in HANDOFF.md under
their letter; stop and report when your acceptance list is green or when
you are blocked.

| agent | model | brief |
|---|---|---|
| A scoring & axes | opus | briefs/A.md |
| B catalog, sources, inventory, targets, profiles | sonnet | briefs/B.md |
| C web scaffold, Field, Rankings, Sources | opus | briefs/C.md |
| D web Profiles, Chains, client rank | opus | briefs/D.md |
| E axes data, profiles data, docs, CI, deploy | sonnet | briefs/E.md |

While they run, you own the integration seam: replace stubs in `cli.py`,
`engine.py`, `api/` as their modules land; keep `export-types` current;
route HANDOFF notes.

## 3. Step 3 — integrate

1. `sieve check` on the shipped `data/axes` and `profiles` — green.
2. `sieve pull openrouter` (no key) then `sieve pull aa_llm` and
   `sieve pull aa_media` if a key is in the environment; otherwise run the
   pulls against `tests/fixtures` with `SIEVE_FIXTURES=1`.
3. `sieve score --profile coder`, `sieve plan`, `sieve export --format json`.
4. `sieve serve` and `pnpm dev` in `web/`; open every screen on the real
   snapshot; check the browser console is clean.
5. Run all validators: `ruff`, `mypy --strict`, `pytest`, `pnpm lint`,
   `pnpm check`, `pnpm test`, Playwright smoke, `gitleaks detect`.
6. Fix what you own; send what others own back to the owning agent with the
   failing command output.

## 4. Step 4 — report

Push `main`. Complete the card (relay does it) and also
`relay say --as negar-cl` with:

- commit hash;
- PLAN §12 acceptance, one line each, `pass` / `fail` / `not built`, with the
  command that proves it;
- what each agent left unfinished, verbatim from their reports;
- open decisions you took on your own and why;
- anything in CONTRACTS.md you had to change, with the diff.

Do **not** point any target at a live gateway and do not run `sieve apply`
against anything but the `file` target. That is reviewed on the other side
first.
