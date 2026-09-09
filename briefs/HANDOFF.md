# HANDOFF

Cross-owner notes. Nobody edits another owner's files: if you need a change in
one, write it here under **that owner's** heading and keep working.

---

## What actually happened

The fan-out did not run. The relay host allows **two concurrent workers** and
one was already taken, so only agent A started; B, C, D and E came back
`Busy: limit is 2`. Agent A was then killed by a hard **900-second job
timeout** at 15 minutes, having pushed nothing.

Every part was therefore built by the integrator, in the owners' own files and
to the same contract, in this order: foundation, B, A, E, C and D. The
ownership table below is still the right map of the codebase, and the next
person to pick up a letter should read it as "these files are yours".

If the two VPS limits are raised, a genuine fan-out is still the right shape
for the next phase. Briefs that fit a 15-minute budget are the other half of
that: each of A–E as written is several hours of work.

## Everything below is landed

- **`sieve/contracts.py`** holds every shared type of CONTRACTS §1 **and** the
  §2 protocols, plus `SourceConfig`, `InventoryConfig`, `TargetConfig`,
  `PullResult`, `TargetResult`, `RateLimit`, `HttpResponse`, `ObsTable`,
  `EngineResult`. `sieve/sources/base.py` re-exports them rather than defining
  a second copy.
- **`sieve/http.py`** is the one `HttpClient`. `FixturePlayer` serves
  recordings by URL; the filename is `fixture_slug(url)`, e.g.
  `openrouter.ai_api_v1_models.json`, and `SIEVE_FIXTURES=1` switches the CLI
  and the API onto it.
- **`sieve/plugins.py`** resolves the three entry-point groups lazily.
- **`sieve/engine.py`** owns the order of PLAN §5 and the constraint gate
  (`passes_constraints`), because that needs `Capability`, which is store-side.
  It also derives `price:per_task` per profile so a cost axis can rank it.
- **API** answers every route of CONTRACTS §6. `create_app()` builds config and
  store on demand, so an in-process ASGI call works without a lifespan.
- **`tests/fixtures/rank_case.json`** is final. Regenerate only with
  `make_rank_case.py`; `weigh.py` and `weigh.ts` both assert against it.

## Signatures the seam depends on

| module | function |
|---|---|
| `sieve.axes.load` | `load_axes(dir, modality)`, `load_all_axes(dir)` |
| `sieve.axes.compute` | `axis_values(axis, obs, pool) -> {model: (value\|None, coverage)}` |
| `sieve.scoring.pareto` | `pareto_prune(rows, weights) -> {model: dominated_by}` |
| `sieve.scoring.weigh` | `weigh(profile, axes_by_model) -> {model: (score, confidence, contributions)}` |
| `sieve.scoring.health` | `health(events, now) -> {model: health}` |
| `sieve.scoring.policy` | `decide(profile, incumbent, ranking, now) -> (Chain\|None, Decision\|None)` |
| `sieve.scoring.explain` | `explain(ranking, profile) -> str \| None` |
| `sieve.profiles.load` | `load_profiles(dir)` |
| `sieve.profiles.save` | `save_profile(dir, profile)` |
| `sieve.profiles.validate` | `validate_profile(profile, axis_names)` |

`engine.py` and `web/src/lib/rank/weigh.ts` both depend on these shapes.

---

## A · scoring and axes

Landed and green. Left for whoever takes this on:

- `health_series()` produces the 7-day sparkline the Rankings screen has a
  column for, but no route serves it yet and no screen draws it. It needs
  telemetry to exist first, which is phase 2.
- `pareto_prune` is unit-tested but never fires on the shipped fixture data —
  no model in it is strictly dominated. Worth re-checking against a real AA
  pull, where it should prune a great deal.

## B · catalog, sources, inventory, targets, profiles

Landed and green. Left:

- ~~The AA fixtures are hand-built, not recorded.~~ **Done, phase 2 part 1.**
  Ten real recordings replace them. The tests did *not* keep passing unchanged,
  and that is the interesting part — see the part 1 note below.
- `aa_llm` stamps `observed_at` with the pull time, because the fixture
  publishes no date. Against the real API, prefer whatever date AA publishes,
  or every hourly pull adds a full set of rows rather than being deduped.
- The AA free-tier music and speech-to-text endpoints are implemented behind
  `enabled` flags that default off, and have never been called. Their shape is
  a guess from the documentation.

## C · web scaffold, Field, Rankings, Sources

Landed and green. Left:

- **Lenis is not wired.** The brief asks for it; the app uses native scroll.
  It was dropped rather than left as an unused dependency.
- The Field scatter draws every point on each redraw. That is comfortably fast
  at the ~60 points the fixtures produce and should still be fine at 644, but
  the "draw once, redraw the hover only" split the brief describes is not
  implemented — only the hit index is.
- The virtualised list above 200 rows is not built; no profile currently ranks
  that many.
- **Lighthouse has not been run.** No Chrome with Lighthouse on this machine.

## D · web Profiles, Chains, client rank

Landed and green. `weigh.ts` matches `weigh.py` on the shared fixture to 1e-6,
FLIP animates the live list and collapses under reduce-motion. Left:

- The "New profile" clone-from-existing control on the Profiles index is not
  built.
- The Chains diff compares against the last `apply` decision rather than
  calling each target's `current()`. For the `file` target those agree; for a
  target that can drift underneath you they would not.
- The editor cannot change constraints, shape or policy — it shows them as
  chips and edits weights only. `PATCH /v1/profiles/{name}/policy` exists and
  is untouched by any screen.

## E · axes data, profiles data, docs, CI, deploy

Landed and green. Left:

- `data/aliases.yaml` is a small hand-written seed, not the pass over B's
  fixtures the brief asks for. That pass needs real AA data to be worth doing.
- The media category axes are generated from what the fixtures publish
  (`anime`, `nature`, `text`, `physics`, `moving_camera`, `photoreal`). A real
  pull will publish more, and each wants its own file.
- `text-to-image/photoreal` weights the `nature` category, because the source
  publishes no photorealism category for that modality today. The file says so
  in a comment. Revisit when one appears.
- **CI has never run on GitHub.** The workflow is written and every step in it
  passes locally; nothing has yet proved it green on a runner.
- `docs/api.md` is written by hand rather than generated from the OpenAPI at
  build time.

---

# Phase 2

## Part 0a · An effort mode is a different model

Landed. `ModelRef` gains `effort` and `family`, `Profile` gains
`prefer_effort`, and both are in `CONTRACTS.md` §1. Migration `0003_effort.sql`
adds the two columns.

