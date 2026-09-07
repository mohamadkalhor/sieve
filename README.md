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
uv sync
cp .env.example .env          # OpenRouter needs no key; add one for Artificial Analysis
cp sieve.toml.example sieve.toml

sieve pull openrouter         # ~600 models: prices, context, tools, modalities
sieve check                   # validates every axis, profile and alias file
sieve score --profile coder   # the ranking, with what carried each score
sieve plan --store            # decide a chain per profile, and keep it
sieve serve                   # API on :8110, and the web app if you built it
```

For the web app:

```bash
cd web && pnpm install && pnpm build   # sieve serve then serves it at /
```

With no key at all you still get prices, context windows and capabilities from
OpenRouter, which is enough for `cheap_bulk`, `reader` and any profile whose
constraints are about reach rather than benchmarks. Add an Artificial Analysis
key and every quality axis lights up.

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

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) has the setup, the file-ownership map and the
recipes for adding a source, an axis or a target. Security policy is in
[SECURITY.md](SECURITY.md).

## Licence

MIT.
