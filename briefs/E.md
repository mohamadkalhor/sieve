# E — axes data, profiles data, docs, CI, deploy (sonnet)

You own `data/axes/**`, `data/aliases.yaml`, `profiles/**`, `README.md`,
`CONTRIBUTING.md`, `SECURITY.md`, `docs/**`, `.github/**`, `deploy/**`,
`.pre-commit-config.yaml`, `LICENSE`. Read PLAN §3, §4, §10 and CONTRACTS
§1 first. Your YAML must load through A's loader and B's validator; run
`sieve check` as they land.

## Axes (`data/axes/<modality>/*.yaml`)

LLM — exactly PLAN §3's table, one file each: `intelligence`,
`agentic_coding`, `agentic_tools`, `reasoning`, `math`, `knowledge`,
`instruction`, `long_context`, `speed`, `latency`, `cost`. Use the AA field
names verbatim; give `livebench` / `arena` fields `phase: 2` so they are
ignored until those sources exist. Cost and latency use `neg_log`.

Media — for each of `text-to-image`, `image-editing`, `text-to-video`,
`image-to-video`, `text-to-speech`: `quality` (field `elo`), `maturity`
(fields `appearances` with `log`, and `ci95` inverted), `cost` (per-unit
price when the source publishes one; otherwise the axis exists and reports
unmeasured), and one axis per category the source publishes, generated
from B's fixture — name them `<category_slug>` with `label` as the source
names it, `fields: [{source: aa_media, field: "elo:<slug>", weight: 1}]`.

## Profiles (`profiles/<modality>/*.yaml`)

PLAN §4's list. Each has a one-line `purpose` a newcomer understands and
weights that sum to 1. Suggested starting weights:

| profile | weights |
|---|---|
| llm/cheap_bulk | cost .5 intelligence .3 speed .2 |
| llm/quick_chat | intelligence .35 latency .25 cost .3 instruction .1 |
| llm/coder | agentic_coding .45 agentic_tools .1 reasoning .1 long_context .1 cost .2 latency .05 |
| llm/researcher | agentic_tools .35 reasoning .2 long_context .2 knowledge .15 cost .1 |
| llm/reasoner | reasoning .5 math .2 intelligence .2 cost .1 |
| llm/judge | instruction .4 reasoning .3 intelligence .3 |
| llm/writer | instruction .4 intelligence .3 knowledge .2 cost .1 |
| llm/reader | long_context .45 cost .35 speed .2 |
| llm/vision | intelligence .5 cost .3 latency .2 · require input_modalities: [image] |
| text-to-image/general | quality .7 maturity .2 cost .1 |
| text-to-image/photoreal | quality .4 photorealistic? .4 maturity .2 (use the category the source actually publishes) |
| text-to-image/illustration | quality .4 anime/illustration category .4 maturity .2 |
| image-editing/general | quality .8 maturity .2 |
| text-to-video/general | quality .7 maturity .2 cost .1 |
| text-to-video/product_shot | quality .35 moving_camera .25 text .2 maturity .1 cost .1 |
| image-to-video/general | quality .7 maturity .2 cost .1 |
| text-to-speech/general | quality .7 maturity .2 cost .1 |

`require` for llm profiles as in PLAN §4; `context_min` 200000 for coder,
researcher, reader; media profiles `min_appearances: 500`.

## Aliases (`data/aliases.yaml`)

Seed from B's fixtures: every OpenRouter id and gateway id that maps to an
AA slug where B's matcher is below 0.8 confidence. Keep it sorted; comment
the ambiguous ones.

## Docs

- `README.md`: what Sieve is in three sentences; a 3-minute quickstart
  (`uv sync`, `.env`, `sieve pull openrouter`, `sieve score --profile
  coder`, `sieve serve`, open the web); the sources table from PLAN §2 with
  the free/keyed column; "connect your gateway" (openai_compat); "let your
  agents steer it" (tokens, `/v1`, MCP with a Claude Code / Cursor config
  snippet); screenshots placeholders.
- `docs/`: `add-a-source.md`, `add-an-axis.md`, `add-a-target.md`,
  `scoring.md` (PLAN §5 expanded with a worked example), `api.md`
  (generated from the OpenAPI at build), `mcp.md`.
- `CONTRIBUTING.md`: setup, the ownership idea, PR checklist.
  `SECURITY.md`: supported versions, how to report, what is a secret here.
- `LICENSE`: MIT, year 2026, copyright holder as the repository owner's
  GitHub name.

## CI and deploy

`.github/workflows/ci.yml`: matrix python 3.12; `uv sync`, `ruff check`,
`ruff format --check`, `mypy --strict`, `pytest -q`, `sieve export-types`
+ `git diff --exit-code web/src/lib/types.ts`, `pnpm i --frozen-lockfile`,
`pnpm lint`, `pnpm check`, `pnpm test`, `pnpm build`, Playwright smoke
against `sieve serve` with `SIEVE_FIXTURES=1`, `gitleaks/gitleaks-action`.
`.pre-commit-config.yaml` with ruff, mypy, eslint, gitleaks.

`deploy/sieve.service` (uvicorn `sieve.api.app:app`, `EnvironmentFile=-/etc/default/sieve`,
`WorkingDirectory` configurable, `MemoryMax=400M`), `deploy/sieve-pull.timer`
+ `.service` (`sieve pull && sieve plan` hourly, `RandomizedDelaySec=180`,
`Persistent=true`), `deploy/Dockerfile` (multi-stage: pnpm build → uv →
slim runtime, non-root), `deploy/compose.yaml`.

## Done when

`sieve check` green on everything you wrote; README quickstart works from a
clean clone in the order written; CI green on a PR; `docker compose up`
serves the web on 8110 with `SIEVE_FIXTURES=1`.
