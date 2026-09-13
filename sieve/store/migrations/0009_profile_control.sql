CREATE TABLE profiles (
    name TEXT PRIMARY KEY,
    modality TEXT NOT NULL,
    json TEXT NOT NULL,
    settings TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE profile_models (
    profile TEXT NOT NULL REFERENCES profiles(name) ON DELETE CASCADE ON UPDATE CASCADE,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','pinned','removed')),
    pin_order INTEGER,
    PRIMARY KEY(profile, model_id)
);
CREATE TABLE cost_multipliers (
    prefix TEXT PRIMARY KEY,
    multiplier REAL NOT NULL CHECK(multiplier >= 0)
);
CREATE TABLE outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT NOT NULL REFERENCES profiles(name) ON DELETE CASCADE ON UPDATE CASCADE,
    model_id TEXT NOT NULL,
    local_id TEXT NOT NULL,
    ok INTEGER NOT NULL,
    seconds REAL NOT NULL,
    vote INTEGER NOT NULL CHECK(vote BETWEEN -1 AND 1),
    note TEXT,
    at TEXT NOT NULL
);
CREATE INDEX outcomes_profile_at ON outcomes(profile, at);
