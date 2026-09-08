# Phase 2 — the loop closes

Phase 1 made Sieve able to answer *which model*. Phase 2 makes it keep
answering, unattended, and lets an agent act on the answer without a person in
the middle. That is the whole theme: **measurement in, decision out, on a
schedule, with the outcome reported back.**

One worker, no fan-out. Phase 1's fan-out failed on two VPS limits and the
briefs were each hours of work against a 15-minute job ceiling; a seat card
has neither limit, so do the parts in order, yourself.

Sources that are only *more data* — Arena, LiveBench, Epoch — move to phase 3.
They add rows; they do not close the loop. PLAN.md §11 is amended to match.

Work in the order below. **Commit and push after every part**, and put a
one-paragraph note in `briefs/HANDOFF.md` under that part's heading saying what
you left. If you run out of quota, stop at a part boundary, write
`QUOTA REACHED` and the next step in HANDOFF, report, and stop.

---

## Ground truth measured on 2026-09-08

A real key was used on the owner's server to run phase 1's own code against the
live API. Take these as facts; they are the numbers your work has to keep true.

| command | result |
|---|---|
| `sieve pull aa_llm` | 644 models, 7,416 observations, 644 prices |
| `sieve pull aa_media` | 482 models, 1,369 observations — all five arena endpoints |
| `sieve pull openrouter` | 428 models, 428 prices; 299 with ≥200k context, 361 with tools |
| `sieve check` | green: 35 axes, 17 profiles |
| `sieve score --profile coder` | ranks, contributions, coverage and a flip line, on real data |

So PLAN §12 acceptance line 1c — *aa_llm ≥ 600 models* — **passes**. It is the
line the phase 1 report could not stand behind; it can now be struck.

Two things that run showed, both for part 1:

- `reachable` was `-` for every row, because that machine had no inventory
  configured. Nothing is wrong; there was simply no gateway to reach.
- `pareto_prune` still never fired. On 644 real models it should. Find out why
  or prove it is correct to stay silent.

---

## Part 1 · Real data replaces the invented fixtures

`tests/fixtures/recorded/` now holds **real, trimmed API responses**, recorded
2026-09-08 with a live key. No key or header is inside them; they are response
bodies only. `MANIFEST.json` gives, per file, the exact URL it came from,
whether `include_categories=true` was sent, how many rows the endpoint really
publishes, and how many were kept.

| file | endpoint | published → kept |
|---|---|---|
| `llms_models.json` | `/api/v2/data/llms/models` | 644 → 60 |
| `media_text_to_image.json` | `/api/v2/data/media/text-to-image` | 157 → 40 |
| `media_image_editing.json` | `/api/v2/data/media/image-editing` | 74 → 40 |
| `media_text_to_video.json` | `/api/v2/data/media/text-to-video` | 81 → 40 |
| `media_image_to_video.json` | `/api/v2/data/media/image-to-video` | 75 → 40 |
| `media_text_to_speech.json` | `/api/v2/data/media/text-to-speech` | 95 → 40 |
| `free_music_instrumental.json` | `/api/v2/media/music/instrumental/models/free` | 18 → 18 |
| `free_music_with_vocals.json` | `/api/v2/media/music/with-vocals/models/free` | 15 → 15 |
| `free_speech_to_text.json` | `/api/v2/media/speech-to-text/models/free` | 67 → 67 |
| `free_speech_to_speech.json` | `/api/v2/media/speech-to-speech/models/free` | 38 → 38 |

Do:

1. Rename each into the `fixture_slug()` name `FixturePlayer` looks up, and
   delete `tests/fixtures/artificialanalysis_ai_*`, the hand-built ones. Note
   the arena endpoints are fetched **with** `params={"include_categories":
   "true"}`, and `fixture_slug` appends `__include_categories_true` when params
   are passed — check which name the player actually resolves and make the two
   agree rather than leaving a silent miss.
2. Make the whole suite green against real numbers. `tests/fixtures/README.md`
   must say these are recordings, when, and that they are trimmed.
3. Do the `data/aliases.yaml` pass the phase 1 brief asked for and HANDOFF
   deferred: real AA slugs against real OpenRouter ids. The seed is small and
   hand-written today.
4. Settle `pareto_prune`: either it now fires on real data (add the test that
   shows it) or explain in HANDOFF why nothing in a 644-model field is strictly
   dominated on a weighted axis set.
5. Delete `1c` from the open list in HANDOFF and PLAN §12; it is proven.

**Done when:** `pytest` is green with no invented number left in the tree,
`sieve check` is green, and a test asserts the real observation count for a
recorded pull.

---

## Part 2 · The free-tier media endpoints, with their true shapes

`aa_media` implements these behind `enabled` flags that default off, written to
a guess from the documentation. The guess has been checked against the live API
and it is **wrong in five specific ways**. Real shapes:

