# Security

## Supported versions

Phase 1 is pre-release. Until `v0.1.0`, only `main` is supported.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/mohamadkalhor/sieve/security/advisories/new)
rather than a public issue. You should get a first reply within a week.

Please include what you did, what happened, and what you expected. A proof of
concept helps; a working exploit is not required.

## What counts as a secret here

Sieve holds no user data and calls no model. What it does hold is worth
protecting:

- **API keys** for the sources it pulls (`ARTIFICIAL_ANALYSIS_API_KEY`) and for
  the gateways it lists (`GATEWAY_TOKEN`). These come from the environment
  only. `sieve.toml` names the variable; it never holds the value, and
  `.env.example` lists names with nothing after the `=`.
- **`SIEVE_TOKENS`**, the bearer tokens for the API and MCP. A token carries
  scopes: `read`, `profiles:write`, `apply`, `telemetry`. Only `apply` can
  write outward, and the CLI additionally requires `--yes`.
- **Your inventory** is a list of the models you can reach and where. It is not
  a credential, but it does describe your infrastructure, so `GET /v1/inventory`
  is worth putting behind `[server] read_token = true` on a shared network.

`gitleaks` runs in CI, and the recorded test fixtures are checked for keys and
auth headers before they are committed.

## Design choices that are deliberate

- Reads are open by default because the tool is usually run on `127.0.0.1`.
  Set `[server] read_token = true` to close them.
- Sieve never `eval`s anything and never imports code from data. Sources,
  inventories and targets are Python entry points, so adding one is installing
  a package, not dropping a file in a directory.
- Every remote payload is validated through `sieve/contracts.py` before it is
  stored.
- A scheduled recompute never writes to a target unless the profile says
  `auto_apply: true`.
