# Sieve — plan

Sieve is an independent, open-source web tool that picks models for agents.
It pulls measured benchmark data from sources the user trusts, turns the
numbers into a small set of meaningful axes, scores every model against
*profiles* (one per role an agent fleet has), intersects the result with the
models the user can actually reach, and exports ranked lists and fallback
chains to wherever the user routes traffic — a file, an HTTP call, a gateway
such as 9router or LiteLLM. Agents can steer it from outside through an API
and an MCP server, so selection keeps improving without a person in the loop.

It covers language models **and** media models — image generation, image
editing, text-to-video, image-to-video, speech and music — each as its own
modality with its own axes and profiles.

Sieve is not a proxy, not a benchmark runner and not a chat UI. It never calls
a model to test it. It reads what has been measured and what the user's own
traffic has observed.

## 1. Vocabulary

| term | meaning |
|---|---|
| **modality** | `llm`, `text-to-image`, `image-editing`, `text-to-video`, `image-to-video`, `text-to-speech`, `speech-to-text`, `music`. More can be added by data. |
| **source** | a connector that pulls measurements. Each pull yields *observations*. Sources are plugins; users enable the ones they trust. |
| **observation** | one measured value: `(model, field, value, unit, n, ci, observed_at, source)`. Append-only; nothing is overwritten. |
| **catalog** | the canonical model registry: one record per model per modality, with aliases so `oc-go/glm-5.3`, `z-ai/glm-5.3` and AA's `glm-5-3` are the same model. |
| **inventory** | which models the user can reach, and where. Comes from an OpenAI-compatible `/v1/models`, a static list, or a provider account. |
| **axis** | a composite score built from source fields by a YAML definition. Axes are the vocabulary profiles speak; sources are hidden behind them. |
| **profile** | a role: weights over axes, hard constraints, a token/usage *shape* that turns prices into cost per task, and a *policy* for when the top pick may change. |
| **ranking** | the scored list for one profile over the eligible catalog, with per-axis contributions, confidence and health. |
| **chain** | the ranking ∩ inventory, cut to N: primary + fallbacks. What a target receives. |
| **target** | where chains go: JSON/CSV file, webhook, 9router combos, LiteLLM router config, or a caller of `GET /v1/recommend`. |
| **telemetry** | outcomes reported back by the user's gateway or agents (ok / error / rate-limited / latency / tokens). Feeds *health*. |
| **decision** | a stored record of a chain change (or a refusal to change) with the reason and the actor. |

## 2. Sources — what exists and what it costs