```
/api/v2/media/music/instrumental/models/free   18 rows  {id, name, model_creator, elo, ci_95}
/api/v2/media/music/with-vocals/models/free    15 rows  {id, name, model_creator, elo, ci_95}
/api/v2/media/text-to-speech/models/free       96 rows  {id, name, slug, model_creator, elo, ci_95}
/api/v2/media/speech-to-text/models/free       67 rows  {id, name, model_creator, aa_wer_index}
/api/v2/media/speech-to-speech/models/free     38 rows  {id, name, slug, model_creator,
                                                         bba_score, fdb_score, tau_voice_score}
```

Every response is `{"tier": "free", "data": [...]}`. The field is **`ci_95`**,
not `ci95`. There is no `rank`, no `appearances`, no `release_date`, and music
and speech-to-text carry **no `slug`**.

The five defects to fix:

1. **Music collides with itself.** `music/instrumental` and `music/with-vocals`
   both map to modality `music` and both write a field named `elo`, for the same
   models, in one pull. `observations` is unique on
   `(model_id, source, field, observed_at)`, so one silently loses. They are two
   different leaderboards — Suno V5.5 scores 1186 on one and 1170 on the other.
   Store `elo:instrumental` and `elo:with_vocals`, and give `music` an axis for
   each.
2. **`text-to-speech` is pulled twice** — it is in `ENDPOINTS` and in
   `FREE_ENDPOINTS`. Decide which wins. The arena endpoint carries more (rank,
   appearances, release date, price); the free one carries 96 rows against 95.
   Whichever you keep, only one may run.
3. **`ci_95` becomes an observation of its own.** The generic free-tier parser
   stores every numeric field, so the confidence interval lands as a fake
   measurement beside the Elo. It belongs in `Observation.ci95` of the Elo row,
   as the arena parser already does.
4. **`speech-to-speech` is not pulled at all**, and `"speech-to-speech"` is not
   in the `Modality` literal. Adding it is a `CONTRACTS.md` §1 change: make it by
   editing the file in the same commit and saying why, as CONTRACTS instructs.
   Its three scores are not interchangeable — `bba_score` is populated far more
   often than the other two, so an axis over them needs the coverage rule, not a
   silent zero.
5. **`aa_wer_index` direction is unknown.** The name says index, the values
   behave like a rate: Google's Chirp reads 0.3 while three others read 0.1 and
   one reads 0. Do not guess in code. Read the published speech-to-text
   leaderboard, decide from the order models appear in, set `higher_is_better`
   accordingly, and write the reason in the axis file. If the leaderboard cannot
   settle it, leave the source disabled and say so — a wrong direction here
   silently recommends the worst model.

Also: with no `slug` on music and speech-to-text rows, id matching must fall
back to name plus creator. Check what `model_id_of()` does with those rows
before trusting them.

**Done when:** all five free endpoints pull into distinct, correctly-named
fields; `music`, `speech-to-text` and `speech-to-speech` each have axes and at
least one shipped profile; `sieve check` is green; the fixtures in part 1 cover
them.

---

## Part 3 · Telemetry and health

Health is the one axis no benchmark can supply: whether the model is answering
*you*, today. Everything for it exists in pieces — `TelemetryEvent` in
contracts, a `telemetry` table, `health()` in scoring, a route in the API table,
a sparkline column on the Rankings screen — and none of it is joined up.

Do:

1. `POST /v1/telemetry` accepts a batch, validates, persists, returns
   `{accepted}`. Scope `telemetry`. Prune rows older than 30 days on write or on
   the scheduled run, as CONTRACTS §4 says.
2. Accept either a local id or a canonical id in `TelemetryEvent.model`, and
   resolve local ids through the catalogue, since the caller is a gateway that
   only knows its own names.
3. Wire `health()` into the engine so `final = score × health` is real rather
   than always 1.0, over the last 24 h with the 7-day figure kept for the
   sparkline.
4. Make `policy.suspend_below_health` and `policy.require_telemetry` actually
   fire, each with its own `Decision` kind and a sentence a person can read.
5. Serve `health_series()` — the route the Rankings sparkline column was built
   for and never got.
6. Build the **Pulse** screen: per model, ok rate, rate-limited share, p50 and
   p95 latency, events counted, over a 24 h / 7 d toggle. Reachable models only.
   It is the sixth screen in PLAN §8 and the only one not built.

Health is computed from what callers report. Do **not** add a log reader for
anyone's gateway; that is a phase 3 connector.

**Done when:** posting events moves a model's health and its rank; a model
below `suspend_below_health` is suspended with a decision row naming the number;
Pulse draws all of it from `/v1`.

---

## Part 4 · Targets that write to a real gateway

Today `file` and `http` exist, and the Chains diff compares against the last
`apply` decision rather than the target itself — so a target that drifts
underneath you shows no diff. Three targets and one fix:

