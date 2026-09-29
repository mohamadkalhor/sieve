# Operating Sieve as an agent

Sieve turns benchmark observations and gateway inventory into controlled model lists. A **profile** is one workload seat: modality, purpose, axis weights, constraints, task shape and switching policy. An **axis** maps published source fields into one 0–1 criterion. A **chain** is the selected primary model plus ordered fallbacks. A **combo** is that chain installed on a writable connector. A **connector** is a router Sieve may inventory (`read`) or configure (`write`); it stores only the id of a `[secrets.<id>]` entry (`secret`), never its secret and never the name of the environment variable holding one. A **cost multiplier** scales prices for a local-id prefix (for example, subscription traffic). Model status is **active** (rank normally), **pinned** (forced into the list in pin order), or **removed** (excluded). The **experience** axis is the 30-day Laplace-smoothed success rate `(successes+1)/(outcomes+2)` reported through outcomes.

## Complete control surface

Use `GET /v1/config` for the current versioned document. Send a complete or partial-by-object document to `PUT /v1/config?dry_run=1`; inspect every `{path,before,after}`, then send the identical body to `PUT /v1/config`. Omitted named objects remain unchanged. Add `prune=1` only when omitted profiles, axes, connectors and multipliers must be deleted. Unknown keys and invalid items reject the entire import.

Profile settings:

- `weights.<axis>.value`, `min`, `max`: each is 0–1; value must remain inside its bounds. `locked=true` tells an optimiser not to move that weight. Sieve normalises unlocked values for scoring; operational weights should sum to 1 after normalising. An axis must exist for the profile modality.
- `list_length`: 1–100; maximum number of models in the controlled list.
- `floor_score`: 0–1; active models below it do not enter the list.
- `price_sensitivity`: 0–1; 0 ignores price preference and 1 applies it fully.
- `experience_weight`: 0–1; blends observed 30-day experience into benchmark score; 0 disables it.
- `cost_multipliers`: non-negative per-profile prefix overrides.
- `auto_apply`: automatically ship a changed chain. The profile's top-level mirror must match.
- `model_status`: map canonical model id to `{status,pin_order}`. `pin_order` is a positive integer for pinned models.

Axis settings:

- `name`, `modality`, `label`, `describes` identify and explain it.
- `fields[]`: `source` and `field` must exist; `weight` combines fields; `transform` is `identity`, `neg_log`, `log`, or `invert`; optional `min_n` rejects small samples; `phase>1` permits a future field.
- `missing`: `renormalise` ignores missing fields or `penalise` counts them as zero.
- `min_coverage`: 0–1 required evidence share.
- `higher_is_better`: reverses ordering when false.

Connector settings are `name`, kind (`openai_compat` or `ninerouter`), an HTTP(S) `base_url`, optional `secret` (the id of a `[secrets.<id>]` entry in `sieve.toml`, which names the environment variable holding the key), `admin_secret` for a kind with an admin API of its own (only `ninerouter` today), booleans `read`/`write`, and `poll_minutes` (at least 1). A body naming a variable (`token_env`, `admin_token_env`, any `*_env`) is refused with a `422`; an id whose `kinds` do not include the connector's kind is treated as missing. Global cost multipliers are non-negative numbers keyed by local-id prefix.

Underlying profile policy controls are: `margin` (minimum challenger lead), `max_tenure_days`, `min_confidence` (0–1), `chain` (legacy list length), `suspend_below_health` (0–1), `auto_apply`, and `require_telemetry`. Constraints may require tools, reasoning, structured output, minimum context, input modalities, or minimum axis scores. Shape declares non-negative task usage (`in_tokens`, `out_tokens`, cached fraction, images, seconds, chars, megapixels, requests) so cost is per task.

## Daily run

1. Export and retain the document returned by `GET /v1/config`.
2. Inspect source freshness, connector inventory, outcomes and rankings; change only evidence-backed knobs.
3. Submit the edited document with `dry_run=1`. Reject surprising paths and repair validation errors.
4. Submit the same document without `dry_run`; confirm `applied` equals the reviewed diff length.
5. Re-export, evaluate affected profiles, and apply only when the resulting chain is acceptable. Restore the retained document through the same dry-run/import sequence if not.

## Worked mutations

