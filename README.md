# Sieve

[![CI](https://github.com/mohamadkalhor/sieve/actions/workflows/ci.yml/badge.svg)](https://github.com/mohamadkalhor/sieve/actions/workflows/ci.yml)

Weighted model selection for agent fleets — language, image, video, speech and
music models.

Sieve reads what has been measured, scores every model against *profiles* — one
per role your agents play — intersects the result with the models you can
actually reach, and hands the winners to wherever you route traffic. It never
calls a model to test it, and your agents can steer it through a REST API and an
MCP server, so selection keeps improving without you in the loop.

Status: **phase 1**. See [PLAN.md](PLAN.md) for the whole design and
[CONTRACTS.md](CONTRACTS.md) for the types every part shares.

## Three minutes

```bash
uv sync                            # creates .venv; `sieve` is not on your PATH
cp sieve.toml.example sieve.toml

# Artificial Analysis is what fills the quality axes. Nothing reads a .env
# file, so export the key in your shell (.env.example lists every name):
export ARTIFICIAL_ANALYSIS_API_KEY=...

uv run sieve pull                  # every source, and which models you can reach
uv run sieve check                 # validates every axis, profile and alias file
uv run sieve score --profile coder # the ranking, with what carried each score
uv run sieve plan --store          # decide a chain per profile, and keep it
```

Pull one source at a time (`uv run sieve pull openrouter`) once you know what
you want. A bare `sieve pull` also refreshes the inventories, and `sieve plan`
seats only models an inventory says you can reach — so a run that pulled a
single source ranks fine but plans nothing.

`sieve.toml.example` points at a gateway on `localhost:20128`. With nothing
listening there, `sieve pull` reports `HTTP 404` for that one inventory and
exits non-zero while every other source still lands. Delete the
`[inventories.gateway]` block, or point it at your own, to clear it.

Build the web app before you serve, because `sieve serve` picks up
`web/build` at startup:

```bash
cd web && pnpm install && pnpm build
cd .. && uv run sieve serve        # API on :8110, web app at /
```

Every command is `uv run sieve …` because `uv sync` installs the console script
into `.venv` without putting it on your PATH. Activate the venv
(`source .venv/bin/activate`, or `.venv\Scripts\activate` on Windows) if you
would rather type `sieve` on its own.

OpenRouter needs no key and fills the catalogue — prices, context windows and
capabilities for every model it serves. It publishes no quality measurements,
though, so an Artificial Analysis key is currently what makes a ranking appear;
without one `sieve score` reports no models. Ranking on reach and price alone is
a known gap, tracked below in [Known issues](#known-issues).

## Where the numbers come from

| source | modality | free? | needs a key |
|---|---|---|---|
| **Artificial Analysis** (llm) | llm | yes, 1 000 req/day | yes |
| **Artificial Analysis** (media) | image, video, speech | yes, same key | yes |
| **OpenRouter** | llm | yes | **no** |
| **manual** | any | — | no |
| Arena, LiveBench, Epoch AI | llm | yes | phase 2 |

A source is a plugin. `manual` reads CSV or JSON you drop in
`data/observations/`, so a private benchmark weighs exactly like a public one.

Nothing is ever overwritten: observations are append-only, and a second pull
adds rows rather than replacing them.

## The idea

**Axes** are the vocabulary. A profile never names a benchmark; it says
`agentic_coding: 0.45`, and `data/axes/llm/agentic_coding.yaml` decides what
that means from whatever the sources measured. Adding an axis is adding a file.

**Profiles** are roles. Weights over axes, hard constraints, a token shape that
turns prices into cost per task, and a policy for when the top pick may change:

```yaml
name: coder
modality: llm
weights: { agentic_coding: 0.45, cost: 0.2, agentic_tools: 0.1, reasoning: 0.1,
           long_context: 0.1, latency: 0.05 }
require: { tools: true, reasoning: true, context_min: 200000 }
shape:   { in: 30000, out: 4000, cached: 0.5 }
policy:  { margin: 3.0, max_tenure_days: 14, chain: 5 }
```

**Coverage, not silence.** A benchmark nobody ran for a model is *unmeasured*,
never zero. The loss shows up as confidence, and a model under the profile's
`min_confidence` is set aside by name rather than quietly ranked low.

**Hysteresis.** A challenger has to clear `margin` points to take a seat, an
incumbent past `max_tenure_days` re-earns it with no margin, and one whose
health has fallen loses it at once. Every evaluation writes a decision row —
including `hold: challenger x +1.4 inside margin 3.0`, which is the sentence
that stops you asking why the list did not move.

## Connect your gateway

Sieve only recommends models you can actually call. Point it at anything that
speaks `/v1/models`:

```toml
[inventories.gateway]
kind      = "openai_compat"
base_url  = "http://localhost:20128"
token_env = "GATEWAY_TOKEN"
```

Every id it serves is matched to the catalogue — `oc-go/glm-5.3`, `GPT-5.2` and
`claude-opus-5-20260420` all land on the right model. Anything Sieve will not
guess at stays unmatched and is listed on the Sources screen for you to alias in
`data/aliases.yaml`. It is never guessed at, because a wrong match silently
routes traffic to a different model.

## Connectors

That TOML block still works, and on the first start after connectors landed it
becomes one — so a machine configured this way migrates itself and keeps
running. From then on a router is a **row**, not a file: you add one by URL over
the API, test it, and switch it on for reading (its ids join the inventory),
for writing (it is given a combo per profile), or for both.

A connector never holds a token. It holds `token_env`, the *name* of the
environment variable you set, exactly as a source names `key_env`.

```bash
curl -sX POST http://127.0.0.1:8110/v1/connectors \
  -H "authorization: Bearer $SIEVE_TOKEN" -H "content-type: application/json" -d '{
    "name": "spare",
    "kind": "ninerouter",
    "base_url": "http://127.0.0.1:20128",
    "token_env": "SPARE_GATEWAY_TOKEN",
    "read": true,
    "write": true,
    "poll_minutes": 60,
    "options": {"admin_token_env": "SPARE_NINEROUTER_TOKEN"}
  }'
```

```json
{
  "id": "9f2a1c7b40de", "name": "spare", "kind": "ninerouter",
  "base_url": "http://127.0.0.1:20128", "token_env": "SPARE_GATEWAY_TOKEN",
  "read": true, "write": true, "poll_minutes": 60,
  "last_pull_at": null, "last_push_at": null, "last_error": null,
  "options": {"admin_token_env": "SPARE_NINEROUTER_TOKEN"},
  "token_present": true, "admin_token_present": true
}
```

Then ask it whether it works. `POST /v1/connectors/{id}/test` is one of the two
calls that leave the box — every `GET` is answered from the store, because a
list of routers should not be as slow as the slowest one:

```json
{"ok": true, "models_count": 162, "error": null}
```

A router that is down answers the same shape with `"ok": false` and a sentence
saying which environment variable it refused, never the value of one. The rest
of the surface is `PUT /v1/connectors/{id}`, `DELETE /v1/connectors/{id}`,
`POST /v1/connectors/{id}/pull` to refresh its inventory now rather than on the
hour, and `GET /v1/connectors/{id}/models` for what it was last seen serving.

Two kinds ship: `openai_compat`, which can only be read, and `ninerouter`,
which is read the same way and written through its admin API. Adding a third is
one file in `sieve/connectors/` and one line in its registry.

## Profiles: the list you control

A profile owns one ordered model list. `GET/PUT /v1/profiles/{name}/settings`
controls its length, score floor, bounded axis weights, experience share, and
per-provider price multipliers. Models are `active`, `pinned`, or `removed`;
pins stay first in pin order and removed models never ship. Preview a partial
settings change without saving at `POST /v1/profiles/{name}/preview`, then
`POST /v1/profiles/{name}/apply` sends that exact list to every write-enabled
connector. Outcomes reported to `/v1/outcomes` provide a 30-day,
Laplace-smoothed experience axis. Profiles and their settings live in SQLite;
the shipped YAML is imported only when the store has no profiles.

## Let your agents steer it

```bash
export SIEVE_TOKENS="agent:read,profiles:write,telemetry:<secret>"
```

Scopes are `read`, `profiles:write`, `apply` and `telemetry`. Reads are open
unless you set `[server] read_token = true`; every write is logged as a decision
with the token's name as the actor.

`GET /v1/recommend?profile=coder&n=3` is the endpoint a gateway calls. The rest
of `/v1` is in [CONTRACTS.md](CONTRACTS.md) section 6.

The same surface is an MCP server, so an agent can move a weight and see the new
list in one call:

```json
{
  "mcpServers": {
    "sieve": {
      "command": "sieve",
      "args": ["mcp"],
      "env": { "SIEVE_TOKEN": "<secret>" }
    }
  }
}
```

Tools: `list_profiles`, `get_profile`, `set_weights`, `set_policy`, `evaluate`,
`recommend`, `get_ranking`, `explain`, `report_outcome`, `apply`.

## The web app

Five screens, all reading `/v1` and nothing else.

- **Field** — every measured model against what it costs you, at your shape.
- **Profiles** — weight sliders that re-rank the list as you drag, with the gap
  to #2 and whether it clears the margin.
- **Rankings** — the list for one profile: contributions, coverage, confidence,
  what was dominated and what was excluded, and the smallest weight change that
  would flip #1.
- **Chains** — what each profile ships, diffed against what was last applied.
- **Sources** — what has been pulled, and the ids nothing matched.

## Known issues

Three things in the quickstart do not yet behave the way the design intends.
They are open bugs, not settings you can work around by configuring differently.

- **Nothing ranks without an Artificial Analysis key.** The engine builds its
  candidate pool from models that carry at least one *observation*
  (`ObsTable.models()` in `sieve/contracts.py`, used at `sieve/engine.py`).
  OpenRouter contributes prices and capabilities but no observations, so a
  keyless pull leaves 428 models in the catalogue and none in the pool, and
  every profile reports `no models — pull a source first`. Ranking on reach and
  price needs the pool seeded from priced models too.
- **`SIEVE_FIXTURES=1` does not stand in for a key.** The recorded payloads in
  `tests/fixtures/` include the Artificial Analysis responses, but `sieve/cli.py`
  skips any source with `needs_key` before it consults `fixtures_enabled()`, so
  the fixture player never gets asked. The offline pipeline therefore pulls
  nothing from those sources. The check wants to also pass when fixtures are on.
- **`pnpm install` exits non-zero.** `web/pnpm-workspace.yaml` carries the
  placeholder `allowBuilds: esbuild: set this to true or false` instead of a
  boolean, so pnpm refuses the esbuild build script and fails with
  `ERR_PNPM_IGNORED_BUILDS`. `pnpm build` succeeds if you run it anyway, so
  `pnpm install; pnpm build` gets you a web app while this stands.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) has the setup, the file-ownership map and the
recipes for adding a source, an axis or a target. Security policy is in
[SECURITY.md](SECURITY.md).

## Licence

MIT.
