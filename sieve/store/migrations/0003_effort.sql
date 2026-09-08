-- Reasoning effort, per PLAN 2.1: a source that publishes one row per effort
-- mode is describing a different model in each. `effort` is the mode this row
-- is about and `family` is the id its modes share, so a profile can choose
-- between them instead of a matcher silently collapsing them onto one row.
ALTER TABLE models ADD COLUMN effort TEXT;
ALTER TABLE models ADD COLUMN family TEXT;

CREATE INDEX IF NOT EXISTS models_family ON models (family);