| source | modality | free? | how | notes |
|---|---|---|---|---|
| **Artificial Analysis** `api/v2/data/llms/models` | llm | free, key required, 1 000 req/day | REST, `x-api-key` | 644 models. Fields: `artificial_analysis_intelligence_index`, `artificial_analysis_coding_index`, `artificial_analysis_math_index`, `gpqa`, `hle`, `mmlu_pro`, `livecodebench`, `scicode`, `math_500`, `aime`, `aime_25`, `ifbench`, `lcr`, `terminalbench_hard`, `terminalbench_v2_1`, `tau2`, `tau_banking`; pricing (in/out/blended per 1M); `median_output_tokens_per_second`, `median_time_to_first_token_seconds`. The newer v4.2 components (AA-Briefcase, GDPval-AA, GDP.pdf, CritPt, AA-Omniscience, Coding Agent Index, Finance Index) are on the site but **not in the API yet** — the axis system must accept them the day they appear. |
| **Artificial Analysis** `api/v2/data/media/text-to-image` (157), `image-editing` (74), `text-to-video` (81), `image-to-video` (75), `text-to-speech` (95) | media | free, same key | REST; `?include_categories=true` | Arena Elo with `ci95`, `appearances`, `rank`, `release_date`. Video and image carry per-category Elo (`style_category`, `subject_matter_category`, `format_category` — e.g. Physics, Moving camera, Text, Nature, Anime). |
| **Artificial Analysis** `api/v2/media/{music/instrumental,music/with-vocals,speech-to-text,speech-to-speech,text-to-speech}/models/free` | media | free tier of the paid Data API | REST | Music Elo (+genres), STT word-error-rate index, TTS pricing per 1M characters. Verify the free-tier shape on first pull; treat as optional sources. |
| **OpenRouter** `api/v1/models` | llm | free, **no key** | REST | ~600 models: prices, context length, `architecture.input_modalities` / `output_modalities`, `supported_parameters` (tools, reasoning, structured outputs), top provider limits. Also republishes a subset of AA scores. The best key-free capability and price source. Measured 2026-09-08: 426 models, of which only 11 output images and 4 output audio, and **none output video** — so it cannot price the media field. |
| **fal** `fal.ai/api/models?limit=&page=` | media | free, **no key** | REST, paginated | Measured 2026-09-08: **1,493 models**, categories `text-to-image`, `image-to-image`, `text-to-video`, `image-to-video`, `video-to-video`, `text-to-audio`, `text-to-speech`, `speech-to-text`, `image-to-3d`. Carries **price**, `durationEstimate`, `machineType`, `licenseType`, `modelFamily`, `sandboxFree*` and a thumbnail. Publishes **no quality score at all** — see §2.1. |
| **Arena** (ex-LMArena) | llm, image, video, webdev, search, agent | free (HF dataset `lmarena-ai/leaderboard-dataset`, CC) | download parquet/CSV | Human-preference Elo per arena. No REST API; the dataset is updated regularly. |
| **LiveBench** | llm | free (HF datasets `livebench/*`, GitHub) | download | Contamination-free monthly questions; per-category scores (coding, math, reasoning, language, data analysis, IF). |
| **Epoch AI benchmarking hub** | llm | free, CC-BY (CSV; `pip install epochai`) | download | Independent runs of GPQA Diamond, FrontierMath, SWE-bench Verified and others, with run metadata. |
| **manual** | any | — | CSV/JSON dropped in `data/observations/` | For private benchmarks or a source with no connector yet. |

Sources without a free machine-readable feed (Vellum, Scale SEAL, most vendor
pages) are out of scope; the `manual` source covers them. Replicate was
measured and rejected: `api.replicate.com/v1/models` answers 401 without a
paid key, and behind it there is no quality measurement — only run counts.

Phase 1 ships AA (llm + media) and OpenRouter plus `manual`. Arena, LiveBench
and Epoch are phase 2 connectors behind the same interface.

### 2.1 Two rules this project exists to honour

Both were measured on 2026-09-08 against the live APIs. Neither is optional,
and neither may be quietly dropped from a later phase.

**A model generation marketplace never judges quality.** fal, Replicate and
the rest publish what a model costs and what knobs it takes; they run no
votes and no evaluations. Artificial Analysis and the public arenas are the
only sources that measure *good*. So the media modalities are assembled from
two sides and must stay that way:

```
   AA           quality  (elo, per-category elo, ci95, appearances)
   fal          price, run duration, hardware, licence, parameters
                              ↓  joined on the model, by name + alias
   a media model that can be ranked on value, not just on taste
```

A media model with a score and no price ranks on quality alone and says so.
A media model with a price and no score is listed and never ranked. Neither
is invented. fal states its prices in English prose (`pricingInfoOverride`,
e.g. "Your request will cost **$0.08** per image"), so the parser must be
conservative: parse the plain per-image, per-second and per-1M-token forms,
and leave everything it cannot read blank rather than guessing.

**An effort mode is a different model.** AA publishes one row per reasoning
effort, and 230 of its 644 rows are effort modes. Measured examples:

| model | mode | intelligence | $ / 1M in |
|---|---|---|---|
| GPT-5.6 Sol | max | 51.3 | 4.00 |
| GPT-5.6 Sol | xhigh | 49.8 | 4.00 |
| GPT-5.6 Sol | high | 48.3 | 4.00 |
| GPT-5.6 Sol | medium | 46.0 | 4.00 |
| GPT-5.6 Sol | low | 40.8 | 4.00 |
| GPT-5.6 Sol | non-reasoning | 32.9 | 4.00 |
| Claude Opus 5 | max effort | 54.1 | 5.00 |
| Claude Opus 5 | low effort | 43.8 | 5.00 |
| Gemini 3.8 Flash | high / medium / low | 47.1 / 46.8 / 41.0 | 0.75 |