The mode is read from the **published name**, never guessed from the slug, and
that distinction is the whole point: AA writes a family's top mode without a
suffix, and which mode that is varies by family. `gpt-5-6-sol` is `(max)`;
`gemini-3-8-flash` is `(high)`. Anything that assumed the bare slug meant "max"
would have been wrong for Gemini, and anything that stripped `-high` to reach
the base row would have sent `-low` there too, which is the bug itself.

A gateway names every mode explicitly, so the bare row is given an alias for
its own spelled-out mode — `google/gemini-3-8-flash` also answers to
`google/gemini-3-8-flash-high` — written from the data and never where AA
already publishes a distinct row under that id. Two matcher fixes were needed
to make that reach a real gateway id:

- aliases are now indexed by **slug** as well as in full. An alias is written
  as a canonical id while a gateway id carries the gateway's own prefix, so
  `ag/gemini-3.8-flash-high` could never equal `google/gemini-3-8-flash-high`
  however it was normalised.
- the ambiguity guard counted the *same* model twice as a conflict. A row often
  reaches one key by two routes — its published name and its spelled mode both
  normalise to `gemini38flashhigh` — and that was rejecting a match nothing was
  actually ambiguous about.

Proven on the CLI, same store, same data: `prefer_effort: best` seats
`openai/gpt-5-6-sol` (max) at 0.728; `cheapest_clearing` seats
`openai/gpt-5-6-sol-non-reasoning` at 0.378.

### Decisions taken where the brief left a choice

- **Cost is still computed per mode from the profile's shape.** The brief says
  to use "that mode's own output-token count where a source publishes one" and
  otherwise to mark the cost unknown rather than copying the base one. The
  recording publishes **no output-token count for any model** — only
  `median_output_tokens_per_second` and time-to-first-token — so marking every
  mode's cost unknown would remove cost from 50 of 60 rows and from every
  weighted `cost` axis. Each AA row carries its own `pricing` block, so nothing
  is being *copied from a base row*, which is what that instruction guards
  against. The true statement Sieve makes today is "at this shape, every mode of
  a family costs the same", and it is worth more than a blank. **What is still
  missing is the real thing:** a high mode burns more output tokens for the same
  task, and no source we read publishes that, so cost cannot yet separate modes.
  Overturn this if you would rather see `unknown`.
- **`cheapest_clearing` picks the lowest mode still standing after the
  constraint gate.** That makes it only as good as the profile's floors: with no
  `require` and no `min_axis`, it will always seat the bottom of the ladder,
  which the demonstration above shows plainly (non-reasoning at 0.378). It is
  meant to be used with floors set. A future refinement could clear against the
  *incumbent's* score rather than the profile's floors.
- **An unstated mode ranks above every stated one**, so `best` prefers a model
  with no modes over a mode row. The alternative — treating "no mode" as
  low — would have demoted every non-reasoning-capable model.

### Left open

- **`prefer_effort` is on no shipped profile.** Every one of the 17 leaves it
  unset, which keeps phase 1 behaviour exactly. Setting it is a judgement about
  each seat and belongs with whoever owns the profile.
- **The `:batch` price collision from part 1 is now sharper.** `:batch` folds
  onto its base model, and effort modes deliberately do not. Both are "the same
  model, served differently"; only one of them keeps its own row. If `:batch`
  should also stay separate, that is a one-line change to the tag rule and a
  larger question about what a catalog entry is.
- **Claude Opus 5's numbers are not locked.** The brief quotes 54.1 at max and
  43.8 at low; the 60-row trim does not include it. `Quasar 438B (max, based on
  GLM-5.2)` is parsed correctly, so the multi-clause bracket is covered, but the
  Opus figures themselves need a wider recording.

## Part 0 · answers folded back in

mhmd-cl answered the two questions the 0a report asked. Both answers are in the
tree now; neither undid anything already pushed.

**Cost per mode: keep it.** The rule's intent was "never copy a base row's
numbers onto a mode", which was already satisfied — each mode is priced from its
own AA pricing block. PLAN §2.1 is amended to say what is true rather than what
was assumed: the rate per token is identical across modes, no source in our set
publishes a per-task output-token count, so a high mode's extra token burn is
**unmeasured**. AA's own site computes it; the v2 API does not expose it and we
do not scrape. Three consequences landed:

- `Rank.cost_from` records whether a cost came from the profile's shape or from
  measured tokens, and `sieve score` prints `cost $0.0375 per task, estimated
  from the profile shape`. A cost nobody can tell is an estimate is worse than
  one that admits it.
- Part 3's "Done when" now requires cost from telemetry: an observed
  output-token multiplier per model, and a model with telemetry pricing
  differently from the same model without.
- Nothing else changed, because nothing else was wrong.

**prefer_effort: a guard first, then nine seats.** `sieve check` now **fails**
when `cheapest_clearing` is set on a profile with no floor to clear — that
combination always seats the bottom of the ladder and calls it a decision — and
fails when `prefer_effort` appears on a non-llm profile, since effort modes are
an LLM thing and a silently ignored setting rots. `reasoner` takes `best`; the
other eight LLM seats take `cheapest_clearing` with a floor on the axis that
defines them. `coder`'s existing `min_axis` on `agentic_coding` was **raised
from 0.5 to 0.75** as instructed: the floor now decides how far down a family's
ladder the seat may go, so it has to be the standard a coding agent needs rather
than the lowest tolerable one.

**An axis field nothing publishes is now an error.** `sieve check` compares every
axis field against what the store has actually seen for that modality, and fails
with the nearest fields the source does publish. It skips a modality with no
observations, so a fresh install is unaffected. On its first run against real
media data it found a bug nobody was looking for: **`maturity` for
text-to-speech weighted `appearances` 0.6, and that endpoint has never published
it** — its rows carry `elo`, `ci95`, `rank`, `id`, `name`, `model_creator` and
nothing else. The axis was silently judging every model on the remaining 40%. It
now reads `ci95` alone, and says why in the file.

### Left open

- The eight `cheapest_clearing` floors are "a considered start, not measured
  truth" in mhmd-cl's words. None has been checked against a live ranking to see
  whether it seats something absurd; the trimmed recording is too small to tell.
- `coder` at `agentic_coding: 0.75` is a real tightening. On the 60-model
  recording it leaves a small field.

## Part 0b · fal, the price for the media field

Landed. `sieve/sources/fal.py`, no key, registered as a plugin and enabled in
`sieve.toml.example`. Recording in `tests/fixtures`, 95 of 1,494 rows, chosen to
exercise every branch: each parsed unit, a resolution-tiered refusal, a token
table, and categories with no Sieve modality.

