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
| `fal` | **Prices** for media models | no | terms of use |
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

### fal — `fal`

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