The rate per token is identical across every mode of a model; what changes is
the score and the number of tokens burned to reach it. So:

- effort modes are **separate catalog entries**, never merged onto a base
  model. Collapsing them assigns max effort's score to a low effort call,
  which is the single most expensive mistake this tool can make;
- a gateway that serves `-high`, `-medium` and `-low` as separate ids must
  match each to **its own** AA row, not to the base name;
- **cost per mode is the price alone, and that is not yet enough.** The rate
  per token is identical across modes, and **no source in our set publishes a
  per-task output-token count**: the AA v2 API carries
  `median_output_tokens_per_second` and time-to-first-token and nothing about
  tokens burned per task. AA's own site does compute reasoning and answer
  tokens per task; the API does not expose them and we do not scrape the site.
  So today each mode is priced from **its own** AA pricing block against the
  profile's declared shape -- nothing is copied from a base row -- and a high
  mode's extra token burn is **unmeasured**. If those fields appear in the API,
  this becomes a one-line axis change;
- the honest fix is **measured, not guessed**: once telemetry carries
  `tokens_out` per model (phase 2 part 3), derive an observed output-token
  multiplier per model over a trailing window and use it in `cost_per_task` in
  place of the profile shape's flat `out`. That comes from the gateway's own
  traffic, which is the only place the truth exists;
- until then a rank whose cost came from a flat shape rather than measured
  tokens **says so**, the way coverage is already reported. A cost nobody can
  tell is an estimate is worse than one that admits it;
- a profile may then ask for the cheapest mode that clears a bar, and pick
  medium over max by itself.

### 2.2 A modality is claimed, never assumed

A source's own categories are its filing system, not ours, and they do not
always line up. fal files music generation and sound effects together under
`text-to-audio`: 47 models, some of which we can rank and some of which nothing
on earth measures. The category cannot tell us which is which, so the category
must not decide.

**The rule.** When a source category spans more than one of our modalities, the
modality is a *claim*. It stands only where a source that **does** separate them
agrees — in practice, where the id folds onto one the catalogue already holds in
that modality. Artificial Analysis separates music from sound effects by
publishing music as two leaderboards and never scoring sound effects, so a fal
`text-to-audio` row that reaches an AA music model becomes `music` and carries
its price. Every other row is **dropped and counted**, and the count is
reported.

Two things this rule refuses, both deliberately:

- **assigning the nearest modality.** A wrong modality is not a small error: it
  puts a model in a ranking it was never measured for, and the coverage rules
  cannot catch it because the coverage looks fine.
- **inventing a modality for the remainder.** A `sound-effects` modality would
  be a list nobody can rank, because no source scores sound effects — dead
  weight on every screen. Revisit it the day a score source exists.

`PullResult.provisional` carries the claimed ids, and the pull confirms or drops
them. It is general: any future source whose categories are coarser than ours
uses the same path.

### 2.3 Where a media price comes from, and where it does not

Artificial Analysis scores media and prices nothing. Its five arena endpoints
publish nine fields and not one of them is a price, `include_prices=true`
included. Every media price in Sieve therefore comes from a marketplace, and a
marketplace price is one vendor charging to run one model — never the price of
the model. Sources are kept separate, ranking uses the cheapest known price,
and the vendor is named wherever the number is shown.

Measured 2026-09-08 against the live catalogues, over 775 scored media models
of which 62 carried any price:

| source | key | media models | price form | models it can price |
| --- | --- | --- | --- | --- |
| fal | no | 1,498 total, 729 with a price sentence | English prose | +88 once tiers are parsed |
| DeepInfra | no | 116 | numeric, unit declared, in cents | +42, of which 30 fal cannot reach |
| Eden AI | no | 106 named | numeric | +13, later |
| AIML API | no | 691 | none published anywhere | catalogue only, never a price source |
| models.dev | no | 7,583 | numeric | LLM data; 57 image-output models |
| Together, Replicate, Segmind, Runware, Nebius, SiliconFlow, Hyperbolic | yes | — | — | 401 without an account, so never a default |

