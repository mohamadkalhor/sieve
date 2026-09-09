# Where the numbers come from

Sieve measures nothing itself. Every number in a ranking was published by
somebody else, and this page says who, under what licence, and what each one is
good for — because a weighted score is only as honest as its inputs, and an
axis pointed at the wrong source is a confident wrong answer.

## The sources

| source | measures | key | licence |
| --- | --- | --- | --- |
| `aa_llm` | LLM benchmarks: intelligence, coding, maths, agentic indices, latency, price | yes | terms of use |
| `aa_media` | Human preference Elo for image, video and speech, per category | yes | terms of use |
| `openrouter` | Prices, context windows, capabilities for LLMs | no | terms of use |
| `aa_media_prices` | **Prices** for media models, from the leaderboards | no | terms of use, citation required |
| `fal` | **Prices** for media models | no | terms of use |
| `deepinfra` | **Prices** for media models, machine-readable | no | terms of use |
| `arena` | **Human preference Elo**, text and media | no | **CC-BY-4.0** |
| `manual` | Whatever you measured yourself | — | yours |

### Artificial Analysis — `aa_llm`, `aa_media`

<https://artificialanalysis.ai>. The benchmark spine: 644 LLMs with per-evaluation
scores, and 482 media models with per-category Elo. Needs
`ARTIFICIAL_ANALYSIS_API_KEY`; the free tier allows 1,000 requests a day, and
`docs/the-loop.md` has the arithmetic showing an hourly run uses 144 of them.

**It publishes no media prices at all.** That is not an oversight to route
around — it measures quality and leaves cost to somebody else, which is why
`fal` exists in this list.

### OpenRouter — `openrouter`

<https://openrouter.ai>. No key. Prices, context windows, tool and reasoning
support for ~428 LLMs — the only key-free answer to "can this model actually do
what the profile requires". It republishes a subset of Artificial Analysis's
scores, and Sieve deliberately **does not** store those: AA is the source of
AA's numbers, and keeping them twice would double their weight in an axis.

### Artificial Analysis media prices — `aa_media_prices`

The v2 data API returns nine fields for a media arena and none of them is a
price, `include_prices=true` included. **The leaderboard pages carry one on
every row.** PLAN §2.3 said the opposite for a day, on the strength of checking
the API and not the page.

This makes Artificial Analysis the primary media price source, and a better one
than any marketplace: the price arrives on the same row as the score, joined by
the same uuid. `aa_media` already stores that uuid as an alias, so the fold is
exact — no name matching at all.

| board | key | stored as | priced |
| --- | --- | --- | --- |
| `/image/leaderboard/text-to-image` | `pricePer1kImages` | `usd_per_image` | 143 |
| `/image/leaderboard/editing` | `pricePer1kImages` | `usd_per_image` | 68 |
| `/video/leaderboard/text-to-video` | `pricePerMinute` | `usd_per_second` | 86 |
| `/video/leaderboard/image-to-video` | `pricePerMinute` | `usd_per_second` | 72 |
| `/text-to-speech` | `pricePer1mCharacters` | `usd_per_1m_chars` | 77 |
| `/speech-to-text` | `pricePer1kMinutes` | `usd_per_second` | 47 |
| `/video/leaderboard/video-editing` | `pricePerMinute` | `usd_per_second` | 9 |
| `/speech-to-speech` | `pricePerHourInput`/`Output` | `usd_per_second` | 32 |

Measured 2026-09-09. Together they take the media priced share from 13.4% to
**53.0%**, which is what brings the Field's cost scatter back for six of the
eight media modalities without touching the threshold.

#### It reads a page, so it is built to fail loudly

Next.js streams its data as `self.__next_f.push([1,"<chunk>"])` — JSON inside a
JS string literal inside HTML. Unescaping once gives a payload whose row objects
decode with `json.loads`. **Nothing here pattern-matches a number.**

There is deliberately no anchor on a prop name. The first version looked for
`hostModels`, and the speech boards turned out to publish one set of rows four
times over — `stsIndexHostModels`, `costPerHourOfInputAudioHostModels`,
`pricingHostModels`, `tauChartModels` — each a different subset, none of them
under the name being looked for. Anchoring on the price key finds every copy and
cannot go stale when a chart is renamed.

