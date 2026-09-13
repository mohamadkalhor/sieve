# Sieve — contracts

Everything two agents share is written here first. Code follows this file;
this file does not follow code. Change it by pull request, and say why.

## 1. Types (`sieve/contracts.py`, pydantic v2)

```python
Modality = Literal["llm","text-to-image","image-editing","text-to-video",
                   "image-to-video","text-to-speech","speech-to-text",
                   "speech-to-speech","music"]
                   # `speech-to-speech` added in phase 2 part 2: AA publishes a
                   # free-tier voice-to-voice leaderboard and nothing could store
                   # it, because the literal had no name for it.

class ModelRef(BaseModel):
    id: str                 # canonical: "<creator>/<slug>", lowercase, e.g. "anthropic/claude-opus-5"
    modality: Modality
    name: str
    creator: str
    aliases: list[str] = [] # every id any source or inventory has used for it
    release_date: date | None = None
    effort: str | None = None   # reasoning mode this row is: non-reasoning|minimal|low|medium|high|xhigh|max
    family: str | None = None   # the id shared by every mode of one model
                            # PLAN 2.1a: a source that publishes one row per effort mode is
                            # describing a different model in each, at one price per token, so
                            # the mode has to survive into the catalogue. Read `effort` from the
                            # published name -- a family's bare slug is its top mode, and which
                            # mode that is varies by family.

class Observation(BaseModel):
    model_id: str           # ModelRef.id
    modality: Modality
    source: str             # "aa_llm", "openrouter", "manual", ...
    field: str              # source field name, verbatim, e.g. "terminalbench_v2_1", "elo:moving_camera"
    value: float
    unit: str               # "index_0_100" | "fraction" | "elo" | "usd_per_1m_tokens" | "tokens_per_s" | "seconds" | "usd_per_image" | "usd_per_second" | "usd_per_1m_chars" | "count" | "usd_per_task"
                            # "usd_per_task" is derived by the engine from a Price and a
                            # profile's Shape, not published by any source: cost is the one
                            # axis that depends on the profile, so it is injected per run as
                            # `price:per_task` and read by a cost axis like any other field.
    n: int | None = None    # appearances / sample size
    ci95: float | None = None
    observed_at: datetime   # when the source published or was pulled
    pulled_at: datetime

class Capability(BaseModel):  # from inventory + openrouter; never from a benchmark
    tools: bool | None = None
    reasoning: bool | None = None
    structured_output: bool | None = None
    context_window: int | None = None
    max_output: int | None = None
    input_modalities: list[str] = []   # "text","image","audio","video","file"
    output_modalities: list[str] = []

class Price(BaseModel):
    model_id: str
    source: str
    modality: Modality | None = None   # PLAN 2.1b: a media model is priced per
                            # endpoint -- one rate per image, another per second of
                            # video -- so a price keyed on the model alone puts the
                            # wrong number on the other row. None means "every
                            # modality this model is in", which is what a
                            # single-modality source means and what every row
                            # written before migration 0004 meant.
    modality: Modality | None = None   # which modality this rate is for; None means
                            # every modality the model is in. fal prices one model
                            # per endpoint, so a price keyed on the model alone put
                            # the per-image rate on its video row.
    tier: str | None = None # "480p", "1080p": which tier this rate is, where a
                            # source prices one model several ways. The cheapest is
                            # stored and named, so the number is true and visibly
                            # incomplete.
    unit: str               # as Observation.unit price units
    input: float | None = None
    output: float | None = None
    cached_input: float | None = None
    per_unit: float | None = None   # media: per image / per second / per 1M chars
    source_url: str | None = None
    observed_at: datetime

class Reachable(BaseModel):
    inventory: str          # connector name; the `reachable` row also carries its id
    local_id: str           # the id the gateway serves, e.g. "oc-go/glm-5.3"
    model_id: str | None    # matched canonical id; None = unmatched (shown on Sources screen)
    capability: Capability = Capability()
    seen_at: datetime

class Connector(BaseModel):     # a router, as data: added at runtime, not edited into sieve.toml
    id: str; name: str
    kind: str                   # "openai_compat" | "ninerouter"
    base_url: str
    token_env: str | None = None    # the NAME of the variable holding the token, never the token
    read: bool = True           # its ids join the inventory
    write: bool = False         # it is given the chains, one combo per profile
    poll_minutes: int = 60
    last_pull_at: datetime | None = None
    last_push_at: datetime | None = None
    last_error: str | None = None   # the last sentence it failed with; cleared by a success
    options: dict[str, Any] = {}    # kind-specific, and every key names something: admin_token_env, timeout
    created_at: datetime | None = None

class ConnectorTest(BaseModel):     # POST /v1/connectors/{id}/test; 200 even when the router is down
    ok: bool; models_count: int = 0; error: str | None = None

class ComboResult(BaseModel):       # one chain seated on one connector
    ok: bool; created: bool = False; error: str | None = None

class AxisField(BaseModel):
    source: str; field: str; weight: float = 1.0
    transform: Literal["identity","neg_log","log","invert"] = "identity"
    min_n: int | None = None
    phase: int = 1

class Axis(BaseModel):
    name: str; modality: Modality; label: str; describes: str
    fields: list[AxisField]
    missing: Literal["renormalise","penalise"] = "renormalise"
    min_coverage: float = 0.5
    higher_is_better: bool = True

class Shape(BaseModel):     # per profile; only the keys the modality uses
    in_tokens: int | None = None; out_tokens: int | None = None; cached: float | None = None
    images: int | None = None; seconds: float | None = None; chars: int | None = None

class Policy(BaseModel):
    margin: float = 3.0
    max_tenure_days: int = 14
    min_confidence: float = 0.75
    chain: int = 5
    suspend_below_health: float = 0.75
    auto_apply: bool = False
    require_telemetry: bool = False

class Profile(BaseModel):
    name: str; modality: Modality; purpose: str
    weights: dict[str, float]           # axis name -> weight; must sum to 1 ± 0.001
    require: dict[str, Any] = {}        # tools, reasoning, context_min, input_modalities, min_axis{axis:pct}, min_appearances
    shape: Shape = Shape()
    policy: Policy = Policy()
    targets: list[str] = []             # target names from config this profile ships to
    prefer_effort: str | None = None    # "best" | "cheapest_clearing" | a mode name; None leaves
                                        # every mode of a family in the ranking as its own row

class AxisScore(BaseModel):
    axis: str; value: float | None; coverage: float; contribution: float

class Rank(BaseModel):
    position: int; model_id: str; reachable: bool; local_ids: list[str]
    score: float; confidence: float; health: float; final: float
    axes: list[AxisScore]
    cost_per_task: float | None
    dominated_by: str | None = None
    excluded_by: str | None = None      # constraint name, when excluded
    flip: str | None = None             # "raise cost to 0.31 and #2 leads" — only on position 1

class Ranking(BaseModel):
    profile: str; modality: Modality; computed_at: datetime
    snapshot: str                        # id of the observation snapshot used
    ranks: list[Rank]                    # ordered; position 1..N for ranked models, 0 for excluded or
                                         # dominated ones, which stay in the list with their reason

class Chain(BaseModel):
    profile: str; computed_at: datetime
    primary: str; fallbacks: list[str]   # canonical ids
    local: dict[str, list[str]]          # canonical id -> local ids per inventory
    incumbent: str | None; incumbent_since: datetime | None

class Decision(BaseModel):
    id: str; at: datetime; profile: str
    kind: Literal["switch","hold","suspend","weights","policy","apply","pull"]
    actor: str                           # "timer" | "cli" | token name | "web:<user>"
    before: Any | None; after: Any | None
    reason: str                          # human sentence, e.g. "challenger +4.2 over margin 3.0 on agentic_coding"
    detail: dict[str, Any] = {}

class TelemetryEvent(BaseModel):
    model: str                           # local or canonical id
    profile: str | None = None
    ok: bool; status: int | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None; tokens_out: int | None = None
    at: datetime
```