Two rules follow.

- **Several numbers in a sentence is not a reason to refuse it.** 494 of the fal
  price sentences carry more than one amount; 78 are one price restated as
  "for $1.00 you can run this model 12 times", and 416 are genuine tiers — 161
  by resolution, 32 by input versus output. Tiers are recorded in full, a
  declared default tier is used for ranking, and that choice is stated in the
  UI. Refusing what cannot be read stays the rule; refusing what can be read is
  a bug.
- **`per compute second` is not `per second of generated output`.** 109
  sentences price hardware time, which varies with the job. They are a distinct
  unit and are never compared against output-length prices in a ranking.

## 3. Axes — the vocabulary between sources and profiles

An axis is a YAML file. It names the source fields that feed it, how each is
transformed and weighted, and how missing fields are handled. Example:

```yaml
# data/axes/llm/agentic_coding.yaml
name: agentic_coding
modality: llm
label: Agentic coding
describes: end-to-end software tasks in a terminal or repository
fields:
  - {source: aa, field: terminalbench_v2_1, weight: 0.5}
  - {source: aa, field: terminalbench_hard,  weight: 0.2}
  - {source: aa, field: artificial_analysis_coding_index, weight: 0.2}
  - {source: aa, field: livecodebench, weight: 0.1}
  - {source: livebench, field: coding, weight: 0.3, phase: 2}
missing: renormalise      # drop absent fields, renormalise the remaining weights
min_coverage: 0.5         # below this share of weight measured, the axis is "unmeasured" for that model
higher_is_better: true
```

Each field is percentile-normalised within the modality's pool before
weighting, so an Elo, a 0–1 accuracy and a 0–100 index combine. An axis
reports `value` (0–1) and `coverage` (share of its weight that was measured).

Built-in LLM axes for phase 1 — chosen from what the API actually carries:

| axis | fields (AA unless noted) | situation it describes |
|---|---|---|
| `intelligence` | intelligence_index | general capability; the fallback axis for roles with no benchmark of their own |
| `agentic_coding` | terminalbench_v2_1, terminalbench_hard, coding_index, livecodebench | coding agents |
| `agentic_tools` | tau_banking, tau2 | multi-step tool use, research loops, assistants that act |
| `reasoning` | gpqa, hle, scicode | hard science, "thinking" roles |
| `math` | math_index, aime_25, math_500 | quantitative work |
| `knowledge` | mmlu_pro, hle | breadth of facts |
| `instruction` | ifbench | following a brief exactly — judges, writers, structured output |
| `long_context` | lcr (+ context window from inventory/OpenRouter as a constraint) | readers, summarisers |
| `speed` | median_output_tokens_per_second | throughput |
| `latency` | median_time_to_first_token_seconds (inverted) | interactive roles |
| `cost` | price × profile shape (inverted, log) | everyone who pays |
| `health` | telemetry (ok rate, rate-limit rate, p50 latency) | live reliability — not a source field |

Media axes: `quality` (arena Elo), one axis per published category
(`physics`, `moving_camera`, `text_rendering`, `nature`, `anime`, …, generated
from the categories the source returns), `cost` (per image / per second / per
1M characters where published), `runtime`, and `maturity` (appearances and
ci95 — a model with 200 votes and ±40 is not yet a safe primary).

Adding an axis is adding a file. Adding a field to an axis is a line. Both are
validated by `sieve check`.

## 4. Profiles

```yaml
# profiles/llm/coder.yaml
name: coder
modality: llm
purpose: agentic coding inside a repository
weights:
  agentic_coding: 0.45
  agentic_tools: 0.10
  reasoning: 0.10
  long_context: 0.10
  cost: 0.20
  latency: 0.05
require:
  tools: true             # from inventory / OpenRouter supported_parameters
  reasoning: true
  context_min: 200000
  min_axis: {agentic_coding: 0.5}   # percentile floor
shape: {in: 30000, out: 4000, cached: 0.5}
policy:
  margin: 3.0             # points (0–100) a challenger must clear
  max_tenure_days: 14     # after this, margin drops to 0 and the seat is re-contested
  min_confidence: 0.75    # weighted share of axes that must be measured
  chain: 5
  suspend_below_health: 0.75
  auto_apply: false       # a timer may recompute; only apply writes to targets
```