Two guards, because a page is a weaker contract than an endpoint:

- a board whose shape has moved returns `ok=False` and the run goes **red**;
- `sieve check` **errors** on an enabled board that has priced nothing while its
  siblings have — a board that quietly stopped producing on some earlier run.

#### The unit is never converted across a modality

Per 1k images, per minute, per 1M characters and per 1k minutes are four
different things. A thousand images is a thousand images and a minute is sixty
seconds, so those conversions are arithmetic — but the *units* stay apart, and
because `cost_per_task` reads `shape.images` for one and `shape.seconds` for
another, comparing an image price with a video price is impossible by
construction rather than forbidden by convention.

`priceDisplayOverride: "no_api"` means the model has no public API. Whatever
number sits in the price field, nobody can pay it, so the row is skipped and
counted.

#### It also takes what the v2 API does not publish

`winRate` — the share of head-to-head wins — appears on the image boards and
nowhere in the API, so it is stored everywhere it is offered, as a fraction.

**Elo is taken from exactly one board: `video-editing`.** Everywhere else
`aa_media` already stores the same number from the documented endpoint, and one
organisation's single measurement must not arrive twice under two source names —
an axis reading both would count it twice. The v2 API has no video-editing arena
at all, so the page is the only place that Elo exists, which is what makes the
modality rankable.

`ciDelta` is the **half-width**: a row with Elo 1178.11 carries ciLower 1168.11
and ciUpper 1188.11 against ciDelta 10. That is the same thing `ci95` means
elsewhere in the store, so the two agree. Reading it as the full width would
make every interval twice as wide and every model look half as settled.

#### Music has no price on its board

`/music/leaderboard/instrumental` and `/music/leaderboard/vocals` carry no price
key at all, so music keeps the ranking and no cost axis. It is the one media
modality a marketplace is still the only answer for.

Two corrections to what was expected there: the with-vocals board is at
`/music/leaderboard/vocals`, not `/with-vocals`, and **there are no per-genre
leaderboards.** No `/music/leaderboard/<genre>` URL resolves, and the payload
carries no genre split — the repeated Elo blocks are the same nineteen models
once per sub-board, and the labels that look like genres are creator names.

#### Attribution

The pages embed a schema.org Dataset carrying the licence and the citation
Artificial Analysis asks for. Both are reproduced here because this is a public
tool built on their measurements:

> Artificial Analysis (2025). LLM benchmarks dataset.
> <https://artificialanalysis.ai>

Terms of use: <https://artificialanalysis.ai/docs/legal/Terms-of-Use.pdf>

### fal — `fal`

**Off by default since part 10.** Artificial Analysis prices media on the same
row as the score, so a marketplace price is only wanted where AA has none. The
code, the tests and the recordings all stay, and turning it on is one word: it
is the answer for music, which AA's board does not price at all, and for models
that never join the catalogue.


<https://fal.ai>. No key. 1,494 media models with prices and **no quality score
whatsoever**, because a generation marketplace never judges quality. Joined with
Artificial Analysis it makes a media model rankable on both axes; alone, neither
side can.

Its prices are English prose written for a person reading a model card
(`Your request will cost **$0.08** per image`), so the parser reads only the
forms it can prove and leaves the rest blank and counted. A wrong price does not
merely mis-rank a model — it recommends the wrong one and bills for it.

#### Several numbers in a sentence is not a reason to refuse it

PLAN §2.3. Most fal price sentences carry more than one amount, and the parser
used to refuse all of them. Most of those sentences are perfectly readable once
the clauses that are **not** a rate are set aside:

| The clause | Why it is not a rate |
| --- | --- |
| `For $1.00, you can run this model 12 times` | the same price as arithmetic |
| `after which 480p is $0.05/second` | a rate for a different time |
| `an additional $0.015 will be charged` | an add-on, not the rate |
| `4K outputs are charged at double the standard rate` | a multiplier |
| `For example, a 5s video will cost $0.70` | a worked example |
| `$0.003 per step` | a count the source does not publish |

