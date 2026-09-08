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