```yaml
# profiles/text-to-video/product_shot.yaml
name: product_shot
modality: text-to-video
purpose: short product clips with a moving camera and readable text
weights: {quality: 0.35, moving_camera: 0.25, text_rendering: 0.2, maturity: 0.1, cost: 0.1}
require: {min_appearances: 500}
shape: {seconds: 8, clips: 1}
policy: {margin: 15, max_tenure_days: 30, chain: 3, auto_apply: false}
```

Profiles are files in the repo **and** rows the API can change. A change
through the API is written back to the file (so git stays the record) and
logged as a decision with its actor.

Shipped profiles (phase 1), as examples users edit or delete:
`llm/`: `cheap_bulk`, `quick_chat`, `coder`, `researcher`, `reasoner`,
`judge`, `writer`, `reader`, `vision`.
`text-to-image/`: `general`, `photoreal`, `illustration`.
`image-editing/`: `general`. `text-to-video/`: `general`, `product_shot`.
`image-to-video/`: `general`. `text-to-speech/`: `general`.

## 5. Scoring

```
n_f(m)     = percentile of model m on field f within the modality pool (cost, latency on −log)
axis_a(m)  = Σ_f w_f · n_f(m) / Σ_f w_f  over measured fields      coverage_a(m) = Σ_measured w_f / Σ w_f
score(m,p) = Σ_a W_a · axis_a(m)                                    Σ_a W_a = 1
conf(m,p)  = Σ_a W_a · coverage_a(m)
health(m)  = 1 − err_24h(m) − ½·ratelimit_24h(m)   (1.0 with no telemetry; profile may require telemetry)
final(m,p) = score(m,p) · health(m)
```

Order of operations: constraints → Pareto pruning (a model beaten or tied on
every weighted axis by another *reachable* model is dropped and labelled
"dominated by X") → score → confidence floor → health → rank.

Decision rule per profile: the incumbent keeps #1 unless
`final(challenger) ≥ final(incumbent) + margin`, or the incumbent's tenure
exceeds `max_tenure_days`, or the incumbent's health falls below
`suspend_below_health`. Every evaluation writes a decision row — including
"held: challenger +1.4 inside margin 3.0".

Every rank carries an explanation: axis contributions, coverage per axis,
the dominating model if pruned, and the smallest single weight change that
would swap #1 and #2.

## 6. Inventory, targets and telemetry

**Inventory connectors** (phase 1): `openai_compat` — any base URL with
`/v1/models` and an optional bearer token (covers 9router, LiteLLM, vLLM,
OpenRouter, Ollama); `list` — a YAML list of model ids. Each reachable model
is matched to the catalog by normalised name plus an alias file the user can
edit (`data/aliases.yaml`); unmatched ids are listed on the Sources screen,
never silently dropped.

**Targets**: `file` (JSON + CSV under `out/`), `http` (`GET /v1/recommend`,
`GET /v1/chains/<profile>`), `webhook` (POST the chain on change),
`ninerouter` (writes the profile's chain as a combo, by API when available,
by its SQLite `combos` table otherwise — path in config), `litellm` (writes a
`router_settings.fallbacks` block). Phase 1 ships `file` and `http`; phase 2
adds the rest. A target write happens only on `sieve apply` or an API call
with the `apply` scope; the scheduled recompute never writes to a target
unless the profile says `auto_apply: true`.

**Telemetry**: `POST /v1/telemetry` accepts `{model, profile?, ok, status,
latency_ms, tokens_in, tokens_out, at}` in batches; a `ninerouter_sqlite`
reader and a `litellm` log reader are phase 2. Health is computed from the
last 24 h and 7 d per model.

## 7. Control surface — how agents steer it

REST (`/v1`), bearer tokens with scopes `read`, `profiles:write`, `apply`,
`telemetry`:

