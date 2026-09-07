-- What a model can do, as published by a source (OpenRouter today).
-- Inventory capabilities stay on `reachable`: those describe one gateway's copy
-- of a model, while these describe the model itself. The engine overlays the
-- inventory's view on top of this one.

CREATE TABLE IF NOT EXISTS capabilities (
    model_id   TEXT NOT NULL,
    modality   TEXT NOT NULL,
    source     TEXT NOT NULL,
    capability TEXT NOT NULL DEFAULT '{}',
    seen_at    TEXT NOT NULL,
    PRIMARY KEY (model_id, modality, source)
);

CREATE INDEX IF NOT EXISTS capabilities_modality ON capabilities (modality);