`sieve export-types` renders these to `web/src/lib/types.ts` (via
`pydantic` JSON schema → `json-schema-to-typescript`). The file is generated;
CI fails if it is stale.

## 2. Plugin interfaces

```python
class Source(Protocol):
    name: str; modality: list[Modality]; needs_key: bool
    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult
    # PullResult: models, observations, prices, capabilities, provisional,
    # rate_limit, warnings.
    # `provisional: set[model_id]` is PLAN 2.2: ids whose modality is a *claim*,
    # because the source's own category spans more than one of ours. fal files
    # music and sound effects together under `text-to-audio`. The claim stands
    # only where a source that separates them already knows the id; the rest are
    # dropped and counted, never assigned to the nearest modality.
    # `capabilities: dict[model_id, Capability]` is how a catalogue that publishes
    # what a model can do (OpenRouter's context_length, supported_parameters) reaches
    # the `require:` gate. Without it only a gateway inventory could ever answer
    # `tools: true` or `context_min`, and a model nobody serves yet is unjudgeable.
class Inventory(Protocol):
    name: str
    def list(self, cfg: InventoryConfig, http: HttpClient) -> list[Reachable]
class Target(Protocol):
    name: str
    def current(self, cfg) -> dict[str, list[str]]     # profile -> chain currently in place, if readable
    def write(self, cfg, chains: list[Chain], dry_run: bool) -> TargetResult
class ConnectorAdapter(Protocol):   # one kind of router; sieve/connectors/<kind>.py
    kind: str; writes: bool
    def list_models(self) -> list[str]
    def test(self) -> ConnectorTest                    # never raises: a router that is down is an answer
    def put_combo(self, name: str, ordered_ids: list[str]) -> ComboResult
```

