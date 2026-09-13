-- Runs and schedules: the loop under hand control.
--
-- Until now the only thing that ran the loop was a systemd timer, and the only
-- record it left was a decision row per profile. That cannot answer "did the
-- 04:30 run work", "what did it do", or "run just the harvest now" -- so a run
-- is a row of its own, and the cadence lives here rather than in a unit file
-- nobody can edit from a phone.

CREATE TABLE IF NOT EXISTS runs (
  id            TEXT PRIMARY KEY,
  step          TEXT NOT NULL,
  requested_by  TEXT NOT NULL,
  started       TEXT NOT NULL,
  -- NULL while the run is in flight. Exactly one row may hold NULL here.
  finished      TEXT,
  ok            INTEGER,
  summary       TEXT,
  error         TEXT,
  log_path      TEXT
);

CREATE INDEX IF NOT EXISTS runs_started ON runs (started DESC);
CREATE INDEX IF NOT EXISTS runs_step ON runs (step, started DESC);

-- One row per step, plus `full`. Seeded in Python rather than here, because
-- the default timezone is the one the box keeps: the retired timer fired at
-- 04:30 local time and the replacement has to fire at the same moment.
CREATE TABLE IF NOT EXISTS schedules (
  step        TEXT PRIMARY KEY,
  mode        TEXT NOT NULL DEFAULT 'off',
  at_minute   INTEGER NOT NULL DEFAULT 0,
  at_time     TEXT NOT NULL DEFAULT '04:30',
  timezone    TEXT NOT NULL DEFAULT 'Europe/Amsterdam',
  last_fired  TEXT
);
