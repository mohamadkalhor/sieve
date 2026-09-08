-- An observation belongs to a modality, and the key that guarded it did not say so.
--
-- The table has carried a `modality` column since the beginning, but its
-- uniqueness was (model_id, source, field, observed_at). A model measured on
-- two leaderboards -- GPT Image 2 is on text-to-image and on image-editing, and
-- is one model id -- writes `elo` twice in one pull with the same pull-time
-- stamp, and the second INSERT OR IGNORE was silently dropped.
--
-- Measured against the 2026-09-08 recordings before the fix: image-editing kept
-- 13 of its 40 models and image-to-video 14 of 40, while text-to-image and
-- text-to-video, pulled first, kept all 40. Two whole modalities were losing
-- two thirds of their field to whichever endpoint happened to run first.
--
-- Rebuilt rather than altered for the same reason as migration 0004: SQLite
-- cannot alter a table constraint in place, and an ADD/DROP INDEX would leave
-- the old UNIQUE binding and go on dropping the rows.
CREATE TABLE observations_new (
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
    UNIQUE (model_id, modality, source, field, observed_at)
);

INSERT INTO observations_new
    (id, model_id, modality, source, field, value, unit, n, ci95,
     observed_at, pulled_at, snapshot)
SELECT id, model_id, modality, source, field, value, unit, n, ci95,
       observed_at, pulled_at, snapshot
FROM observations;

DROP TABLE observations;
ALTER TABLE observations_new RENAME TO observations;

-- DROP TABLE took the index with it.
CREATE INDEX IF NOT EXISTS observations_lookup
    ON observations (modality, source, field, model_id, observed_at DESC);