**Live numbers, 2026-09-08.** `sieve pull fal` reads 1,494 models over 8 pages
at `limit=200`. 985 sit in a Sieve modality; 509 are skipped and named
(`video-to-video` 203, `training` 59, `text-to-audio` 47, `audio-to-audio` 43,
`image-to-3d` 41, `vision` 34, and the rest). After folding endpoint variants —
`nano-banana-2/edit` and `nano-banana-2/text-to-image` are one model — that is
**435 model rows and 67 prices, with 83 of 124 price strings refused**.

Refusing them is the point. The parser reads only per-image, per-second and
per-token forms it can prove, and a **tiered** price is refused outright: "Video
costs $0.0125 per second at 480p, $0.02 at 768p, $0.04 at 1080p" has no single
rate, and taking the first would bill 4K work at the 480p line.

**Two defects had to be fixed before the join produced anything**, and both were
older than this part:

- **`data/aliases.yaml` never reached a pull.** `merge_pull` was handed
  `store.aliases()` only, so the hand-written file — whose own header says it
  beats every rule in the matcher — could not fix a source-to-source merge at
  all. It only ever reached inventory matching. The file is now merged in, with
  store entries still winning.
- **A price had no modality.** The table was keyed `(model_id, source,
  observed_at)`, so a model serving both image and video could hold one price,
  and fal's per-image rate landed on its video row. That is a wrong price, which
  the rules forbid outright. `Price.modality` is in CONTRACTS §1 and migration
  `0004_price_modality.sql` **rebuilds** the table, because the uniqueness lived
  in a table constraint and SQLite cannot alter one in place — an ADD COLUMN
  alone left the old UNIQUE binding and silently dropped the second modality's
  price.

**The join.** Ten checked aliases in `data/aliases.yaml` carry fal's ids onto
AA's, all slug-identical after normalisation and differing only in the vendor
prefix (`fal-ai/veo3.1` → `google/veo-3-1`, `xai/grok-imagine-image` →
`spacexai/grok-imagine-image`). `sieve score --profile image_general` then ranks
on quality *and* cost: `google/nano-banana-2` leads at 0.848 with
`cost +0.038 (cov 1.00)`, while a model with a score and no price shows
`cost +0.000 (cov 0.00)` — the coverage loss, not a silent zero.

### Left open

- **The join is small: 8 model-modality pairs.** Both sides are trimmed
  recordings (AA to 142 media models of 482), and fal carries hundreds of models
  AA never measures. Against a live AA key it will be larger. **The 482 figure
  the brief asks for cannot be measured here** — no AA key on this machine.
- **The near-misses are not aliases.** `nano-banana` against `nano-banana-2`,
  `gpt-image-1.5` against `gpt-image-2` — close names, different models. None was
  added, and the alias file says so, because fusing them is the exact failure the
  0.9 merge floor exists to prevent.
- **`merge_pull`'s 0.9 floor still rejects every 0.85 slug match.** Ten were
  hand-written instead. A slug that is *identical* after normalisation and
  differs only in creator is stronger evidence than the generic 0.85 rule, and
  could earn its own confidence — that is a matcher change nobody has agreed to,
  so it was not made.
- **`text-to-audio` (47 models) has no modality.** It is probably our `music`,
  but fal mixes music and sound effects under it and the rules say a wrong
  modality is worse than none. Needs a decision.
- **fal publishes no quality of any kind**, by design. Nothing here ranks a
  model on its own; it only makes a media model's cost knowable.
- **The Field chart for a media modality** is untested — that half of 0b's
  "Done when" is a web check and belongs with part 6.

## Part 2 · The free-tier media endpoints, with their true shapes

All five defects fixed, each with a test, plus a sixth the work exposed. The
free tier is one flag per endpoint in `sieve.toml.example`, and `FREE_ENDPOINTS`
is now a spec table describing what each really publishes rather than a
path-to-modality map read by a parser that stored every numeric key it saw.

1. **Music collided with itself.** Instrumental and with-vocals both wrote a
   field called `elo` for the same model in one pull, and observations are
   unique on `(model, source, field, observed_at)`, so one silently lost every
   time. They are `elo:instrumental` and `elo:with_vocals` now, with an axis
   each. Suno V5.5: 1186 and 1170 — two contests, as the brief said.
2. **text-to-speech was pulled twice**, from `ENDPOINTS` and `FREE_ENDPOINTS`.
   The arena endpoint wins: it publishes `rank` and it is the documented one.
   **The brief's reason for preferring it was wrong** — it says the arena tier
   carries appearances, release date and price, and against the recording it
   carries none of those; its rows are `elo`, `ci95`, `rank`, `id`, `name`,
   `model_creator` and nothing else. The free tier's only real advantage is one
   extra model, 96 against 95, which is not worth two sources disagreeing.
3. **`ci_95` was stored as a measurement of its own**, beside the Elo it
   describes. It is `Observation.ci95` on the Elo row now, as the arena parser
   already did.
4. **speech-to-speech was never pulled**, because `Modality` had no name for it.
   Added to the literal and to CONTRACTS §1. Its three scores are kept apart and
   their coverage is the reason: `bba_score` on 34 of 38 models, `fdb_score` on
   26, `tau_voice_score` on 21.
5. **`aa_wer_index` is a word error rate and lower is better.** Settled from the
   published leaderboard as instructed, not guessed: artificialanalysis.ai ranks
   speech-to-text by AA-WER, calls it "% of words transcribed incorrectly", and
   puts Fun-Realtime-ASR-preview (1.7%) first with Google's legacy Chirp (31.2%)
   last. With the direction set, `sieve score --profile transcription`
   independently seats `fun-realtime-asr-preview` first — the same model the
   leaderboard ranks first, which is the strongest confirmation available.

**The sixth: the ids were UUIDs.** Music and speech-to-text rows carry no
`slug`, and the fallback ran through to `id`, which is a UUID — producing
`suno/8a999846-4c1d-4ce7-a8b7-1310a7166fd7`, an id that matches nothing and
never will. It now goes slug, then name, and refuses a UUID outright. The name
needed cleaning too: the free tier writes `Cloud Speech-To-Text (Chirp), Google`
and the trailing creator was ending up in the slug.

**A seventh, found on the way.** `sieve --config <path>` fell back to the
defaults **without a word** when the path did not exist, so a typo ran the whole
command against a different store and different sources and looked like the
source had published nothing. It now fails with `no such config file: …`. A
missing `sieve.toml` where none was named is still fine.

New: `data/axes/music/{instrumental,with_vocals}.yaml`,
`data/axes/speech-to-text/accuracy.yaml`,
`data/axes/speech-to-speech/conversation.yaml`, and the profiles
`music_general`, `transcription`, `voice_agent`.

### Left open