These are fragments: begin with an export, mutate the named values, and send the resulting whole document.

Raise judge reasoning and pin a model:

```json
{"profiles":[{"name":"judge","settings":{"weights":{"reasoning":{"value":0.5,"min":0.0,"max":1.0,"locked":false}}},"model_status":{"anthropic/claude-opus-5":{"status":"pinned","pin_order":1}}}]}
```

Add an axis to coder (the source field must already exist):

```json
{"profiles":[{"name":"coder","settings":{"weights":{"code_reliability":{"value":0.2,"min":0.0,"max":1.0,"locked":false}}}}],"axes":[{"name":"code_reliability","modality":"llm","label":"Code reliability","describes":"published coding reliability","fields":[{"source":"aa_llm","field":"livecodebench","weight":1.0,"transform":"identity","phase":1}],"missing":"renormalise","min_coverage":0.5,"higher_is_better":true}]}
```

Set the `cc/` price multiplier to 0.1:

```json
{"cost_multipliers":{"cc":0.1}}
```

The guide is also served as Markdown at `GET /v1/guide` and as MCP resource `sieve://operating-guide`. With the kit on, `POST /v1/mcp` serves the same tools as MCP (each one is a `/v1` route, called with your own API key); `sieve mcp` on your own machine keeps `SIEVE_TOKEN` as its bearer.
## Agent API (/v1)

Everything below exists only when the operator sets `AGENT_V1=on`. Unset — the default — means none of it exists and `/v1/*` behaves exactly as it did before: the same routes, the same synchronous answers, the same credential rules.

### Credentials

Send either `Authorization: Bearer <key>` or the gate session cookie, never both.

- both -> 400 `mixed_credentials`
- a bad or unknown bearer -> 401 `bad_key`; there is no fallback to the cookie
- neither -> 401 `sign_in`
- a key without the route's scope -> 403 `not_allowed`

Public, no credential: `/v1/guide`, `/v1/openapi.json`, `/healthz`, `/v1/health`, `/llms.txt`.

### Errors

Every error body is `{"error": {"code": "...", "message": "...", "detail": ... (optional), "request_id": "..."}}`, and every `/v1` answer carries an `X-Request-Id` header.

### Retries and Idempotency-Key

Send `Idempotency-Key: <any unique string>`. It is required on the run routes `POST /v1/apply`, `POST /v1/runs/{step}` and `POST /v1/sources/{name}/pull`; a missing key is 400 `idempotency_key_required`. It is optional on every other write.

Same key with the same body inside the retention window -> the stored answer is replayed. Same key with a different body -> 409.

### Jobs

With `AGENT_V1` on, `POST /v1/sources/{name}/pull` answers 202 with a job object; poll `GET /v1/jobs/{job_id}` until its status is `done` or `failed`. A failed job carries its own error code: `store_busy`, `not_found` or `source_disabled`.

With `AGENT_V1` off the same route answers synchronously, as before.

### The store lock

Every bulk writer — the CLI's `pull`, `plan --store` and `run`, and the API's `apply` and `pull` — takes one lock file `<data dir>/store.lock`. A second writer waits up to `SIEVE_STORE_LOCK_WAIT` seconds (default 600) and then fails: the CLI exits 1 with a message, the API answers 503 `store_busy` with `Retry-After: 2`.

### MCP

`POST /v1/mcp` is JSON-RPC (MCP over HTTP) with the caller's own credential. 15 tools: `list_profiles`, `get_profile`, `set_weights`, `set_ship`, `evaluate`, `recommend`, `get_ranking`, `explain`, `report_outcome`, `apply`, `list_models`, `leaderboard`, `status`, `list_runs`, `export_config`. `apply` is a run tool: pass `_idempotency_key` in its arguments. `sieve mcp` (stdio, local) still works and still uses `SIEVE_TOKEN` for its own bearer.

### For operators: environment

- `AGENT_V1` — off by default; turns the whole surface above on.
- `AIO_CURSOR_KEY` — required outside `APP_ENV=dev`; without it, list cursors do not survive a restart.
- `APP_ENV` — `dev` enables `GET /v1/_crash` for the conformance suite; never set `dev` in production.
- `SIEVE_STORE_LOCK_WAIT` — seconds a bulk writer waits for the store lock (default 600).
