# Recorded responses

Real Artificial Analysis API responses, recorded 2026-09-08 with a live key.
They are **response bodies only** — no key, no request header and no
`Authorization` value is inside any of them.

`MANIFEST.json` gives, per file: the URL it came from, whether
`include_categories=true` was sent, how many rows the endpoint published that
day, and how many were kept. Most are trimmed; the four `free_*` files are
whole, being small.

They exist to replace `tests/fixtures/artificialanalysis_ai_*`, which were
written by hand to the documented shape with invented numbers because the
phase 1 build had no key. See `briefs/PHASE-2.md`, part 1: rename these into
the names `fixture_slug()` produces, delete the hand-built ones, and make the
suite green against real numbers.

Re-record with the same trimming when a shape changes. Never commit a
recording that carries a credential, and never enlarge one past what a test
needs to read.