- **The accuracy axis is coarse and says so.** The free endpoint rounds the
  error rate to one decimal, so 44 of the 58 models that publish a value all
  read 0.0 — everything under 5% ties. It separates a broken transcriber from a
  working one and nothing finer; the leaderboard's own 1.7/2.0/2.2 precision is
  thrown away before the API returns it. A test fails if the endpoint ever stops
  rounding, so the axis can be reweighted then.
- **`speech-to-speech` has one axis over three scores.** Whether `bba_score`
  deserves half the weight is a judgement nobody has made on evidence; the
  0.5/0.3/0.2 split follows their coverage, which is a proxy, not a reason.
- **Music has no cost.** fal's `text-to-audio` category is where music prices
  would come from and it has no Sieve modality yet — the open question with
  mhmd-cl.
- **The three new profiles are unreachable-only.** Nothing in any inventory
  serves music, speech-to-text or speech-to-speech, so they rank but seat
  nothing.

## Part 3 · Telemetry and health

Landed. Health was already wired into `final = score x health` and
`suspend_below_health` already fired in `decide()`; what was missing was
everything that makes them mean something.

- **`POST /v1/telemetry` resolves local ids.** A gateway knows its own names and
  nothing else, so `model` may be either and is resolved through the catalogue.
  An id that resolves to nothing is still stored under the name it arrived with
  -- dropping it would lose evidence, and it surfaces on Sources as an unmatched
  id for a person to alias. The route prunes to 30 days on write, which is when
  the table grows, and returns `{accepted, pruned}`.
- **`policy.require_telemetry` fires.** It was documented and read by nothing.
  It is a seat saying "do not put anything here I have never actually called",
  and it now excludes with that reason.
- **`GET /v1/health`** serves the sparkline the Rankings screen was built for and
  never got, plus the Pulse figures: ok rate, rate-limited share, p50 and p95
  latency, events, median output tokens, over a 24h/7d toggle. `HealthRow` is in
  CONTRACTS §1 so `types.ts` carries it.
- **Pulse is built** -- the sixth screen in PLAN §8 and the only one that did not
  exist. Sorted worst health first, because the screen exists to surface trouble.
  Three Playwright specs cover it, including the empty state, which matters as
  much as the full one: "nobody called it" and "every call failed" must never
  look alike.
- **Cost from measured tokens**, the requirement mhmd-cl added. `cost_per_task`
  takes an observed output-token count, `observed_tokens_out` derives a median
  per model over a trailing week, and the engine uses it where it exists.
  Proven on the CLI: the same model reads `cost $0.0005 per task, estimated from
  the profile shape` before telemetry and `cost $0.0480 per task, from measured
  tokens` after 40 calls that each burned four times the profile's assumption.

**A typegen bug fell out of it.** `list[float | None]` rendered as
`number | null[]`, which TypeScript reads as "a number, or an array of nulls".
The union is parenthesised now. Nothing had caught it because no exported model
had a list of an optional until `HealthRow.series`.

### Left open

- **`min_calls` is 5 and `days` is 7**, both chosen rather than measured. Five
  calls is a low bar for replacing a stated assumption with a median; nobody has
  looked at what number of calls makes the estimate stable.
- **The tokens are a median over the window**, so a model whose usage shifts
  (a longer prompt, a harder task) is priced on its recent past. That is the
  honest reading of "what it burns", but it is not a forecast.
- **Only `tokens_out` is used.** `tokens_in` is stored and ignored, though the
  profile's declared input shape is just as much an assumption.
- **The Rankings sparkline column still does not call the new route.** The
  column was built in phase 1 and the data now exists; wiring it is part 6.
- **Nothing prunes on a schedule.** Pruning happens on write, so a store that
  stops receiving telemetry keeps its last 30 days for ever. Part 5's scheduled
  run is the place for that.

## Part 4 · Targets that write to a real gateway

Three targets and the diff fix. **Nothing points at a live gateway**: the
webhook is tested against a fake HTTP server on localhost, 9router against a
scratch SQLite file built in `tmp_path` with its real `combos` schema, LiteLLM
against a YAML file.

- **`webhook`** POSTs one document with every profile and signs it: HMAC-SHA256
  over `timestamp.body`, secret from an env var named in config, timestamp
  inside the signed material so a captured request cannot be replayed later. It
  refuses to push at all without a secret, because a receiver that accepts an
  unsigned POST will route traffic for anyone who can reach the URL. 5xx and
  dropped connections retry with backoff; a **4xx does not** -- the receiver
  understood and refused, and sending it again is only load.
- **`ninerouter`** writes one combo per profile, prefixed `sieve-`, so a combo
  somebody made by hand is never touched. HTTP admin API when a token is
  configured, the SQLite file when a path is given, and a refusal naming both
  when neither is -- it will not guess which database to write. The file is
  copied before it is written: it belongs to another program, and corrupting a
  gateway's database is worse than failing to write.
- **`litellm`** owns exactly one key, `router_settings.fallbacks`. `model_list`,
  `general_settings` and everything else belong to whoever wrote them.

**The diff now reads the target, not the last decision.** `GET /v1/diff` asks
every configured target what it holds, and the Chains screen shows that instead
of the last `apply` decision -- which only ever said what Sieve *believed* it
wrote, so a target edited by hand or rolled back showed no difference at all.

Doing that properly needed one addition to the protocol: **`Target.plan()`**.
9router holds the gateway's own local ids and LiteLLM keys on the primary; the
engine computes canonical ids. Comparing those directly would have reported a
change on every single run, which teaches everyone to ignore the diff. Each
target now says what it *would* write in the same vocabulary it reads back, and
a test pins that `plan()` and `current()` agree exactly after a write.

`sieve apply --dry-run` exists, and needs no `--yes`, since demanding
confirmation for something that writes nothing only teaches people to type
`--yes` without reading it.

Proven end to end on a scratch database: seed a stale combo, `sieve diff` shows
`- cheap_bulk: gw/something-old`; `apply --yes` writes it and takes a backup;
`diff` then reads `= cheap_bulk: unchanged`; drift it again and `--dry-run`
prints "would write" and leaves it drifted.

### Left open

- **The 9router HTTP path is written but never exercised against 9router.** The
  admin endpoint shape (`PUT /admin/combos/{name}`) is inferred from the catalogue
  and the SQLite schema, not from its documentation. The SQLite path is the one
  proven here. **This is the live test the brief reserves for the owner.**
- **`current()` for `litellm` is keyed by primary, not by profile**, because its
  file format has no idea what a profile is. The diff is still real, but a
  profile rename looks like a new entry rather than a move.
- **The webhook has no receipt.** It reports the status it got and nothing about
  what the receiver did with the body. `current()` raises rather than returning
  `{}`, which the diff surfaces as "cannot be read back".
- **No target writes concurrently.** Two `sieve apply` runs against one 9router
  file would interleave; SQLite's own locking is all that stands there.