```
GET  /v1/modalities                      GET  /v1/axes?modality=llm
GET  /v1/models?modality=llm             GET  /v1/models/{id}
GET  /v1/profiles                        GET  /v1/profiles/{name}
PUT  /v1/profiles/{name}                 PATCH /v1/profiles/{name}/weights
PATCH /v1/profiles/{name}/policy         POST /v1/profiles/{name}/evaluate   (dry run, returns ranking + decision)
GET  /v1/rankings/{profile}              GET  /v1/chains/{profile}
GET  /v1/recommend?profile=coder&n=3     POST /v1/apply {profiles:[...]}
POST /v1/telemetry                       GET  /v1/decisions?profile=
GET  /v1/sources  POST /v1/sources/{name}/pull                   GET /v1/events (SSE)
```

MCP server (`sieve mcp`, stdio and streamable HTTP) exposing the same as
tools: `list_profiles`, `get_profile`, `set_weights`, `set_policy`,
`evaluate`, `recommend`, `get_ranking`, `explain`, `report_outcome`, `apply`.
An agent with `profiles:write` can move a weight and see the new list in one
call; with `telemetry` it can report what happened; with `apply` it can ship.
Every call is a decision-log row with `actor: <token name>`.

CLI: `sieve pull [source]`, `sieve check`, `sieve score --profile`, `sieve
plan`, `sieve diff`, `sieve apply --yes`, `sieve serve`, `sieve mcp`,
`sieve export --format json|csv`, `sieve export-types`.

## 8. Web app

SvelteKit + TypeScript + Tailwind v4, D3 for charts, Lenis for scroll,
Motion for FLIP. Dark by design. Fonts: Fraunces (display), Hanken Grotesk
(UI), IBM Plex Mono (data). Tokens: bg `#0B0C10`, panel `#13151B`, panel2
`#1A1D25`, rule `#242833`, ink `#ECE8E1`, muted `#7C8290`, accent `#E9A23B`
(the decision), reach `#7FA6FF` (you can use this), good `#5DBB8A`, warn
`#E0704A`, bad `#E05A5A`. Honour `prefers-reduced-motion`.

Screens — show few charts, compute many:

1. **Field** — one scatter per modality: main axis vs cost, all measured
   models faint, reachable lit, current #1s in accent. Axis pickers offer
   only the axes profiles use.
2. **Profiles** — cards per modality; editor with weight sliders that
   re-rank live (FLIP), constraint chips, shape and policy fields, "what would
   flip #1" line, Save (writes the YAML) and Evaluate (dry run).
3. **Rankings** — the list for one profile: axis contribution bars,
   confidence dots, health sparkline, reachable / not, dominated-by.
4. **Chains** — per profile: primary + fallbacks, diff against each target's
   current state, Apply with a confirmation that names the target, decision
   timeline.
5. **Sources** — enabled connectors, last pull, rows, rate-limit remaining,
   unmatched inventory ids with an "alias to…" control.
6. **Pulse** (phase 2) — telemetry: ok rate, 429s, latency per model.

Everything the web shows comes from the API; the web never reads files.

## 9. Repository

```
sieve/
├── pyproject.toml  sieve.toml.example  .env.example  LICENSE (MIT)
├── README.md  CONTRIBUTING.md  SECURITY.md  CONTRACTS.md  PLAN.md
├── briefs/                      per-agent build briefs (this build only)
├── sieve/                       Python 3.12 package
│   ├── contracts.py             pydantic v2 models — the single source of types
│   ├── catalog/  registry.py  aliases.py  match.py
│   ├── sources/  base.py  aa_llm.py  aa_media.py  openrouter.py  manual.py   (phase 2: arena.py livebench.py epoch.py)
│   ├── inventory/ base.py  openai_compat.py  static_list.py
│   ├── axes/     load.py  compute.py
│   ├── scoring/  normalize.py  pareto.py  weigh.py  confidence.py  health.py  policy.py  explain.py
│   ├── profiles/ load.py  save.py  validate.py
│   ├── engine.py                pull → catalog → axes → rankings → chains → decisions
│   ├── targets/  base.py  file.py  http.py            (phase 2: webhook.py ninerouter.py litellm.py)
│   ├── telemetry/ ingest.py  health.py
│   ├── store/    db.py  migrations/                    SQLite, append-only observations + decisions
│   ├── api/      app.py  auth.py  routes/  sse.py
│   ├── mcp/      server.py
│   └── cli.py
├── data/axes/<modality>/*.yaml   data/aliases.yaml   data/observations/  (manual drops)
├── profiles/<modality>/*.yaml
├── web/                          SvelteKit
├── tests/  fixtures/  (frozen AA + OpenRouter samples; CI never touches the network)
├── deploy/  sieve.service  sieve-pull.timer  Dockerfile  compose.yaml
└── .github/workflows/ci.yml
```