A connector kind is **not** an entry point: it is chosen by a row in the
database while a request is in flight, so it is a dict in
`sieve/connectors/registry.py` rather than a lookup that can fail with
`ImportError` halfway through an hourly run. Adding a kind is one file and one
line. A connector shadows an `[inventories.*]` or `[targets.*]` block of the
same name, so a gateway described in both places is read once and written once.

Registration is by entry point group `sieve.sources` / `sieve.inventories` /
`sieve.targets` in `pyproject.toml`, so a third-party package can add one.
`HttpClient` is the only way to the network: it sets timeouts, retries with
backoff on 429/5xx, records rate-limit headers, and is replaced by a fixture
player in tests.

## 3. Scoring functions (pure; `sieve/scoring/`)

```python
percentile(values: Sequence[float | None]) -> list[float | None]     # mid-rank, ignores None
axis_values(axis: Axis, obs: ObsTable, pool: list[str]) -> dict[model_id, (value|None, coverage)]
pareto_prune(rows: dict[model_id, dict[axis, float|None]], weights) -> dict[model_id, dominated_by]
weigh(profile, axes_by_model) -> dict[model_id, (score, confidence, contributions)]
health(events: list[TelemetryEvent], now) -> dict[model_id, float]     # 1.0 when no events
decide(profile, incumbent: Chain|None, ranking: Ranking, now) -> (Chain, Decision)
explain(ranking, profile) -> flip line for #1
```

Properties tests must hold: raising a weight on axis A never lowers the rank
of the model that is best on A; percentile is invariant to monotone
transforms; `decide` is deterministic for equal inputs; a `hold` decision
never changes the chain.

## 4. Storage (`sieve/store`, SQLite, WAL)

Tables: `models`, `aliases`, `observations` (append-only, unique on
`(model_id, source, field, observed_at)`), `prices`, `reachable` (carrying the
`connector_id` that served each row), `connectors` (id, name, kind, base_url,
token_env, read, write, poll_minutes, last_pull_at, last_push_at, last_error,
options — never a token), `snapshots`
(one row per engine run: id, at, source rows counted), `rankings` (JSON per
profile per snapshot), `chains` (current per profile), `decisions`
(append-only), `telemetry` (append-only, pruned after 30 days), `tokens`
(name, scopes, sha256). Migrations are numbered SQL files applied on start.

## 5. Config (`sieve.toml`)

```toml
[store]      path = "data/sieve.db"
[server]     host = "127.0.0.1"  port = 8110  cors = []
[sources.aa_llm]      enabled = true    key_env = "ARTIFICIAL_ANALYSIS_API_KEY"
[sources.aa_media]    enabled = true    key_env = "ARTIFICIAL_ANALYSIS_API_KEY"   modalities = ["text-to-image","image-editing","text-to-video","image-to-video","text-to-speech"]
[sources.openrouter]  enabled = true
[sources.manual]      enabled = true    dir = "data/observations"
[inventories.gateway] kind = "openai_compat"  base_url = "http://localhost:20128"  token_env = "GATEWAY_TOKEN"
[inventories.pinned]  kind = "list"           models = ["anthropic/claude-sonnet-5"]
[targets.out]         kind = "file"           dir = "out"
[schedule]   pull = "hourly"  evaluate = "hourly"   # deprecated, read by nothing
```

**`[schedule]` is deprecated and ignored.** The keys are still accepted so an
old `sieve.toml` loads, but nothing reads them: the cadence lives in the
`schedules` table, one row per step, and is edited through
`PUT /v1/schedules/{step}` or the status box on any page. `/v1/status` reports
the cadence of `full` from that table. A config that said `pull = "hourly"` on
a box whose timer had been overridden to 04:30 daily is the reason: a status
line that is confidently wrong is worse than no status line.

Tokens: env `SIEVE_TOKENS="ops:read,profiles:write,apply,telemetry:<secret>;agent:read,profiles:write,telemetry:<secret>"`.

## 6. API (`/v1`, JSON, FastAPI)