## Part 5 · The loop runs itself

Landed. One command, `sieve run`: pull every enabled source, evaluate every
profile, decide, and apply **only** where `policy.auto_apply` is true.

- **The units are renamed honestly.** `sieve-pull.*` became `sieve-run.*`, and
  the service now has **one** `ExecStart` instead of two. That was a real bug,
  not tidying: systemd stops at the first ExecStart that fails, so one source
  being down meant nothing was re-ranked at all -- from data already on disk and
  perfectly good.
- **A failed source no longer looks like a quiet one.** `PullResult.ok` is False
  when the endpoint could not be *read*, which is different from an endpoint
  that published nothing new. `sieve run` carries on with what is stored, ranks
  every profile, ships what opted in, and still **exits non-zero** so the unit
  shows as failed. A timer that can never fail is a timer nobody checks.
- **The rate-limit arithmetic is written down** in `docs/the-loop.md`: six AA
  requests per run, hourly, 144 a day against a 1,000/day budget -- 14%. With
  the free-tier endpoints on it is 240. The doc says not to raise the frequency
  without redoing it, and that the limit is per key, not per host.
- **Retries wait for the published reset.** `X-RateLimit-Reset` is now used, not
  merely recorded: three fast retries before the reset are three more refusals
  charged against the same budget. Capped at 120s, so a reset an hour away fails
  the run and lets the next timer pick it up rather than holding a systemd job
  open for an hour.
- **`ranking` joins the SSE stream**, so all four kinds in PLAN §8 are emitted.
- **`docs/the-loop.md`** is the operator page: what runs, how often, what it may
  change without asking, how to turn it off, and how to read a run.

Proven on a scratch install: `cheap_bulk` with `auto_apply: true` shipped
`out/cheap_bulk.json`; `quick_chat` made the identical decision, wrote nothing,
and appeared on the `held (no auto_apply)` line.

### Left open

- **The SSE bus is in-process.** A `sieve run` from the timer is a different
  process, so its events do not reach a running server's `/v1/events`. The work
  is still visible -- every run writes decision rows and `/v1/decisions` serves
  them -- but the live stream only covers work done through the API. Making the
  timer publish would need the bus to leave the process, which is a real design
  decision and not a small one.
- **`--no-pull` is the only granularity.** There is no way to say "pull only the
  sources whose rate budget allows it": the budget is per key and Sieve does not
  track spend across runs, only what the last response reported.
- **`RandomizedDelaySec=180` spreads a fleet, it does not coordinate one.** Two
  machines sharing a key still double the request count; nothing prevents that
  but arithmetic and attention.
- **Nothing prunes telemetry on the schedule.** Part 3 left it pruning on write,
  and `sieve run` does not prune. A store that stops receiving telemetry keeps
  its last 30 days for ever.

## Part 6 · Finish the web

Landed, all six items, and two of them were answered by measuring rather than
by building.

1. **The editor edits constraints, shape and policy**, not only weights.
   `PATCH /profiles/{name}/policy` existed and no screen called it; the two
   routes for the other blocks did not exist at all, so
   `PATCH /profiles/{name}/constraints` and `.../shape` are new. Constraints
   are **replaced**, not merged, because the interesting edit is *removing* one
   and a merge cannot say that -- `{"tools": false}` reads as "require the
   absence of tools". Each block saves on its own: they are three decisions and
   one Save button would make them look like one. Turning on `auto_apply` says
   in words what it will do unattended.
2. **New profile, cloned.** `POST /v1/profiles` with `{name, from}`. Starting
   empty means assembling weights that sum to 1 over axes for a modality you
   have not picked yet; starting from the seat next to it is how anyone
   actually makes one. It refuses a duplicate name with 409, because chains are
   keyed by name and phase 1 already shipped that collision once.
3. **The Chains screen** shows the `current()` diff from part 4, one block per
   target, and the apply confirmation now **names the targets it will write
   to**. A target that cannot be read back says so rather than showing an empty
   diff.
4. **Lenis: decided against, in writing.** `briefs/C.md` no longer asks for it
   and carries the reason. It replaces the browser's scrolling on a dense data
   tool -- a 644-row scatter, tables, a diff -- desynchronising `scrollIntoView`
   and moving Page Up/Down away from where the platform puts them.
   `animate:flip` on the ranking stays: it shows which row moved where, which is
   information.
5. **The scatter was measured, not rebuilt.** Phase 1's handoff said it redrew
   every point on hover. It does not, and `e2e/field-hover.spec.ts` proves it by
   counting `arc()` calls: a full repaint draws 59, and **40 hovers draw 0**.
   Per hover: median 0.10 ms, p95 0.90 ms. The zero is the real result -- it
   holds at any point count, because the scene effect does not depend on the
   hover at all.
6. **Lighthouse ran.** Desktop 100 performance / 100 accessibility. Mobile was
   73-76 with an LCP of 7.0 s, of which 6.5 s was *render delay* on a paragraph
   of static text. Two causes, both fixed:
   - the shells were not prerendered, so `index.html` was empty and nothing --
     not even a heading -- painted until the bundle hydrated. The static routes
     are prerendered now (`ssr` at build time only; the data still comes from
     `/v1` in the browser), and FastAPI serves `<path>.html` when it exists,
     which it previously ignored in favour of the SPA shell;
   - the Google Fonts stylesheet was render-blocking, on someone else's domain.
     It is loaded non-blocking with an onload swap, so a network that cannot
     reach Google shows the page in a fallback stack instead of nothing.

   Six mobile runs after: **64, 73, 94, 94, 95, 97**, median 94, accessibility
   100 every time. The spread is real and worth stating: when this sandbox's
   DNS for `fonts.googleapis.com` fails fast, LCP is 2.1-2.5 s and the score is
   94-97; when it stalls, LCP is ~6.8 s and the score drops to the 60s-70s.

### Left open

- **The webfont is still a third-party request.** Non-blocking is not the same
  as absent: a machine that cannot reach Google still pays a connection attempt
  and renders in the fallback stack. Self-hosting the three families would make
  the mobile score flat instead of bimodal. That changes what the app looks
  like on a bad network, so it is a design call rather than mine.
- **The two low Lighthouse runs are not explained to the millisecond.** They
  correlate exactly with a stalled font connection, and nothing else in the
  trace moves, but this machine is offline and a real deployment would not
  reproduce the stall.
- **Parameterised routes are not prerendered** -- `/profiles/[name]`,
  `/rankings/[profile]`, `/chains/[profile]` -- because there is no build-time
  list of names. They land on the SPA shell as before. Prerendering them would
  mean enumerating profiles at build time, which couples the build to a store.
- **The clone form is on the Profiles list, not in the editor.** Cloning *this*
  profile from the profile you are looking at would be the more obvious gesture.
