-- Several users: everything a person makes is theirs (AMS-28).
--
-- Until now the store was one pool. Every profile, connector, axis, multiplier
-- and outcome belonged to whoever happened to be looking, so a second person
-- signing in through gate saw -- and could rewrite -- the first one's work.
--
-- `owner_id` is the local `users.id` of whoever made the row; NULL means
-- shared, which is what a builtin axis is. `visibility` says whether anybody
-- else may read it: profiles are private by default, axes shared, because a
-- shipped axis is a vocabulary and a profile is a seat.
--
-- Names are scoped to their owner, which is the whole point of item 3 of the
-- card: two people may each keep a `judge`. That cannot be expressed by the
-- old `PRIMARY KEY(name)`, so every table whose key was a bare name is rebuilt
-- with a unique index over `(IFNULL(owner_id,''), name)` instead. Every
-- `ON CONFLICT` clause in Python names that same expression.
--
-- The rows that exist today are adopted in Python (`sieve/owners.py`), not
-- here: the owner's id has to be looked up or minted from `SIEVE_OWNER_EMAIL`
-- first, and a migration that guessed it would have to be undone by hand.

PRAGMA foreign_keys=OFF;

-- The people Sieve knows. Its own table on purpose: gate's `/admin/users` is
-- not readable with the service token this box holds, and an identity the
-- store cannot resolve without a network call is an identity that disappears
-- when gate is down. `gate_id` is filled in on first sign-in; `email` is the
-- join, because that is what gate states about a session.
CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    role       TEXT NOT NULL CHECK(role IN ('owner','member','viewer')),
    slug       TEXT NOT NULL,
    gate_id    TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users (email);
CREATE UNIQUE INDEX IF NOT EXISTS users_slug ON users (slug);

-- Per-user script tokens. Same rule as everywhere else in Sieve: only the
-- sha256 is stored, never the secret, and a token authenticates as its owner.
DROP TABLE IF EXISTS tokens;

CREATE TABLE tokens (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    scopes       TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked      INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
);

CREATE UNIQUE INDEX tokens_hash ON tokens (sha256);
CREATE UNIQUE INDEX tokens_owner_name ON tokens (owner_id, name);

-- profiles -------------------------------------------------------------- --

