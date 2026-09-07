# API

`/v1`, JSON, FastAPI. The full table is CONTRACTS.md section 6; the live
OpenAPI is at `/docs` when the server is running.

## Authentication

```bash
export SIEVE_TOKENS="ops:read,profiles:write,apply,telemetry:<secret>;agent:read,telemetry:<secret>"
```

A record is `name:scopes:secret`. Scopes are comma-separated and one of them
contains a colon (`profiles:write`), so the name is read up to the *first*
colon and the secret after the *last* — meaning a secret may not contain a
colon.

| scope | lets a caller |
|---|---|
| `read` | read, when `[server] read_token = true` closes reads |
| `profiles:write` | change a profile's weights or policy, and set aliases |
| `apply` | write chains to targets, and trigger a pull |
| `telemetry` | report outcomes |

Reads are open by default, because the server usually listens on `127.0.0.1`.
Writes always need the scope: no token is `401`, the wrong scope is `403`.

Every write is a decision row with the token's name as the actor.

## Errors

Always the same envelope:

```json
{ "error": { "code": "bad_weights", "message": "weights must sum to 1 +/- 0.001, got 0.9000" } }
```

A `501` additionally carries `shape`, the JSON schema of what the route will
return once the module behind it lands.

## The one endpoint a gateway needs

```bash
curl 'http://127.0.0.1:8110/v1/recommend?profile=coder&n=3'
```

```json
{
  "profile": "coder",
  "models": [
    { "id": "anthropic/claude-opus-5", "local_ids": ["gw/claude-opus-5"],
      "final": 0.70, "confidence": 1.0 }
  ],
  "computed_at": "2026-09-07T18:44:25Z"
}
```

`local_ids` are the ids *your gateway* serves, which is what you actually put in
a request. If nothing has been stored yet, `recommend` computes the ranking on
the spot rather than answering 404.

## Moving a weight from outside

```bash
curl -X PATCH http://127.0.0.1:8110/v1/profiles/coder/weights \
  -H 'authorization: Bearer <secret>' -H 'content-type: application/json' \
  -d '{"agentic_coding": 0.5, "cost": 0.3, "reasoning": 0.2}'
```

Weights must sum to 1 ± 0.001 or the call is `400` and nothing is written. On
success the YAML on disk changes — git stays the record — and a decision is
logged. `POST /v1/profiles/coder/evaluate` is the same computation as a dry run
that stores nothing.

## Pagination and events

List endpoints take `?limit=&cursor=` and return
`{"items": [...], "next_cursor": ...}`.

`GET /v1/events` is server-sent events: `pull`, `ranking`, `decision`, `apply`.