- **`min_axis` is not editable** in the constraints block, only shown. It is a
  map of axis to floor rather than a flag, and it needs a different control.

## Part 7 · Arena

Landed, and the brief was wrong about it in a useful way.

**"None has a REST API; each is a download."** LMArena does have one. Hugging
Face's datasets-server serves the same rows as JSON, and its `/filter` endpoint
narrows the text leaderboard from 10,517 rows to the 399 that are the
leaderboard proper. So there is **no parquet reader and no `pyarrow`** — a
40 MB dependency avoided by checking rather than believing the brief.

Live pull, before the fixtures were recorded: **684 models, 1,390 observations**
across llm, text-to-image, image-editing, text-to-video and image-to-video.

Two things this source has that nothing else does:

- **A real publication date.** Every row carries `leaderboard_publish_date`, so
  `observed_at` is when the leaderboard was published rather than when we
  fetched it. Observations are unique on `(model, source, field, observed_at)`,
  so an unchanged leaderboard pulled hourly writes one set of rows instead of
  twenty-four. `aa_llm` still stamps pull time; this is what the fix should
  look like when somebody does it.
- **Effort modes already separated** — `claude-opus-5-high`, `gpt-image-2
  (medium)`. PLAN §2.1a's rule arriving from outside, which is a useful check
  on it. The brackets are folded into the id, because brackets match nothing.

Six of its leaderboards measure `llm` (text, agent, webdev, vision, document,
search) and each gets its own field. Writing them all as `elo` is exactly how
the two music leaderboards overwrote each other in part 2.

**The cache is the ETag.** Each pull asks for the dataset's commit sha first; if
it matches the last one, the pull stops there — one request instead of ten, no
rows written, and a warning saying how to force a full read. It is
`enabled = false` in `sieve.toml.example` and a test pins that: a bulk read of
somebody else's dataset has no business running on every CI push.

`docs/sources.md` is new, and carries the CC-BY-4.0 attribution the licence
requires, with a test asserting it is actually there.

### Two bugs it found on the way

- **A transport failure took the whole run down.** `Http.get` let httpx's
  `ReadTimeout` propagate, so one slow endpoint crashed `sieve run` and nothing
  was re-ranked, from data already on disk. A dropped connection is now a 599
  response, which every source already knows how to report. This is the bug
  behind the part 5 promise that a source being down does not stop the loop —
  that promise was only true for endpoints that answered.
- **`fixture_slug` drops the query string**, so every arena config folded onto
  one recording name and each overwrote the last. The source passes its query
  as `params` now, which is what the `__include_categories_true` suffix on the
  Artificial Analysis recordings was always for.

### Left open

- **LiveBench and Epoch are not started.** Part 7 says to do them in order and
  not to start one until the part before it is pushed and green. Arena is
  pushed and green; the next session starts with LiveBench.
- **Two leaderboards failed on the live pull** — `vision` timed out, `webdev`
  answered HTTP 500. Both were reported and the pull continued. The
  datasets-server `/filter` endpoint builds an index on first call and can take
  over 30 s, which is longer than the default client timeout; a source cannot
  set its own timeout under the current `Source` protocol, and giving it one is
  a contract change nobody has asked for yet.
- **No axis uses an arena field.** The observations are stored and nothing
  scores on them. Adding `data/axes/llm/preference.yaml` over `elo:text` is a
  small change, but it alters every shipped LLM profile's weights, which is a
  judgement about what the seats should value rather than a mechanical step.
- **`categories = false` by default**, so the sub-leaderboards — 3d_modeling,
  multi_image_edit and the rest — are not pulled. They are per-category Elo of
  exactly the kind `aa_media` publishes and would be worth axes.


## Part 8 · The Field screen, honestly

Landed in two commits: A and B as `f777a5e`, C here.

### A · the scatter appears only where cost is answerable

The Field plots a quality axis against cost, so a point needs both. Media
almost never has both — on these recordings 5 of 313 scored media models carry
a price, against 60 of 60 LLMs; on the live box it was 62 of 775. Either
population is far below any threshold worth drawing a scatter at.

`MIN_PRICED_SHARE = 0.25` lives in `sieve/scoring/leaderboard.py` and
`GET /v1/leaderboard` reports `scatter_ok` per modality. The **server** decides,
because it is the side that knows how many models carry a price, and if media
ever crosses the threshold the scatter returns with no code change.

### B · the ranking, where the scatter cannot go

One horizontal bar per model, best first, with the metric each modality
actually publishes. `speech-to-text` gets a sentence instead of a chart:
`aa_wer_index` is published rounded to one decimal, so 44 of 58 read 0.0, and a
chart of ties invents an order.

Duplicates collapse on `(creator, value to ~4 significant figures)`. The brief
said one decimal, which is right for an Elo near 1200 and a tenth of the whole
range on a 0–1 score — it merged three different speech-to-speech models.
Absorbed ids are kept and shown as "N duplicates merged".

### C · two searches, and the effort line

`web/src/lib/field.ts` holds the arithmetic, unit-tested; `FieldSearch.svelte`
holds the two boxes; `Scatter.svelte` draws the line.

- **Provider** lights that provider's models green and dims the rest. Dimmed is
  still drawn: the greys are what make the green mean anything.
- **Model** narrows to one family and draws its modes joined in effort order,
  each node labelled with its **mode** — the model name is identical on all six
  and would be six copies of one word.
- **Cost axis is a choice.** `price per million tokens` is the posted rate, and
  `cost per task` is that mode's own measured output tokens at that rate. A node
  measured from telemetry is a disc; one falling back to a posted price is a
  hollow square, and the key sits beside the switch.
- The screen counts and states, from the data loaded, how many multi-mode
  families charge one rate for every mode. On these recordings 6 of 10; on the
  live box 44 of 58.

### Three bugs it found on the way

- **A fold left the family pointer behind.** `merge_pull` rewrote a model's id,
  aliases, observations, prices and capabilities, but not `family` — so when
  AA's `openai/gpt-5-6-luna` folded onto OpenRouter's `openai/gpt-5.6-luna`,
  every mode of that family went on pointing at an id that no longer existed.
  9 of 33 families, and 4 of the 10 with more than one mode. Nothing could find
  the family's base row, so `inherit_family_capabilities` (843bb74) reached
  none of them and every mode stayed excluded by `require`. Measured: tools
  reached 5 mode rows before, 23 after.
- **The cost axis was a race.** It merged all nine profiles' rankings and kept
  whichever `cost_per_task` arrived first, so the same screen reloaded drew
  different numbers. It now reads one named profile, chosen alphabetically by
  default and changeable, with the shape printed beside it. This matters more
  than it sounds: at `cheap_bulk`'s 2k input the astra modes spread over 5.4x,
  and at `reader`'s 200k they converge to within 6%.