## 10. Security and open-source hygiene

- Secrets only from the environment (`ARTIFICIAL_ANALYSIS_API_KEY`,
  `SIEVE_TOKENS` as `name:scope,scope:secret;…`, inventory bearer tokens).
  `.env.example` lists names, never values. gitleaks runs in CI.
- API read-only without a token; writes need a scoped token; `apply`
  additionally needs `--yes` on the CLI.
- Every remote payload is validated through `contracts.py` before it is
  stored. No `eval`, no shell-out, no dynamic imports from data.
- Dependencies pinned (uv lock / package-lock). `ruff`, `mypy --strict`,
  `pytest`; `eslint`, `svelte-check`, `vitest`, one Playwright smoke.
- `SECURITY.md` with a disclosure contact; `CONTRIBUTING.md` with the
  file-ownership map and the "add a source / axis / target" recipes.
- Nothing organisation-specific in the tree. Example configs use
  `localhost` gateways and placeholder names.

## 11. Phases

**Phase 1 (this build)** — contracts; catalog + aliases; sources `aa_llm`,
`aa_media`, `openrouter`, `manual`; inventory `openai_compat`, `list`; axes
(all LLM axes above, media `quality`/categories/`maturity`/`cost`); scoring
end to end with explanations; profiles as files + API; decisions store;
targets `file`, `http`; API + tokens + SSE; MCP server; CLI; web screens
Field, Profiles, Rankings, Chains, Sources; tests, CI, docs skeleton, deploy
units.

**Phase 2 — the loop closes** (brief: `briefs/PHASE-2.md`) — **the two rules
of §2.1 first**: effort modes as separate catalog entries priced on their own
token counts, and the `fal` source joined to AA so media models rank on value.
Then: real recorded fixtures replacing the invented ones; the AA music,
speech-to-text and speech-to-speech free endpoints, against their measured
shapes; telemetry ingest, health and the Pulse screen; targets `webhook`,
`ninerouter`, `litellm`, and a Chains diff that reads each target's
`current()`; a scheduled pull-evaluate-decide-apply run with `auto_apply`; the
unfinished half of the web.

**Phase 3** — sources `arena`, `livebench`, `epoch`; motion polish; docs site;
`pip install sieve` + Docker image; v0.1.0 release.

## 12. Acceptance for phase 1

- ~~`sieve pull aa_llm` with a key stores ≥ 600 models~~ — **proven 2026-09-08**:
  644 models, 7,416 observations, 644 prices against the live API. A second
  pull adds observation rows, never overwrites; `sieve pull openrouter` needs
  no key.
- `sieve pull aa_media` stores five modalities with per-category Elo.
- `sieve check` validates every axis, profile and alias file and fails on an
  unknown field or a weight set that does not sum to 1 (±0.001).
- `sieve score --profile coder` prints the ranking with per-axis contribution,
  coverage, confidence, "dominated by", and the flip line.
- Hysteresis tests: +1 does not switch at margin 3; +4 does; a 15-day
  incumbent loses to +0.1; health 0.6 suspends.
- `PATCH /v1/profiles/coder/weights` with a `profiles:write` token changes
  the file on disk, logs a decision with the actor, and the next
  `GET /v1/rankings/coder` reflects it; without a token it is 401.
- `sieve mcp` answers `recommend {profile: coder, n: 3}` over stdio.
- Web: five screens on a real snapshot; sliders re-rank with FLIP; Lighthouse
  ≥ 90 performance and accessibility; no horizontal scroll at 390 px.
- CI green; gitleaks clean; no hostname, key or organisation name in the tree.
