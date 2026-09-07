# Add an axis

An axis is the vocabulary a profile speaks. A profile never names a benchmark;
it says `agentic_coding: 0.45` and the axis file decides what that means from
whatever the sources measured.

Adding one is adding a file.

## The file

`data/axes/<modality>/<name>.yaml`:

```yaml
name: agentic_coding
modality: llm
label: "Agentic coding"
describes: "end-to-end software tasks in a terminal or a repository"
fields:
  - {source: aa_llm, field: terminalbench_v2_1, weight: 0.5}
  - {source: aa_llm, field: terminalbench_hard,  weight: 0.2}
  - {source: aa_llm, field: artificial_analysis_coding_index, weight: 0.2}
  - {source: aa_llm, field: livecodebench, weight: 0.1}
  - {source: livebench, field: coding, weight: 0.3, phase: 2}
missing: renormalise
min_coverage: 0.5
higher_is_better: true
```

`name` and `modality` default to the file name and its directory, so both are
optional. `sieve check` validates it.

## The fields that decide behaviour

**`weight`** — relative within the axis. They do not have to sum to anything;
only profile weights do.

**`transform`** — `identity`, `neg_log`, `log` or `invert`. Cost and latency
want `neg_log`: the difference between $0.10 and $1.00 matters far more than
between $10 and $11, and negating makes cheap rank high.

**`min_n`** — ignore a measurement with too few samples. An arena Elo from 12
votes is not a measurement; `min_n: 500` makes it *unmeasured* rather than
believed.

**`missing`** — `renormalise` judges a model on the weight that was measured;
`penalise` scores an absent field at the 20th percentile instead. Use
`penalise` when not being measured is itself a bad sign.

**`min_coverage`** — below this share of measured weight the axis reports
nothing for that model. Unmeasured, not bad.

**`phase: 2`** — the field is ignored until its source publishes something, and
starts counting on its own the day it does. That is how an axis can name a
benchmark before the connector exists.

## A category axis

Media sources publish per-category Elo, stored as `elo:<slug>`:

```yaml
name: moving_camera
modality: text-to-video
label: "Moving camera"
describes: "Elo within the Moving camera category only"
fields:
  - {source: aa_media, field: "elo:moving_camera", weight: 1}
```

## Then use it

A profile may only name an axis that exists for its modality — `sieve check`
fails otherwise, which is the point: a typo in a weight name would silently
score every model zero on something.

```yaml
weights:
  agentic_coding: 0.45
  cost: 0.2
  ...            # profile weights must sum to 1 ± 0.001
```

## Check your work

```bash
sieve check                      # every axis, profile and alias file
sieve score --profile coder      # contributions and coverage, per model
```

If an axis reads `unmeasured` everywhere, the usual cause is a field name that
does not match what the source stored. `sieve pull` warns about field names it
did not recognise, and `GET /v1/models/{id}` shows every observation a model
has, under the exact names to use.