- **The x domain had a hardcoded floor.** `Math.min(...xs, 0.0001)` folded a
  fallback into the minimum, so the axis always reached down to $0.0001 whether
  or not anything was there, and a filtered field stayed squashed against the
  right edge.

### Left open

- **`per million tokens` blends input and output 3 : 1.** An axis needs one
  number and a price list has two. The ratio is declared on screen, but it is
  still a choice, and a profile's own shape would be a better one if the axis
  ever needs to be exact rather than comparable.
- **The effort line is drawn for a provider search too**, one per multi-mode
  family. On a provider with many families that is a lot of lines; `openai` has
  four and it reads well, but nobody has seen it with twenty.
- **`data-cost-domain` on the figure** exists so a test can assert the axis
  rescaled without guessing at canvas pixels. It is inert for a reader, but it
  is a test affordance in shipped markup and worth knowing about.
- **Telemetry in the smoke store is seeded by `web/e2e/seed-telemetry.py`**,
  written straight into the store because `sieve plan --store` computes the
  ranking the Field reads and telemetry arriving after it is not in it. If the
  seed ever grows a server-first ordering, that script should post to
  `/v1/telemetry` instead.
- **Part 9 is next** (card `t_bb756a1d`): the fal tier parser, then deepinfra.
  My fal parser still refuses every tiered price, which PLAN §2.3 contradicts —
  that is 88 more priced media models on Mohamad's measurement, and it is the
  thing that could take media over `MIN_PRICED_SHARE` and bring the scatter
  back rather than leaving it permanently hidden.


## Part 9 job 1 · The fal tier parser

Landed. `parse_rates` reads a price sentence as **clauses**, drops the ones that
are not a rate, and keeps **every** tier of what remains.

    on the recordings          before   after
    media models priced            14      22
    media priced share            6.2%    9.8%    (threshold is 25%)
    price rows written             14      57

The share moved by more than half and the Field's cost scatter is still hidden,
which is the honest headline. Job 2 (deepinfra) is what could move it further:
42 more models, 30 of which fal has no price sentence for at all.

### What changed

- **Clause classification.** A sentence is split on `.!?;` and each clause is
  read or dropped with a named reason: a restatement (`For $1.00, you can run
  this model 12 times`), a rate for a different time (`after which 480p is
  $0.05/second`), an add-on (`an additional $0.015 will be charged`), a
  multiplier (`4K is charged at double the standard rate`), a worked example
  (`For example, a 5s video will cost $0.70`), or a per-step bill.
- **Every tier is stored**, not the cheapest. `Store.latest_prices` picks the
  cheapest and the row names its tier, so a 1080p job is never priced at the
  480p line without saying so.
- **Tiers are read in written order** when there are as many as there are
  amounts. Nearest-first is wrong: `At an output resolution of 480p, every
  second costs $0.05, and at 720p, every second costs $0.07` puts 720p nearer
  the first amount than the 480p it belongs to.
- **Input / cached / output token rates** are one price with three sides, keyed
  by tier (`text tokens`, `image tokens`).
- **Three new units.** `usd_per_compute_second` (stored, named, and deliberately
  not costable against any shape — hardware time is not output length),
  `usd_per_megapixel`, `usd_per_request`. `Shape` gained `megapixels` and
  `requests` to cost the last two.
- **The dollar sign may follow the number** (`0.25 $ for 512p`), and a minute is
  converted to sixty seconds.
- **fal's `speech-to-speech` category is mapped.** Sieve gained the modality in
  part 2 and the category map was written before it existed, so those rows were
  being skipped as "no Sieve modality" long after there was one.

### Two bugs it found

- **`per 1,000 characters` was stored under the per-million unit at the
  published number**, understating every such model by a factor of a thousand.
- **The `prices` uniqueness never fired.** It was
  `UNIQUE (model_id, source, modality, observed_at)`, and SQLite treats NULLs in
  a UNIQUE constraint as distinct from each other — so for every single-modality
  source, which writes `modality = NULL`, re-pulling appended a duplicate row
  per model instead of being ignored. Migration 0007 makes it a unique index
  over `COALESCE`d columns, with `tier` in the key.

### The trade this makes, stated plainly

Sixteen models are newly priced and **eleven are no longer priced** — net five.
The eleven were being read wrongly: `$0.112 (audio off) or $0.168 (audio on)`
was stored as $0.112, the cheaper of two rates, with nothing recording that a
choice had been made. Under this project's own rule a wrong price is worse than
no price, so they are refused and counted. If that trade is wrong, the fix is a
tier vocabulary that can name "audio on", not a parser that guesses.

### Left open

- **Unnamed alternatives are refused**, and there are two shapes of them: a
  parenthetical qualifier (`(audio off)`, `(with HD textures)`) and one price
  covering two tiers (`$0.15 with audio for 720p or 1080p`). The first could be
  read by widening the tier vocabulary to parentheticals; the second cannot be
  read at all without asking fal.
- **`per 1,000 <kind> tokens`** — `You will be charged $0.005 per 1,000 input
  text tokens` — is refused. It is the per-1M token shape at a different scale
  and a different layout; two models here, nine on the live catalogue.
- **A parenthetical decomposition is refused**: `$0.022 per image ($0.02 for
  image output + $0.002 for image input)`. The parts sum to the total, so it is
  checkable arithmetic rather than a guess, and it is worth doing if the shape
  turns out to be common.
- **Multiplier tiers are not expanded.** `4K outputs will be charged at double
  the standard rate` gives the base rate the tier `standard` and stops. The 4K
  price is derivable and is not derived, because pairing `2K and 4K` with `1.5
  times and 2 times ... respectively` is ordered prose and getting it backwards
  bills a job at half.
- **No profile declares `megapixels` or `requests` yet**, so a model priced in
  those units has a cost the engine reports as unmeasured. That is correct
  behaviour and it is also a gap: the shapes want filling once a media profile
  is meant to rank on cost.
- **Job 2, deepinfra, is not started.** Card `t_bb756a1d` carries the field map:
  `GET https://api.deepinfra.com/models/list`, no key, 116 media models, every
  price machine-readable and **in cents**.


## Part 9 job 2 · deepinfra, a second price source

Landed. `sieve/sources/deepinfra.py`, registered as `deepinfra`, no key, one
request, recorded as `api_deepinfra_com_models_list.json` (120 of 372 rows kept:
all 116 media plus four others so a test can prove the others are skipped).

    on the same store and config     fal only   + deepinfra
    media models scored                   349          368
    media models priced                    22           49
    media priced share                   6.3%        13.3%     threshold 25%

