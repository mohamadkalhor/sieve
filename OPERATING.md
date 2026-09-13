# Operating Sieve as an agent

Sieve turns benchmark observations and gateway inventory into controlled model lists. A **profile** is one workload seat: modality, purpose, axis weights, constraints, task shape and switching policy. An **axis** maps published source fields into one 0–1 criterion. A **chain** is the selected primary model plus ordered fallbacks. A **combo** is that chain installed on a writable connector. A **connector** is a router Sieve may inventory (`read`) or configure (`write`); it stores only the environment-variable name (`token_env`), never its secret. A **cost multiplier** scales prices for a local-id prefix (for example, subscription traffic). Model status is **active** (rank normally), **pinned** (forced into the list in pin order), or **removed** (excluded). The **experience** axis is the 30-day Laplace-smoothed success rate `(successes+1)/(outcomes+2)` reported through outcomes.

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

Connector settings are `name`, kind (`openai_compat` or `ninerouter`), an HTTP(S) `base_url`, optional uppercase `token_env`, booleans `read`/`write`, and `poll_minutes` (at least 1). Global cost multipliers are non-negative numbers keyed by local-id prefix.

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

The guide is also served as Markdown at `GET /v1/guide` and as MCP resource `sieve://operating-guide`.