What is left is read, and **every tier is kept**:

```
Video costs $0.0125 per second at 480p, $0.02 at 768p, $0.04 at 1080p.
  -> three prices, three rows, one per tier
Text tokens (per 1M): $5.00 input, $1.25 cached, $10.00 output.
  -> one price with three sides, tier "text tokens"
Your request will cost 0.25 $ for 512p, 0.3 $ for 1024p, 0.35 $ for 1536p.
  -> three prices; the dollar sign may follow the number
```

**Tiers are read in the order they are written**, not by proximity. `At an
output resolution of 480p, every second costs $0.05, and at 720p, every second
costs $0.07` puts the tier before its rate, and the *nearest* tier to the first
amount is the wrong one.

#### The default tier, declared

A ranking needs one price per model, so `Store.latest_prices` returns the
**cheapest** tier and the row carries its `tier` name. On fal's catalogue the
cheapest is always the lowest resolution offered — checked against every tiered
model in the recordings. A model that is cheap at 480p and dear at 1080p is
therefore never *silently* cheap: the tier is on the row and on the screen.

#### Per compute second is not per second of generated output

`$0.00111 per compute second` bills hardware time, which varies with the job.
`$0.025 per second of generated video` bills output length, which does not.
They are stored under different units (`usd_per_compute_second` and
`usd_per_second`), and a compute-second price is deliberately **not costable**
against any profile shape — `cost_per_task` returns None for it. Costing it
against `shape.seconds` would put an upscaler and a video model on one axis at
numbers that do not measure the same thing.

#### What it still refuses, and why

Alternatives nothing names. `$0.112 (audio off) or $0.168 (audio on)` is two
prices and the sentence does not say which applies; so is `$0.04 per image (or
$0.08 if you are using a vector style)`. The old parser took the first number in
each and called it the price — which is how eleven models were priced at the
cheaper of two rates. Refusing them loses eleven models and gains sixteen read
correctly, and that is the trade this source exists to make.

Measured on the recordings: **14 media models priced before this rule, 22
after** — a media priced share of 6.2% rising to 9.8%, still well under the 25%
the Field needs before it will draw a cost scatter.

### deepinfra — `deepinfra`

**Off by default since part 10**, for the same reason as fal above.


<https://api.deepinfra.com/models/list>. No key, one request. Measured on
2026-09-09: 372 models, 116 of them media, and **every one of the 116 carries a
machine-readable price**. It is not a rival to fal, it is a complement — the two
catalogues barely overlap, and deepinfra hosts models fal has no price sentence
for at all.

**Every rate is in cents.** `cents_per_output_sec: 5.0` is five cents a second,
not five dollars. Reading one as dollars overstates a model by a hundred and
nothing about the ranking would look wrong, which is why the scale is asserted
against deepinfra's own published price for a model anyone can check
(`gemma-2-9b-it`, $0.03/1M in).

| the field | the unit | how |
| --- | --- | --- |
| `cents_per_image_unit` | `usd_per_image` | ÷ 100 |
| `cents_per_output_sec` | `usd_per_second` | ÷ 100 |
| `cents_per_sec`, `cents_per_input_sec` | `usd_per_second` | ÷ 100 |
| `cents_per_input_chars` | `usd_per_1m_chars` | × 10,000 |
| `cents_per_input_token` | `usd_per_1m_tokens`, as `input` | × 10,000 |
| `cents_per_frame_unit` | — | skipped and counted |

`pricing.short` and `pricing.full` are English restatements of numbers that are
already in the object as floats, and they are **ignored**. Parsing prose when a
float is offered is how a parser earns a wrong price it did not need.

A rate of exactly zero is not a price. Three text-to-speech models publish
`cents_per_input_chars: 0.0`; stored, that puts them at the top of every cost
ranking on the strength of a field nobody filled in.

`cents_per_frame_unit` is skipped because a frame is not a second without a
frame rate and the response publishes none.