1. **`webhook`** — POST the chain when it changes. Secret from an env var named
   in config, HMAC signature header, retry with backoff, and `current()`
   honestly unsupported rather than faked.
2. **`ninerouter`** — the one the owner will test on. A 9router instance serves
   an OpenAI-compatible catalogue on `http://localhost:20128/v1/models`, in which
   each combo appears as a model with `owned_by: "combo"`, and keeps its state in
   a SQLite file whose `combos` table is
   `(id TEXT PK, name TEXT UNIQUE, kind TEXT, models TEXT, createdAt, updatedAt)`
   where `models` is a JSON array of local model ids in fallback order — for
   example `["oc-go/deepseek-v4-flash","oc-go/glm-5.3-flash", …]`. Its admin API
   answers `{"error":"Unauthorized"}` without a token, so: prefer the HTTP API
   when a token is configured, fall back to the SQLite path when one is given,
   and refuse with a clear message when neither is. `current()` reads the live
   chain, so the diff is real. Back the file up before writing it.
3. **`litellm`** — write a `router_settings.fallbacks` block to a YAML path.
4. **Fix the Chains diff** to call each target's `current()`.

**Safety, absolute:** do not point any target at a live gateway, and do not
write to one. Test against a fake HTTP server and a scratch SQLite file you
create in `tmp_path`. The live 9router test is run by the owner's session on his
own box, after review.

**Done when:** each target has unit tests over a fake; `sieve diff` shows a real
difference against a scratch gateway; `sieve apply --yes` writes it and
`--dry-run` does not; a refusal without credentials says exactly what is
missing.

---

## Part 5 · The loop runs itself

1. A scheduled run: **pull every enabled source → evaluate every profile →
   decide → apply only where `policy.auto_apply` is true**. One command, one
   decision row per profile per run, including holds.
2. `deploy/sieve-pull.timer` exists; add the evaluate half, or make one timer do
   both and rename it honestly. Both units must survive a run with a source
   down.
3. Respect rate limits: Artificial Analysis allows 1,000 requests a day and
   publishes `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`
   headers. An hourly pull of the LLM endpoint plus five arena endpoints is
   about 150 a day, comfortably inside it — record the arithmetic in the docs so
   nobody has to redo it, and back off on a `Reset` rather than hammering.
4. `GET /v1/events` emits `pull`, `ranking`, `decision` and `apply` over SSE so a
   watching agent, or the web app, sees a change as it happens.
5. Document the loop in `docs/` as the thing an operator turns on: what runs,
   how often, what it may change without asking, and how to stop it.

**Done when:** the timer runs the whole loop unattended on a machine with no
person at it; a profile with `auto_apply: true` ships a change and logs why; one
with `false` records the same decision and writes nothing.

---

## Part 6 · Finish the web

From HANDOFF, in value order:

1. The profile editor edits **constraints, shape and policy**, not only weights.
   `PATCH /v1/profiles/{name}/policy` exists and no screen calls it.
2. New profile, cloned from an existing one.
3. The Chains screen shows the `current()` diff from part 4 and the apply
   confirmation names the target it will write to.
4. Lenis: wire it, or delete the line from the brief and say the app uses native
   scroll on purpose. Do not leave it ambiguous.
5. The Field scatter redraws every point on every hover. At 644 real models,
   split it into a drawn layer and a hover overlay, or measure it and show the
   measurement says it does not matter.
6. Lighthouse: run it if a Chrome exists on that machine. If none does, say so
   plainly in the report — an unrun check is never a pass.

---

## Part 7 · Only if quota remains

New sources, behind the same `Source` protocol, in this order of value:
**Arena** (`lmarena-ai/leaderboard-dataset` on Hugging Face, CC-licensed, human
preference Elo, and it covers image and video too), then **LiveBench**
(contamination-free, per-category), then **Epoch AI** (CC-BY, CSV or
`pip install epochai`). None has a REST API; each is a download, so they need a
cache with an ETag or a date check and must never run in CI.

Do not start one unless the part before it is pushed and green.

---

## Report

Same format as phase 1, to the card and to `relay say --as negar-cl`:

- commit hash and commit count;
- every "Done when" above marked pass / partial / fail, **with the command and
  its output as proof** — the phase 1 report was believed because it quoted real
  output, including the one line it refused to claim;
- anything unfinished, verbatim, per part;
- decisions you took where this brief left a choice, and why;
- any `CONTRACTS.md` change as a diff.

## Rules that do not bend

- No secret, hostname, person or product name in the tree. Keys from env only.
- **No target may point at a live gateway during this build.** Fakes and scratch
  files only.
- The web reads only `/v1`. `types.ts` is generated, never hand-edited.
- Missing benchmark = declared penalty and coverage loss, never a silent zero.
- Every profile change, switch, hold, suspend and apply is a decision row with
  an actor.
- A number you did not measure does not go in the report as a pass.