Every list endpoint paginates (`?limit=&cursor=`). Errors are
`{"error": {"code": "...", "message": "..."}}`. Writes require `Authorization:
Bearer <secret>` and the scope in the table; reads are open unless
`[server] read_token = true`.

| method & path | scope | returns |
|---|---|---|
| GET /v1/modalities | – | list of modality + counts |
| GET /v1/axes?modality= | – | Axis[] |
| GET /v1/models?modality=&reachable=&q= | – | ModelRef + latest observations + prices + reachable |
| GET /v1/models/{id} | – | one, with full observation history |
| GET /v1/profiles?modality= · GET /v1/profiles/{name} | – | Profile (SQLite truth; YAML seeds an empty store) |
| GET · PUT /v1/profiles/{name}/settings | – · profiles:write | list controls, bounded/locked weights, profile multipliers |
| GET · PUT /v1/profiles/{name}/models/{model_id}/status | – · profiles:write | active · pinned · removed and pin order |
| GET · PUT /v1/cost-multipliers | – · profiles:write | default multiplier per reachable local-id prefix |
| POST /v1/outcomes · GET /v1/profiles/{name}/experience | telemetry · – | append-only outcome · 30-day Laplace success score |
| POST /v1/profiles/{name}/preview | – | unsaved controlled list using partial settings |
| POST /v1/profiles · PATCH · DELETE /v1/profiles/{name} | profiles:write | create/copy · rename · guarded delete |
| POST /v1/profiles/{name}/apply · GET /v1/profiles/{name}/history | apply · – | ship controlled chain · decision history |
| PUT /v1/profiles/{name} | profiles:write | Profile (validated; stored; decision logged) |
| PATCH /v1/profiles/{name}/weights · /policy | profiles:write | Profile |
| POST /v1/profiles/{name}/evaluate | – | {ranking, chain, decision} — dry run, nothing stored |
| GET /v1/rankings/{profile} | – | Ranking (latest) |
| GET /v1/chains/{profile} | – | Chain |
| GET /v1/recommend?profile=&n=3&reachable_only=true | – | {profile, models:[{id, local_ids, final, confidence}], computed_at} |
| POST /v1/apply {profiles:[...], targets:[...]} | apply | TargetResult[] |
| POST /v1/telemetry [TelemetryEvent] | telemetry | {accepted} |
| GET /v1/decisions?profile=&kind=&since= | – | Decision[] |
| GET /v1/sources · POST /v1/sources/{name}/pull | – / apply | status; pull is async, returns job id |
| GET /v1/inventory?unmatched=true · PUT /v1/aliases | – / profiles:write | Reachable[] / alias saved |
| GET /v1/connectors · GET /v1/connectors/{id} | – | Connector[] + token_present; never a token |
| POST /v1/connectors · PUT /v1/connectors/{id} · DELETE /v1/connectors/{id} | apply | Connector |
| POST /v1/connectors/{id}/test | – | ConnectorTest — 200 with `ok:false` when the router is down |
| POST /v1/connectors/{id}/pull | – | {connector, found, matched, unmatched} — refresh its inventory now |
| GET /v1/connectors/{id}/models | – | what it was last seen serving, from the store |
| GET /v1/events | – | SSE: `pull`, `ranking`, `decision`, `apply`, `connector` |
| GET /v1/schedules · PUT /v1/schedules/{step} | – · profiles:write | cadence per step (+ `full`) with `next_fire` |
| POST /v1/runs/{step} | profiles:write | 202 {id}; 409 `run_in_flight` with `running` when one is already going |
| GET /v1/runs?limit=&step= · GET /v1/runs/{id} | – | Run[] · one run |
| GET /v1/runs/{id}/log | – | text/plain, the run's own log |

### Runs (AMS-31)

The loop is three named steps, each callable alone and each recorded:

| step | what it does |
|---|---|
| `pull_sources` | fetch every enabled benchmark source into the store |
| `harvest_connectors` | ask every connector what it serves, refresh the inventory rows, re-extract id prefixes for cost multipliers |
| `ship_profiles` | rank every profile against the current inventory, decide the lists, apply the combos of profiles with `auto_apply`, write decision rows |
| `full` | `harvest_connectors` → `pull_sources` → `ship_profiles` — what the retired `sieve-run.timer` did |

`sieve run <step>` runs one from the shell; `sieve run` alone is `full`. A run
is a `runs` row: `id, step, requested_by, started, finished, ok, summary,
error, log_path`. **One run at a time, box-wide** — the in-flight row is the
lock, so the API answers 409 and the CLI refuses rather than letting two loops
write one store.