**Its categories span our modalities.** `text-to-image` covers
`Wan2.6-Image-Edit` and `Bria/remove_background` beside `FLUX-1-dev`, and
nothing in the response separates generation from editing. So PLAN §2.2 applies:
the row is offered under both candidates and kept only where the catalogue
already holds that id in that modality. On the recordings that keeps 32 of 116
and drops 60 with a count.

### Two sources, one model, two prices

A price is **one vendor charging to run one model**, not the price of the model.
fal and deepinfra host some of the same models at different rates, so:

- both rows are kept, distinguished by `Price.source`, and nothing averages them
- a ranking uses the cheapest known price
- `sieve check` prints a **note** — not a failure — for any model the two price
  more than 3x apart in the same unit, comparing input against input and flat
  rate against flat rate

That last one earns its place immediately. It reports `google/veo-3-1-fast`
priced 5x apart, and the reason is not a margin: fal's `veo3.1/lite` endpoint
folded onto the `veo-3-1-fast` id, so a cheaper model's price is sitting on a
dearer model's row. Both numbers are true about *something*; only one of them is
true about that id.

### LMArena — `arena`

<https://lmarena.ai>, dataset `lmarena-ai/leaderboard-dataset` on Hugging Face.

> **Attribution.** The LMArena leaderboard dataset is published by LMArena
> (lmarena-ai) under the **Creative Commons Attribution 4.0 International**
> licence (CC-BY-4.0). Sieve reads it unmodified and stores the ratings it
> publishes. <https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset>

Every other source is a benchmark: a fixed set of questions, scored. This one is
people choosing between two answers, hundreds of thousands of times. It measures
what a benchmark structurally cannot — whether the output was the one somebody
actually wanted — which is why it is worth a separate axis rather than being
averaged into a quality score.

Two things it has that nothing else does:

- **A real publication date.** Each row carries `leaderboard_publish_date`, so
  `observed_at` is when the leaderboard was published rather than when Sieve
  fetched it. Observations are unique on
  `(model, source, field, observed_at)`, so an unchanged leaderboard pulled
  hourly writes one set of rows rather than twenty-four.
- **Effort modes already separated.** Its rows are `claude-opus-5-high`,
  `gpt-image-2 (medium)`. PLAN §2.1a's rule — an effort mode is a different
  model — arriving from outside, which is a useful check on it.

**It is off by default**, and should stay off in CI. It is a bulk read of
somebody else's dataset across about ten requests. Turn it on in `sieve.toml`:

```toml
[sources.arena]
enabled = true
cache = "data/cache"     # holds the dataset's commit sha
categories = false       # true also pulls every sub-leaderboard
```

The cache is the important part. Each pull asks Hugging Face for the dataset's
commit sha first; if it matches the last one, the pull stops there — one request
instead of ten, and no rows written. Delete `data/cache/arena-revision.json` to
force a full read.

Six of its leaderboards measure `llm` — text, agent, webdev, vision, document,
search — and each gets its own field (`elo:text`, `elo:agent`, …). They are
different contests, and writing them all as `elo` is precisely how the two music
leaderboards silently overwrote each other before phase 2 part 2.

`video_edit` and the `*_style_control` variants are **not** pulled: Sieve has no
modality for video-to-video, and calling it `text-to-video` would misrepresent
what the votes were cast on.

### Manual — `manual`

CSV or JSON you drop in `data/observations`. For a benchmark you ran yourself,
an internal eval, or a number somebody gave you. It is a first-class source: an
axis cannot tell where a measurement came from, only how well covered it is.

## Adding one

`docs/add-a-source.md` has the walkthrough. The short version: implement the
`Source` protocol, register an entry point, and record a real response into
`tests/fixtures/` so the tests never touch the network.

Two rules worth repeating, because both were learned the expensive way:

- **A measurement that is missing must stay missing.** Never substitute a zero.
  The axis reports the coverage loss and the confidence falls, which is a true
  statement about what is known.
- **Never invent a number to fill a shape.** Phase 1's Artificial Analysis
  fixtures were written by hand to the documented shape because no key was
  available, and every one of the five things they got wrong stayed hidden until
  a real recording replaced them.