His numbers checked out exactly before anything was built on them: 372 models,
116 media, and the per-type counts to the model.

### What it reads

`pricing.type` is the discriminator and every rate is in **cents** — the one
thing about this API that will bite somebody, since reading a rate as dollars
overstates a model by a hundred and nothing about the resulting ranking looks
wrong. The scale is asserted against deepinfra's own published price for a model
anyone can check (`gemma-2-9b-it`, $0.03/1M in).

`pricing.short` and `pricing.full` are ignored: they restate in English what is
already in the object as a float.

A rate of exactly zero is not a price — three text-to-speech models publish
`cents_per_input_chars: 0.0` — and `cents_per_frame_unit` is skipped, because a
frame is not a second without a frame rate and the response publishes none. Both
are counted in warnings.

### The modality is a claim

deepinfra's `text-to-image` covers `Wan2.6-Image-Edit` and
`Bria/remove_background` beside `FLUX-1-dev`; its `text-to-video` covers image
conditioning beside text conditioning. Nothing in the response separates them,
so PLAN §2.2 applies: the row is offered under both candidates and kept only
where the catalogue already holds that id **in that modality**. 32 of 116 are
kept and 60 are dropped with a count.

That surfaced a bug in `confirm_provisional`: it compared ids alone, so an id
the catalogue held as text-to-image confirmed a claim that it was
image-editing — the one thing that function exists to prevent, and the thing its
own docstring already said it did. It now compares `(id, modality)`.

### Two sources, one model, two prices

Both rows are kept, distinguished by `Price.source`, and nothing averages them.
A ranking uses the cheapest known price. `sieve check` prints a **note** — not a
failure — for any model the two price more than 3x apart in the same unit,
comparing input against input and flat rate against flat rate.

It earned its place on the first run. `google/veo-3-1-fast` is priced 5x apart,
and the reason is not a margin: fal's `veo3.1/lite` endpoint folded onto the
`veo-3-1-fast` id, so a cheaper model's price is sitting on a dearer model's
row. Both numbers are true about *something*; only one is true about that id.

### Left open

- **The `veo3.1/lite` -> `veo-3-1-fast` fold is wrong** and is now visible. The
  matcher folded two different endpoints of one family onto one id. Fixing it is
  a matcher change, not a source change, and it wants its own look.
- **`google/nano-banana-2-lite` is priced 96x apart on input tokens** — fal
  reads Google's own $0.3125/1M, deepinfra charges $30/1M. That is either a real
  vendor difference or another bad fold, and the note says which model to look
  at rather than deciding.
- **60 provisional rows are dropped** because the catalogue does not know them
  in the claimed modality. Most are genuinely new models that only deepinfra
  carries; they are unrankable either way, having no quality score, but they
  would be worth keeping if a source ever scores them.
- **deepinfra publishes `cents_per_output_token` for 219 text-generation
  models.** This source ignores `text-generation` entirely, because OpenRouter
  already covers LLM prices and a second llm price source is a different
  decision from a second *media* one.
- **The media priced share is 13.3% and the threshold is 25%.** The scatter
  stays hidden. Between them the two jobs roughly doubled it; closing the rest
  needs either a third source or a threshold somebody argues for on the merits.

## Part 1 · Real data replaces the invented fixtures

Landed. Ten recordings from 2026-09-08 sit in `tests/fixtures/` under the exact
name `fixture_slug(url, params)` produces — the five arena files carry the
`__include_categories_true` suffix, because that is the request that made them.
`RECORDINGS.json` holds the URL, params, date, rows published and rows kept for
each, and a test asserts the files still match it. The hand-built
`artificialanalysis_ai_*` files are deleted.

Phase 1's note said the tests "should keep passing unchanged" against real
recordings. They did not, and every failure was the fixture having flattered the
code:

- **Per-category Elo never worked.** The arena endpoints return `categories` as
  a list whose name sits in one of three columns — `format_category`,
  `style_category`, `subject_matter_category` — and the parser looked for a
  `name`/`category`/`slug` key. It found none and silently produced nothing.
  Fixed, and each category now carries its own `appearances` and `ci95` rather
  than the model's overall pair: a model can have 1,056 votes on Physics and 392
  on Moving camera. text-to-video and image-to-video publish 29 categories each,
  text-to-image 13.
- **Four axes named a category that does not exist.** `elo:anime` and
  `elo:photoreal` for text-to-video (really `cartoon_and_anime` and
  `photorealistic`), `elo:nature` and `elo:text` for text-to-image (really
  `nature_landscapes` and `text_typography`). An axis whose field never appears
  is not an error anywhere, so it would have sat at coverage 0 for ever.
- **The arena endpoints publish no price at all.** The invented fixtures had
  one. So every media `cost` axis is unmeasured from this source, and a media
  profile weighting cost loses that coverage honestly. A test pins that no price
  is invented.
- **`pareto_prune` fires.** 29 of 60 models are dominated on `coder`. Phase 1
  reported it as implemented but never firing; that was the six-model fixture,
  not the code. Two tests now check it, one of them re-deriving every domination
  the pruner claims.
- **`:batch` matched nothing.** Twelve OpenRouter ids are `<model>:batch`, and
  the tag rule stripped `:free` and `:nitro` but not that. Added.

Still open, deliberately:

- **image-editing and text-to-speech publish no categories**, even though
  `include_categories=true` is sent to all five arena endpoints. They therefore
  cannot have category axes. Pinned by a test so it is visible if it changes.
- **image-to-video has 29 published categories and no axes at all.** Nothing is
  broken; nobody has decided which of the 29 are worth choosing a model on.
- **text-to-image categories are sparse** — `fantasy_mythical` covers 17 of 40
  models, `anime` 11, and eight others cover exactly 1. Any axis over the thin
  ones is nearly all coverage loss, which is correct but close to useless.
- **`:batch` shares its canonical model with the standard deployment**, so the
  two OpenRouter prices for one model now collide on `(model, source)`. Which
  tier's price survives is whichever is added last. It needs a price-per-tier
  key, or `:batch` needs to stay unmatched; the current answer is neither.
- **`median_time_to_first_answer_token`** is published by the LLM endpoint and
  the source stores nothing for it. Only the older
  `median_time_to_first_token_seconds` is mapped.
- **`aa_llm` still stamps `observed_at` with the pull time.** Against the live
  API every hourly pull will add a full set of rows. The recording carries no
  date field to use instead, so this needs the live response to settle.
- **The alias pass found almost nothing to add**, which is the honest result:
  of 60 OpenRouter ids, 1 matched exactly, 13 by a rule, and the other 46 are
  models one catalogue carries and the other does not. The two recordings are
  trimmed independently, so their overlap is small by construction — not a
  matching failure. `data/aliases.yaml` records the pass and its date.