A run executes on a background thread inside the service (measured: the loop
peaks at 106 MiB, the service sits at ~142 MiB, the unit's cap is 400 MiB).
The schedule is a thread in the same process, awake every 30 s, firing due
steps through the same path `Run now` takes. `sieve-run.timer` and its override
are retired.

A `schedules` row is `step, mode (off|hourly|daily), at_minute, at_time,
timezone, last_fired`; `timezone` defaults to the box's own, so `daily 04:30`
means the same moment the systemd timer meant. `/v1/status` carries
`runs: {running, last, last_by_step}` and `schedules`.

## 7. MCP (`sieve mcp`, stdio + streamable HTTP)

Tools mirror the API one-to-one with the same names as §PLAN 7; each tool's
input schema is the API body; each call carries the token from
`SIEVE_TOKEN` and is logged with `actor: "mcp:<token name>"`.

## 8. Web ↔ API

The web app talks only to `/v1`. `PUBLIC_SIEVE_API` is the base URL. Types
come from `web/src/lib/types.ts`. Client-side re-ranking in the profile
editor uses `web/src/lib/rank/weigh.ts`, which must produce the same numbers
as `sieve/scoring/weigh.py` on the shared fixture `tests/fixtures/rank_case.json`
— a vitest and a pytest both assert it.

## 10. Identity (gate)

Sieve has two ways of knowing who is calling, and the rest of the API cannot
tell them apart.

1. **`SIEVE_TOKENS`**, section 5, unchanged. Scripts, the MCP server and
   anything headless carry `Authorization: Bearer <secret>`. Nothing about
   this changed and nothing about it will.
2. **gate**, the sign-in service at `https://gate.mkalhor.xyz`
   (`http://127.0.0.1:8112` on the box, `gate.service`). A browser carries a
   `gate_session` cookie, set on `.mkalhor.xyz`, so it reaches Sieve without
   anything forwarding it by hand. Sieve asks gate `GET /v1/session` and maps
   the role it gets back:

   | gate role | scopes here |
   |---|---|
   | `owner` | `read`, `profiles:write`, `apply` |
   | `member` | `read`, `profiles:write`, `apply` |
   | `viewer` | `read` |

   `telemetry` is deliberately not in that table: reporting an outcome is a
   machine's job, and a machine carries a token.

Configured by two lines in `/etc/default/sieve`: `SIEVE_GATE_URL` and
`SIEVE_GATE_TOKEN`. **With `SIEVE_GATE_URL` unset the whole mechanism is
inert** — a box without gate behaves exactly as it did before.

An answer from gate is cached for 60 seconds per cookie, so that is the
longest a revoked session or a demotion can keep working here. gate being
unreachable is never treated as a yes.

Unauthenticated pages link to `https://gate.mkalhor.xyz/login?next=<url>`.
gate only redirects back to hosts on its own allow-list.

## 9. File ownership (phase 1)

| owner | files |
|---|---|
| integrator | `sieve/contracts.py`, `sieve/cli.py`, `sieve/engine.py`, `sieve/api/**`, `sieve/mcp/**`, `sieve/store/**`, `pyproject.toml`, `sieve.toml.example`, `.env.example`, `CONTRACTS.md`, `tests/fixtures/rank_case.json`, `web/src/lib/types.ts` (generated) |
| A · scoring (opus) | `sieve/axes/**`, `sieve/scoring/**`, `tests/test_axes*.py`, `tests/test_scoring*.py` |
| B · sources (sonnet) | `sieve/catalog/**`, `sieve/sources/**`, `sieve/inventory/**`, `sieve/targets/{base,file,http}.py`, `sieve/profiles/**`, `tests/fixtures/{aa_llm,aa_media,openrouter,gateway}*.json`, `tests/test_sources*.py`, `tests/test_catalog*.py` |
| C · web I (opus) | `web/` scaffold, `web/src/lib/{tokens,scroll,chart,api}/**`, `web/src/routes/(app)/{field,rankings,sources}/**`, `web/src/lib/components/{Rail,Kpi,Scatter,AxisBars,ConfDots}.svelte` |
| D · web II (opus) | `web/src/routes/(app)/{profiles,chains}/**`, `web/src/lib/rank/**`, `web/src/lib/components/{WeightSlider,Chip,ChainCard,Diff,Timeline}.svelte`, `web/tests/**` |
| E · data & docs (sonnet) | `data/axes/**`, `data/aliases.yaml`, `profiles/**`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/**`, `.github/**`, `deploy/**`, `.pre-commit-config.yaml`, `LICENSE` |

Nobody edits another owner's files. A needed change in someone else's file
is a note in `briefs/HANDOFF.md` under the owner's letter; the integrator
routes it.
