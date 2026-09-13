CREATE TABLE axes (
    name TEXT NOT NULL,
    modality TEXT NOT NULL,
    json TEXT NOT NULL,
    builtin INTEGER NOT NULL DEFAULT 0 CHECK(builtin IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(modality, name)
);
CREATE INDEX axes_name ON axes(name);
