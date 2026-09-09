# Fixtures

Every file here is a **real API response**, recorded and trimmed. Nothing in
this directory is invented any more: the hand-built `artificialanalysis_ai_*`
files that phase 1 had to write from the documentation — because that machine
had no key — were deleted in phase 2 part 1 and replaced by the recordings
below.

## The recordings

| recorded | source | files |
|---|---|---|
| earlier | OpenRouter, no key needed | `openrouter_ai_api_v1_models.json` |
| 2026-09-08 | Artificial Analysis, with a live key | the ten `artificialanalysis_ai_*` files |
| 2026-09-08 | fal, no key needed | `fal_ai_api_models__limit_200_page_1.json` |
| 2026-09-09 | deepinfra, no key needed | `api_deepinfra_com_models_list.json` |

`RECORDINGS.json` gives, per file, the exact URL it came from, the query
parameters that were sent, the date, how many rows the endpoint published that
day, and how many were kept. They are **trimmed**: the LLM endpoint published
644 models and 60 are kept here, the five arena endpoints published 74–157 each
and 40 are kept. The four free-tier files are whole, being small enough
(15–67 rows).

The deepinfra file keeps all 116 media rows and four rows of other types, so a test can prove the others are skipped rather than assume it.

They are **response bodies only**. No key, no request header and no
`Authorization` value is inside any of them. Never commit a recording that
carries a credential, and never enlarge one past what a test needs to read.

Because they are trimmed, a count asserted in a test is the count *in the file*,
not what the endpoint publishes — `RECORDINGS.json` holds both, so a test that
cares about the real figure can read it from there rather than hard-coding a
number nobody can check.

## How the player finds a file

The player is `sieve.http.FixturePlayer`. A file is named
`sieve.http.fixture_slug(url, params)` and holds either the raw body or
`{"status": ..., "headers": {...}, "body": ...}`. `SIEVE_FIXTURES=1` makes the
CLI and the API use it, so nothing in the tests touches the network.

Note the `__include_categories_true` suffix on the five arena files. The player
tries the params-qualified name first and the bare name second, so a file named
without the suffix would still be found — by the fallback, silently. These are
named for the request that actually produced them, so the two agree.

## Re-recording

Re-record with the same trimming when a shape changes, and update
`RECORDINGS.json` in the same commit. A recording that no longer matches what
the API returns is worse than no recording, because the suite stays green while
the real pull breaks.

## Not a recording

`rank_case.json` and `make_rank_case.py` are a constructed case, not an API
response: twelve synthetic models over six axes, with a deliberate exact tie, a
model below the confidence floor and one at half coverage. It exists so the
Python scorer and its TypeScript port can be asserted against the same numbers
to 1e-6, which needs values chosen on purpose rather than found.