CREATE TABLE profiles_owned (
    name       TEXT NOT NULL,
    modality   TEXT NOT NULL,
    json       TEXT NOT NULL,
    settings   TEXT NOT NULL,
    owner_id   TEXT,
    visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','shared')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO profiles_owned (name, modality, json, settings, created_at, updated_at)
SELECT name, modality, json, settings, created_at, updated_at FROM profiles;

DROP TABLE profiles;
ALTER TABLE profiles_owned RENAME TO profiles;
CREATE UNIQUE INDEX profiles_owner_name ON profiles (IFNULL(owner_id,''), name);
CREATE INDEX profiles_owner ON profiles (owner_id);

-- profile_models -------------------------------------------------------- --

CREATE TABLE profile_models_owned (
    profile   TEXT NOT NULL,
    model_id  TEXT NOT NULL,
    status    TEXT NOT NULL CHECK(status IN ('active','pinned','removed')),
    pin_order INTEGER,
    owner_id  TEXT
);

INSERT INTO profile_models_owned (profile, model_id, status, pin_order)
SELECT profile, model_id, status, pin_order FROM profile_models;

DROP TABLE profile_models;
ALTER TABLE profile_models_owned RENAME TO profile_models;
CREATE UNIQUE INDEX profile_models_key
    ON profile_models (IFNULL(owner_id,''), profile, model_id);

-- outcomes -------------------------------------------------------------- --

CREATE TABLE outcomes_owned (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    profile  TEXT NOT NULL,
    model_id TEXT NOT NULL,
    local_id TEXT NOT NULL,
    ok       INTEGER NOT NULL,
    seconds  REAL NOT NULL,
    vote     INTEGER NOT NULL CHECK(vote BETWEEN -1 AND 1),
    note     TEXT,
    at       TEXT NOT NULL,
    owner_id TEXT
);

INSERT INTO outcomes_owned (id, profile, model_id, local_id, ok, seconds, vote, note, at)
SELECT id, profile, model_id, local_id, ok, seconds, vote, note, at FROM outcomes;

DROP TABLE outcomes;
ALTER TABLE outcomes_owned RENAME TO outcomes;
CREATE INDEX outcomes_profile_at ON outcomes (IFNULL(owner_id,''), profile, at);

-- cost multiplier defaults ---------------------------------------------- --

CREATE TABLE cost_multipliers_owned (
    prefix     TEXT NOT NULL,
    multiplier REAL NOT NULL CHECK(multiplier >= 0),
    owner_id   TEXT
);

INSERT INTO cost_multipliers_owned (prefix, multiplier)
SELECT prefix, multiplier FROM cost_multipliers;

DROP TABLE cost_multipliers;
ALTER TABLE cost_multipliers_owned RENAME TO cost_multipliers;
CREATE UNIQUE INDEX cost_multipliers_key ON cost_multipliers (IFNULL(owner_id,''), prefix);

-- axes ------------------------------------------------------------------ --

CREATE TABLE axes_owned (
    name       TEXT NOT NULL,
    modality   TEXT NOT NULL,
    json       TEXT NOT NULL,
    builtin    INTEGER NOT NULL DEFAULT 0 CHECK(builtin IN (0,1)),
    owner_id   TEXT,
    visibility TEXT NOT NULL DEFAULT 'shared' CHECK(visibility IN ('private','shared')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO axes_owned (name, modality, json, builtin, created_at, updated_at)
SELECT name, modality, json, builtin, created_at, updated_at FROM axes;

DROP TABLE axes;
ALTER TABLE axes_owned RENAME TO axes;
CREATE UNIQUE INDEX axes_owner_key ON axes (IFNULL(owner_id,''), modality, name);
CREATE INDEX axes_name ON axes (name);

-- connectors ------------------------------------------------------------ --

CREATE TABLE connectors_owned (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    base_url     TEXT NOT NULL,
    token_env    TEXT,
    read         INTEGER NOT NULL DEFAULT 1,
    write        INTEGER NOT NULL DEFAULT 0,
    poll_minutes INTEGER NOT NULL DEFAULT 60,
    last_pull_at TEXT,
    last_push_at TEXT,
    last_error   TEXT,
    options      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    owner_id     TEXT
);

INSERT INTO connectors_owned (id, name, kind, base_url, token_env, read, write,
    poll_minutes, last_pull_at, last_push_at, last_error, options, created_at)
SELECT id, name, kind, base_url, token_env, read, write, poll_minutes,
    last_pull_at, last_push_at, last_error, options, created_at FROM connectors;

DROP TABLE connectors;
ALTER TABLE connectors_owned RENAME TO connectors;
CREATE UNIQUE INDEX connectors_owner_name ON connectors (IFNULL(owner_id,''), name);

-- chains and rankings --------------------------------------------------- --
--
-- Both were keyed by profile name alone, so the owner's `judge` and a member's
-- `judge` would have overwritten each other's list on every run.

CREATE TABLE chains_owned (
    profile         TEXT NOT NULL,
    computed_at     TEXT NOT NULL,
    primary_model   TEXT NOT NULL,
    incumbent       TEXT,
    incumbent_since TEXT,
    json            TEXT NOT NULL,
    owner_id        TEXT
);

INSERT INTO chains_owned (profile, computed_at, primary_model, incumbent, incumbent_since, json)
SELECT profile, computed_at, primary_model, incumbent, incumbent_since, json FROM chains;

DROP TABLE chains;
ALTER TABLE chains_owned RENAME TO chains;
CREATE UNIQUE INDEX chains_owner_profile ON chains (IFNULL(owner_id,''), profile);

CREATE TABLE rankings_owned (
    snapshot TEXT NOT NULL,
    profile  TEXT NOT NULL,
    modality TEXT NOT NULL,
    at       TEXT NOT NULL,
    json     TEXT NOT NULL,
    owner_id TEXT
);

INSERT INTO rankings_owned (snapshot, profile, modality, at, json)
SELECT snapshot, profile, modality, at, json FROM rankings;

DROP TABLE rankings;
ALTER TABLE rankings_owned RENAME TO rankings;
CREATE UNIQUE INDEX rankings_owner_key ON rankings (snapshot, profile, IFNULL(owner_id,''));
CREATE INDEX rankings_profile ON rankings (profile, at DESC);

PRAGMA foreign_keys=ON;
