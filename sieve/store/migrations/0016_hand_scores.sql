-- Scores a person (or an agent acting for one) gives a model no source has
-- benchmarked: one row per model, modality and axis, 0..1, in the same units
-- an axis value already is. Kept out of `observations` on purpose -- that
-- table is append-only measurement, and a hand score is a judgement that gets
-- edited and cleared.
CREATE TABLE hand_scores (
    model_id  TEXT NOT NULL,
    modality  TEXT NOT NULL,
    axis      TEXT NOT NULL,
    value     REAL NOT NULL CHECK(value >= 0 AND value <= 1),
    by        TEXT NOT NULL,
    at        TEXT NOT NULL,
    owner_id  TEXT
);

CREATE UNIQUE INDEX hand_scores_key
    ON hand_scores (IFNULL(owner_id, ''), model_id, modality, axis);
