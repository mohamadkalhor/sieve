-- Connectors: a router is a row, not a block of sieve.toml.
--
-- Until now Sieve reached exactly one router, named twice in the config file --
-- once as `[inventories.gateway]` to read from and once as `[targets.gateway]`
-- to write to. Anybody with a second router, or without a shell on this box,
-- could not add one at all. A connector is the same two facts as data: where it
-- is, which environment variable holds its token, and two switches saying
-- whether it is read from, written to, or both.
--
-- The token itself is never here. `token_env` is the *name* of the variable,
-- exactly as a source names `key_env`, so a database that leaks leaks no key.
CREATE TABLE IF NOT EXISTS connectors (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,
    base_url     TEXT NOT NULL,
    token_env    TEXT,
    read         INTEGER NOT NULL DEFAULT 1,
    write        INTEGER NOT NULL DEFAULT 0,
    poll_minutes INTEGER NOT NULL DEFAULT 60,
    last_pull_at TEXT,
    last_push_at TEXT,
    last_error   TEXT,
    -- kind-specific settings, and only ones that name something rather than
    -- hold it: `admin_token_env`, `timeout`. Validated on the way in.
    options      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);

-- Which connector served this id. `inventory` already carries the connector's
-- name and is what the screens show; this is the stable identity, so renaming a
-- connector does not orphan its rows and "reachable via gateway A, not B" is a
-- question the store can answer.
ALTER TABLE reachable ADD COLUMN connector_id TEXT;

CREATE INDEX IF NOT EXISTS reachable_connector ON reachable (connector_id);
