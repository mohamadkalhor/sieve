# Sieve — contracts

Everything two agents share is written here first. Code follows this file;
this file does not follow code. Change it by pull request, and say why.

## 1. Types (`sieve/contracts.py`, pydantic v2)

```python
Modality = Literal["llm","text-to-image","image-editing","text-to-video",
                   "image-to-video","video-editing","text-to-speech",
                   "speech-to-text","speech-to-speech","music"]
                   # `speech-to-speech` added in phase 2 part 2: AA publishes a
                   # free-tier voice-to-voice leaderboard and nothing could store
                   # it, because the literal had no name for it.
                   # `video-editing` added in part 10: editing an existing video
                   # is its own arena, and the v2 data API does not expose it at
                   # all -- the leaderboard page does.
                   # `MODALITIES` is `get_args(Modality)`, never a second tuple.

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
    stale: bool = False     # AMS-29: a later pull of this inventory stopped listing it.
                            # **The inventory is the last pull, not the union of every pull.**
                            # A pull is the whole truth about that connector at that moment, so
                            # `set_reachable` marks everything it did not list `stale` instead of
                            # deleting it: the history survives and nothing routes through it.
                            # Every reader that answers "where can traffic go right now" --
                            # `store.reachable()`, `store.local_ids()` (chain local-id
                            # resolution), `store.reachable_for()`, GET /v1/inventory, and the
                            # cost-multiplier prefix list -- reads `stale = 0` by default, and
                            # takes `include_stale` to see the rest. Before this, a provider that
                            # went dark left 37 ids sitting in the inventory looking alive and the
                            # hourly run kept seating them in combos.

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

SHIP_MIN, SHIP_MAX, SHIP_DEFAULT = 1, 10, 4

class Profile(BaseModel):
    name: str; modality: Modality; purpose: str
    weights: dict[str, float]           # axis name -> share of the score; must sum to 1 ± 0.001
    ship: int = SHIP_DEFAULT            # how many models it ships, 1..10: the first is used,
                                        # the rest are fallbacks

class ProfileSettings(BaseModel):       # the tuned half of a profile, and all of it
    ship: int = SHIP_DEFAULT
    weights: dict[str, float] = {}

# A profile is its weights. `require`, `shape`, `policy`, `targets` and
# `prefer_effort` are gone from Profile, and `list_length`, `floor_score`,
# `price_sensitivity`, `experience_weight`, `auto_apply`, `cost_multipliers`
# and per-weight `min`/`max`/`locked` are gone from ProfileSettings. Every one
# of them changed the answer without moving a slider. For one release the write
# doors accept them, drop them, and name them in `warnings` (see section 6);
# stored rows and profile YAML written in the old shape read the same way.
#
# What replaced them: the shape of one task is a fixed internal default per
# modality (`sieve.engine.SHAPES`), used only to turn a published rate into a
# cost per task; health still multiplies the score, because a model that has
# stopped working should rank lower rather than be vetoed by a knob.

class AxisScore(BaseModel):
    axis: str; value: float | None; coverage: float; contribution: float

class Rank(BaseModel):
    position: int; model_id: str; reachable: bool; local_ids: list[str]
    score: float; confidence: float; health: float; final: float
    axes: list[AxisScore]
    cost_per_task: float | None
    flip: str | None = None             # "raise cost to 0.31 and #2 leads" — only on position 1

class Ranking(BaseModel):
    profile: str; modality: Modality; computed_at: datetime
    snapshot: str                        # id of the observation snapshot used
    ranks: list[Rank]                    # position 1..N over every *reachable* model, best first;
                                         # 0 only for a model this box cannot call. Nothing else
                                         # takes a model out of the ranking.

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
| GET /healthz | – | `{"ok": true}` and nothing else — the liveness probe the unit and the image use, outside `/v1` and outside auth |
| GET /v1/modalities | – | list of modality + counts |
| GET /v1/axes?modality= · GET /v1/axes/{name}?modality= | – | Axis[] · one Axis (404 `not_found`). Each carries `meaning`: one line saying what the axis means, for the person moving its slider — `describes` where an axis has one, a sentence built from its label where it does not |
| POST /v1/axes · PUT /v1/axes/{name} · DELETE /v1/axes/{name}?modality=&force= | profiles:write | create · replace · delete an axis. A name a shared axis already holds is refused; a delete an enabled profile still weighs is 409 `axis_in_use` naming the profiles, and `force=1` deletes it and sets those weights to 0 |
| GET /v1/models?modality=&reachable=&q= | – | ModelRef + latest observations + prices + reachable |
| GET /v1/models/{id} | – | one, with full observation history |
| GET /v1/profiles?modality= · GET /v1/profiles/{name} | – | Profile — name, modality, purpose, `weights`, `ship` (SQLite truth; YAML seeds an empty store). Reads resolve `weights` and `ship` through the settings, whatever shape the stored document was written in; writes keep the two in step |
| GET · PUT /v1/profiles/{name}/settings | – · profiles:write | `{ship, weights}` and nothing else. Weights merge per axis, so a page that knows one slider cannot wipe the others; a weight may be sent as a number or as the old `{"value": …}` object. **Removing an axis:** `{"weights": {"axis": null}}` (what a cleared form row sends) or `{"remove_axes": ["axis", ...]}` (what a script writes) drops it from the profile for real; both spellings may be combined with ordinary weight changes in one call. After merging/removal, the shared weight validator requires axes visible in GET /v1/axes?modality= for this profile, values in [0,1], and sum 1 ± 0.001; 400 `{error:{code:"bad_weights",message:...}}` names the axis or sum (settings errors use `bad_settings`). **Retired keys** — `list_length` (read once as the old spelling of `ship`), `floor_score`, `price_sensitivity`, `experience_weight`, `auto_apply`, `cost_multipliers`, and a weight's `min`/`max`/`locked` — are accepted, dropped, and named in the answer's `warnings` |
| GET · PUT /v1/cost-multipliers | – · profiles:write | default multiplier per reachable local-id prefix |
| POST /v1/outcomes · GET /v1/profiles/{name}/experience | telemetry · – | append-only outcome · 30-day Laplace success score |
| POST /v1/profiles/{name}/preview | – | `{weights?, ship?}` in; `{profile, ship, models, next, settings, computed_at, warnings}` out. `models` is the list those weights would ship — the top `ship` reachable models in score order — and `next` the ten behind it, each `{id, name, local_ids, score}`. Reweighs the stored ranking rather than rebuilding it, so it answers in milliseconds; only a profile with no stored ranking pays for a full one, and a box with every ranking slot busy answers 503 `ranking_busy` with `Retry-After` |
| POST /v1/profiles · PATCH · DELETE /v1/profiles/{name} | profiles:write | create/copy (`from` or `copy_from`, optional replacement `weights`) · rename and/or re-describe · guarded delete. POST uses the same axis/[0,1]/sum validator as settings; invalid weights return 400 `bad_weights`. Copy carries effective settings weights. **PATCH takes `name`, `purpose`, or both**: `{"purpose": "..."}` alone rewrites the description and touches nothing else (400 with neither, 404 for an unknown profile), so fixing a sentence no longer means PUTting every weight and constraint back. **DELETE is 409 `in_use` only when the profile has a chain** — it was applied, so a write connector may still hold a combo under that name; a profile that was never applied deletes cleanly, and `?force=1` deletes either way |
| POST /v1/profiles/{name}/apply · GET /v1/profiles/{name}/history | apply · – | ranks, ships, and answers `{chain, combos, models, shipped_at, results}` — `combos` names what was written on each write connector (`sieve-<profile>`), `models` the list it now holds · decision history |
| PUT /v1/profiles/{name} | profiles:write | Replace existing Profile (stored; decision logged); unknown name → 404 `not_found`, "create it with POST /v1/profiles". Same axis/[0,1]/sum validator as settings; 400 `bad_weights` names the axis or sum. **Retired keys** — `require`, `shape`, `policy`, `targets`, `prefer_effort` — are accepted, dropped, and named in the answer's `warnings`; a body carrying `policy.chain` and no `ship` is read as that many to ship |
| PATCH /v1/profiles/{name}/weights | profiles:write | Profile; replaces weight values using the same axis/[0,1]/sum validator as settings; 400 `bad_weights` names the axis or sum |
| POST /v1/profiles/{name}/evaluate | – | {ranking, chain, decision} — dry run, nothing stored |
| GET /v1/rankings/{profile} | – | Ranking (latest) |
| GET /v1/chains/{profile} | – | Chain |
| GET /v1/recommend?profile=&n=3&reachable_only=true | – | {profile, models:[{id, local_ids, final, confidence}], computed_at} — **the shipped chain, in the order it shipped**, so a caller and a router holding the combo cannot disagree. A profile that has never shipped falls back to its ranking |
| POST /v1/apply {profiles:[...], targets:[...]} | apply | TargetResult[] |
| POST /v1/telemetry [TelemetryEvent] | telemetry | {accepted} |
| GET /v1/decisions?profile=&kind=&since= | – | Decision[] |
| GET /v1/sources · POST /v1/sources/{name}/pull?force=true | – / apply | status; synchronous pull returns {job,source,added,warnings}; 404 unknown source; 409 `source_disabled` before any pull unless `force=true` |
| GET /v1/inventory?unmatched=true&include_stale=true | – | Reachable[] — the last pull per connector; `include_stale` adds retired rows |
| GET /v1/aliases | – | {alias,model_id,modality,origin}[]; origin is user or source |
| PUT /v1/aliases | profiles:write | {alias,model_id,actor}; body {alias,model_id,modality}, modality defaults to llm. 400 `unknown_model` when id is absent in that modality: {"error":{"code":"unknown_model","message":"no model 'missing/id' for modality 'llm'"}} |
| DELETE /v1/aliases/{alias}?modality=llm | profiles:write | {deleted,modality,actor}; URL-encode alias (slashes supported); 404 unknown alias/modality; undoes its inventory attachment. Source-origin aliases can be deleted too, but the next source pull may recreate them |
| DELETE /v1/cost-multipliers/{prefix} | profiles:write | {deleted,actor}; URL-encode prefix (slashes supported); 404 when unknown for caller. Removes stored default, not per-profile overrides; reachable prefixes may be recreated at 1.0 when multipliers are next read/synchronised |
| GET /v1/guide | – | text/markdown: OPERATING.md, rendered on /guide |
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
| GET /v1/status | – | one box's state: counts, last snapshot, `runs: {running, last, last_by_step}`, `schedules` |
| GET /v1/health?window=24h\|7d&reachable= | – | HealthRow[] — what this box's own telemetry says per model, and the number the ranking multiplies by |
| GET /v1/diff | – | TargetDiff[] — what every configured target holds now against what Sieve would write |
| GET /v1/leaderboard?modality=&metric= | – | Leaderboard — one modality, best first, deduplicated, with a price where one is published |
| GET /v1/sources/{name}/fields | – | the fields this source has actually written, with row counts — what an axis can be built from |
| GET · PUT /v1/config?dry_run=&prune= | – · profiles:write | the whole box as one document (profiles, axes, multipliers, connector shells — never a token) · applies it, or with `dry_run=1` returns only the diff it would apply; 400 `bad_config` |
| GET /v1/guide | – | text/markdown, `OPERATING.md` — how to drive this box, for an agent that arrived with no other context |
| GET /v1/me | – | the seat this call is answered from: user_id, email, role, slug, counts. A box with no sign-ins answers `user_id: null`, role `owner` |
| GET · POST /v1/tokens · DELETE /v1/tokens/{id} | profiles:write | script tokens for the signed-in seat: list · mint (**the secret is in that reply and nowhere else**; 403 `scope_refused` when the role cannot grant it, 409 `exists` on a repeated name) · revoke. 409 `no_identity` where nobody has signed in |

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
