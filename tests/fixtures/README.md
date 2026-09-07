# Fixtures

`openrouter_ai_api_v1_models.json` is a real recording, trimmed to 60 models,
with no key or auth header in it.

The `artificialanalysis_ai_*` files are **hand-built in the documented shape**,
not recordings: this build had no ARTIFICIAL_ANALYSIS_API_KEY. They exercise
the mapping -- units per field, an undocumented evaluation key, a `ci95` written
as "-12/12", per-category Elo -- but their numbers are invented. Replace them
with real recordings (keys and auth headers removed, 60 models or fewer) as soon
as a key is available, and the tests should keep passing unchanged.

The player is `sieve.http.FixturePlayer`: a file is named
`sieve.http.fixture_slug(url)` and holds either the raw body or
`{"status": ..., "headers": {...}, "body": ...}`. `SIEVE_FIXTURES=1` makes the
CLI and the API use it, so nothing in the tests touches the network.
