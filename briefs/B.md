# B — catalog, sources, inventory, targets, profiles (sonnet)

You own `sieve/catalog/**`, `sieve/sources/**`, `sieve/inventory/**`,
`sieve/targets/{base,file,http}.py`, `sieve/profiles/**`,
`tests/fixtures/{aa_llm,aa_media,openrouter,gateway}*.json`,
`tests/test_sources*.py`, `tests/test_catalog*.py`. Read CONTRACTS §1, §2,
§5 first. All network goes through `HttpClient`; in tests it plays fixtures.

## Sources

1. `sources/base.py` — `Source` protocol, `SourceConfig`, `PullResult`
   (models, observations, prices, rate-limit remaining, warnings). Record
   every `X-RateLimit-*` header.
2. `sources/aa_llm.py` — `GET https://artificialanalysis.ai/api/v2/data/llms/models`,
   header `x-api-key` from `key_env`. Map: `evaluations.*` → observations
   with the field name verbatim (units: `*_index` → `index_0_100`; `gpqa`,
   `hle`, `lcr`, `tau*`, `terminalbench*`, `livecodebench`, `scicode`,
   `ifbench`, `mmlu_pro`, `math_500`, `aime*` → `fraction`);
   `median_output_tokens_per_second` → `tokens_per_s`;
   `median_time_to_first_token_seconds` → `seconds`; `pricing.*` → `Price`
   with `usd_per_1m_tokens`. Canonical id = `<model_creator.slug>/<slug>`.
   **Unknown evaluation keys are stored too** (the site adds benchmarks
   before the API documents them) with a warning listing new names.
3. `sources/aa_media.py` — the five endpoints under `/api/v2/data/media/`
   with `?include_categories=true`: `elo` → field `elo` unit `elo` with `n =
   appearances`, `ci95` parsed from `"-12/12"` to 12.0; each category →
   field `elo:<slug of category name>` (e.g. `elo:moving_camera`,
   `elo:physics`, `elo:anime`); `rank` → `count`. Modality from the endpoint.
   Then the free-tier endpoints `/api/v2/media/{music/instrumental,
   music/with-vocals, speech-to-text, text-to-speech}/models/free` behind
   `enabled` flags that default **off** — probe their shape and store what
   is numeric with a warning if unknown; do not fail the pull.
4. `sources/openrouter.py` — `GET https://openrouter.ai/api/v1/models`, no
   key. Store `Price` (usd per token ×1e6 → `usd_per_1m_tokens`, incl.
   `input_cache_read`), `Capability` from `architecture.input_modalities`,
   `output_modalities`, `context_length`, `top_provider.max_completion_tokens`,
   `supported_parameters` (`tools` ⇒ tools, `reasoning`/`include_reasoning`
   ⇒ reasoning, `structured_outputs`/`response_format` ⇒ structured_output).
   Canonical id = OpenRouter's `id` lowercased; keep `canonical_slug` as an
   alias. Republished AA numbers are **not** stored (AA direct is the source
   of those).
5. `sources/manual.py` — every `data/observations/*.csv|json` with columns
   `model_id, modality, source, field, value, unit, n, ci95, observed_at`.

## Catalog

6. `catalog/registry.py` — upsert `ModelRef`s; `catalog/aliases.py` — load
   `data/aliases.yaml` (`canonical: [alias, alias]`); `catalog/match.py` —
   `normalise(id)` (lowercase, strip vendor prefixes before `/`, drop
   non-alphanumerics, map `claude-opus-4-6` ≡ `claude-opus-4.6`, strip
   suffixes like `-20260420`, `:free`, `-preview` only as a **second** pass
   with lower confidence) and `match(local_id) -> (model_id | None,
   confidence)`. Never guess below 0.8; leave unmatched.

## Inventory

7. `inventory/openai_compat.py` — `GET {base_url}/v1/models` with optional
   bearer; each `data[].id` → `Reachable`; if the payload carries capability
   fields (some gateways do), fill `Capability`. `inventory/static_list.py`
   — YAML list.

## Targets

8. `targets/base.py`, `targets/file.py` (writes `out/<profile>.json` and
   `out/chains.csv`, atomic rename), `targets/http.py` (no-op writer; marks
   chains as served by `/v1/recommend`).

## Profiles

9. `profiles/load.py` / `save.py` / `validate.py` — YAML ⇄ `Profile`;
   weights sum to 1 ± 0.001; every axis exists for the modality; `require`
   keys known; save keeps key order and comments where possible (ruamel).

## Fixtures and tests

Record one real response per endpoint into `tests/fixtures/` **with keys
and any auth headers removed** and trimmed to ≤ 60 models each; write the
player so `HttpClient` serves them by URL. Tests: each source produces the
expected observation count and units; unknown AA keys are stored and
warned; `match()` on 30 hand-listed gateway ids reaches ≥ 27 correct and 0
wrong; profile validation rejects the three bad files you add under
`tests/bad_profiles/`.

## Done when

`sieve pull openrouter` works with no key; `sieve pull aa_llm` /
`aa_media` work with a key or with `SIEVE_FIXTURES=1`; `sieve check` passes
on `data/` and `profiles/`; your tests and `mypy --strict` are green.
