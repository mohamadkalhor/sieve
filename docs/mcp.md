# MCP

`sieve mcp` exposes the same control surface as `/v1` as MCP tools, so an agent
can read a ranking, move a weight, see what that would do, and ship it — without
a person in the loop.

## Connect it

```json
{
  "mcpServers": {
    "sieve": {
      "command": "sieve",
      "args": ["mcp"],
      "env": { "SIEVE_TOKEN": "<secret>", "SIEVE_CONFIG": "/path/to/sieve.toml" }
    }
  }
}
```

`sieve mcp --transport http --port 8111` serves streamable HTTP instead, for a
gateway rather than an editor.

## The tools

| tool | scope needed |
|---|---|
| `list_profiles`, `get_profile` | — |
| `get_ranking`, `explain`, `recommend`, `evaluate` | — |
| `set_weights`, `set_policy` | `profiles:write` |
| `report_outcome` | `telemetry` |
| `apply` | `apply` |

`explain` is `get_ranking` reduced to why the leader leads: the gap to #2, the
per-axis contributions, the confidence, and the smallest single weight change
that would flip them.

## How it is wired

Every tool calls `/v1` in-process over an ASGI transport, carrying `SIEVE_TOKEN`
as the bearer. Scopes, validation and the decision log are therefore exactly the
API's — there is no second code path to keep in step, and a tool cannot do
something the same token could not do over HTTP.

Every call is logged as a decision with `actor: <token name>`.

## A loop worth building

1. `recommend {profile: "coder", n: 3}` before dispatching work.
2. `report_outcome {events: [...]}` after each call — `ok`, `status`,
   `latency_ms`. Health is computed from the last 24 hours, and a primary whose
   health falls below the profile's threshold loses the seat at the next
   evaluation.
3. `evaluate {name: "coder"}` when you suspect the weights are wrong. It returns
   the ranking, the chain and the decision *without storing anything*.
4. `set_weights` only when you can say why in a sentence. The decision log is
   read by people.

## What it will not do

It will not call a model to test one, and `apply` only writes to targets that
are configured — in phase 1, a file and the API's own `/v1/recommend`. Pointing
a target at a live gateway is a separate, deliberate act.
