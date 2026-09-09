-- A tier is part of what makes a price unique.
--
-- PLAN 2.3: "Record EVERY tier, do not collapse them." A model that charges
-- $0.0125 per second at 480p and $0.04 at 1080p publishes three prices, and
-- keeping one of them is how a 1080p job gets billed at the 480p line.
--
-- The uniqueness was (model_id, source, modality, observed_at), so the second
-- and third tiers of one pull collided with the first and INSERT OR IGNORE
-- dropped them silently. The tier joins the key.
--
-- It becomes a UNIQUE INDEX over COALESCE'd columns rather than a table
-- constraint, because SQLite treats NULLs in a UNIQUE constraint as distinct
-- from each other: with `modality` NULL -- which every single-modality source
-- writes -- the old constraint never fired at all, and re-pulling a source
-- appended a duplicate row per model instead of being ignored. Coalescing to
-- '' makes "no modality" one value rather than infinitely many.
CREATE TABLE prices_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id     TEXT NOT NULL,
    source       TEXT NOT NULL,
    modality     TEXT,
    unit         TEXT NOT NULL,
    input        REAL,
    output       REAL,
    cached_input REAL,
    per_unit     REAL,
    source_url   TEXT,
    tier         TEXT,
    observed_at  TEXT NOT NULL
);

INSERT INTO prices_new
    (id, model_id, source, modality, unit, input, output, cached_input,
     per_unit, source_url, tier, observed_at)
SELECT MIN(id), model_id, source, modality, unit, input, output, cached_input,
       per_unit, source_url, tier, observed_at
FROM prices
GROUP BY model_id, source, COALESCE(modality, ''), COALESCE(tier, ''), observed_at;

DROP TABLE prices;
ALTER TABLE prices_new RENAME TO prices;

CREATE UNIQUE INDEX prices_identity ON prices
    (model_id, source, COALESCE(modality, ''), COALESCE(tier, ''), observed_at);

CREATE INDEX IF NOT EXISTS prices_lookup ON prices (model_id, modality, observed_at DESC);
