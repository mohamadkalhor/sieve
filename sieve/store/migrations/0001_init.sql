-- Sieve initial schema (CONTRACTS section 4).
-- Observations, decisions and telemetry are append-only; nothing is overwritten.

CREATE TABLE IF NOT EXISTS models (
    id            TEXT NOT NULL,
    modality      TEXT NOT NULL,
    name          TEXT NOT NULL,
    creator       TEXT NOT NULL,
    release_date  TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (id, modality)
);

CREATE INDEX IF NOT EXISTS models_modality ON models (modality);

-- Every id any source or inventory has used for a model.
CREATE TABLE IF NOT EXISTS aliases (
    alias    TEXT NOT NULL,
    modality TEXT NOT NULL,
    model_id TEXT NOT NULL,
    origin   TEXT NOT NULL DEFAULT 'file',
    PRIMARY KEY (alias, modality)
);

CREATE INDEX IF NOT EXISTS aliases_model ON aliases (model_id, modality);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id    TEXT NOT NULL,
    modality    TEXT NOT NULL,
    source      TEXT NOT NULL,
    field       TEXT NOT NULL,
    value       REAL NOT NULL,
    unit        TEXT NOT NULL,
    n           INTEGER,
    ci95        REAL,
    observed_at TEXT NOT NULL,
    pulled_at   TEXT NOT NULL,
    snapshot    TEXT,
    UNIQUE (model_id, source, field, observed_at)
);

CREATE INDEX IF NOT EXISTS observations_lookup
    ON observations (modality, source, field, model_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS prices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id     TEXT NOT NULL,
    source       TEXT NOT NULL,
    unit         TEXT NOT NULL,
    input        REAL,
    output       REAL,
    cached_input REAL,
    per_unit     REAL,
    source_url   TEXT,
    observed_at  TEXT NOT NULL,
    UNIQUE (model_id, source, observed_at)
);

CREATE INDEX IF NOT EXISTS prices_lookup ON prices (model_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS reachable (
    inventory  TEXT NOT NULL,
    local_id   TEXT NOT NULL,
    model_id   TEXT,
    capability TEXT NOT NULL DEFAULT '{}',
    seen_at    TEXT NOT NULL,
    PRIMARY KEY (inventory, local_id)
);

CREATE INDEX IF NOT EXISTS reachable_model ON reachable (model_id);

-- One row per engine run.
CREATE TABLE IF NOT EXISTS snapshots (
    id          TEXT PRIMARY KEY,
    at          TEXT NOT NULL,
    source_rows INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS rankings (
    snapshot TEXT NOT NULL,
    profile  TEXT NOT NULL,
    modality TEXT NOT NULL,
    at       TEXT NOT NULL,
    json     TEXT NOT NULL,
    PRIMARY KEY (snapshot, profile)
);

CREATE INDEX IF NOT EXISTS rankings_profile ON rankings (profile, at DESC);

-- The chain currently in place, one row per profile.
CREATE TABLE IF NOT EXISTS chains (
    profile         TEXT PRIMARY KEY,
    computed_at     TEXT NOT NULL,
    primary_model   TEXT NOT NULL,
    incumbent       TEXT,
    incumbent_since TEXT,
    json            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id      TEXT PRIMARY KEY,
    at      TEXT NOT NULL,
    profile TEXT NOT NULL,
    kind    TEXT NOT NULL,
    actor   TEXT NOT NULL,
    before  TEXT,
    after   TEXT,
    reason  TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS decisions_profile ON decisions (profile, at DESC);
CREATE INDEX IF NOT EXISTS decisions_kind ON decisions (kind, at DESC);

CREATE TABLE IF NOT EXISTS telemetry (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model      TEXT NOT NULL,
    profile    TEXT,
    ok         INTEGER NOT NULL,
    status     INTEGER,
    latency_ms INTEGER,
    tokens_in  INTEGER,
    tokens_out INTEGER,
    at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS telemetry_model ON telemetry (model, at DESC);

-- Bearer tokens are stored as sha256 only; the secret itself lives in the
-- environment (SIEVE_TOKENS) and is never written here.
CREATE TABLE IF NOT EXISTS tokens (
    name       TEXT PRIMARY KEY,
    scopes     TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
