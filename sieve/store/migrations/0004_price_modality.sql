-- A price belongs to a modality, not only to a model.
--
-- fal prices one model per endpoint: `wan-25-preview` is $0.05 per image on
-- text-to-image and a resolution-tiered per-second rate on text-to-video. With
-- the price keyed on (model, source) alone the image rate landed on the video
-- row -- a wrong price, which is worse than no price at all.
--
-- NULL means "every modality this model is in", which is what a single-modality
-- source means and what every row written before this migration meant.
--
-- The table is rebuilt rather than altered because the uniqueness lived in a
-- table constraint, and SQLite cannot alter one in place: ADD COLUMN alone
-- would leave the old UNIQUE (model_id, source, observed_at) binding, and the
-- second modality's price would be silently ignored.
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
    observed_at  TEXT NOT NULL,
    UNIQUE (model_id, source, modality, observed_at)
);

INSERT INTO prices_new
    (id, model_id, source, modality, unit, input, output, cached_input,
     per_unit, source_url, observed_at)
SELECT id, model_id, source, NULL, unit, input, output, cached_input,
       per_unit, source_url, observed_at
FROM prices;

DROP TABLE prices;
ALTER TABLE prices_new RENAME TO prices;

CREATE INDEX IF NOT EXISTS prices_lookup ON prices (model_id, modality, observed_at DESC);
