# How a model gets picked

A worked example, from a number a benchmark published to a chain a gateway
receives. The arithmetic is PLAN section 5; this is the same thing with the
edges explained.

## 1. Percentile, so different scales can meet

An axis usually mixes measurements that share no units at all: an Elo of 1288, a
GPQA accuracy of 0.82, an index of 73. Sieve keeps only the **order**. Within
one modality's pool, each field is turned into a percentile — best 1.0, worst
0.0, ties sharing a rank.

That has a useful consequence: the axis is unchanged by any monotone transform
of the source's numbers. If Artificial Analysis rescales an index from 0–100 to
0–1 tomorrow, no ranking moves.

A field a source did not publish stays `None` all the way through. It takes no
rank and shifts nobody else's.

## 2. Transforms, before ranking

| transform | what it is for |
|---|---|
| `identity` | a score where higher is already better |
| `neg_log` | **cost and latency**: the gap from $0.10 to $1.00 matters far more than the gap from $10 to $11, and negating makes cheap rank high |
| `log` | a count with a long tail, like arena appearances |
| `invert` | a plain reversal, for a number where lower is better and the scale is linear |

`log` and `neg_log` of zero or less have no meaning, so the value is reported
**unmeasured** rather than clamped. A free model is not infinitely good.

## 3. An axis: value and coverage

```yaml
fields:
  - {source: aa_llm, field: terminalbench_v2_1, weight: 0.5}
  - {source: aa_llm, field: terminalbench_hard,  weight: 0.2}
  - {source: aa_llm, field: artificial_analysis_coding_index, weight: 0.2}
  - {source: aa_llm, field: livecodebench, weight: 0.1}
missing: renormalise
min_coverage: 0.5
```

Say a model was measured on the first two fields only.

- `missing: renormalise` — it is judged on the 0.7 of weight that exists:
  `value = (0.5·p₁ + 0.2·p₂) / 0.7`.
- `missing: penalise` — the absent fields score at the 20th percentile instead,
  and the total weight stays 0.7 in the denominator.

Either way `coverage = 0.7`. Below `min_coverage` the axis reports `None` — the
axis is *unmeasured for that model*, which is not the same as being bad at it.

A field marked `phase: 2` is skipped until its source publishes something, then
starts counting on its own. That is how an axis can name a benchmark before the
connector for it exists.

## 4. Cost is the odd one out

Every other axis is a property of the model. Cost is a property of *the model at
your shape*:

```yaml
shape: {in: 30000, out: 4000, cached: 0.5}
```

30k in with half of it cached, 4k out. At $3/$15 per million with cached input
at $0.30:

```
15000 × $3.00 / 1e6  +  15000 × $0.30 / 1e6  +  4000 × $15.00 / 1e6  =  $0.1095
```

A `reader` profile paying for 200k of input and a `quick_chat` paying for 1.5k
therefore get genuinely different cost rankings from the same price list. The
engine derives this per profile and feeds it in as `price:per_task`, which the
cost axis reads like any other field.

## 5. Score and confidence

```
score(m,p) = Σ  W_a · axis_a(m)          over the axes the profile weights
conf(m,p)  = Σ  W_a · coverage_a(m)
```

An axis that is unmeasured for a model contributes 0 to both. That is not a
silent zero: the loss is visible in `confidence`, and a model below
`policy.min_confidence` is set aside with `excluded_by: min_confidence` — named,
not quietly ranked low.

An exact tie is broken by model id, ascending. Without that rule two runs over
identical numbers could disagree and log a switch that means nothing.

## 6. The order of operations

1. **Constraints.** `tools`, `reasoning`, `context_min`, `input_modalities`,
   `min_axis`, `min_appearances`. A model that fails one is excluded by that
   constraint's name.
2. **Pareto pruning**, among reachable models only. If B is at least as good as
   A on every weighted axis and strictly better on one, no weight change can
   ever put A above B, so A is labelled "dominated by B". An axis where either
   side is unmeasured is skipped — absence of evidence must not prune a model
   for being new.
3. **Score**, then the **confidence floor**.
4. **Health**: `final = score × health`.
5. **Rank.**

## 7. Health

```
health = 1 − error rate(24h) − ½ · rate-limit rate(24h)
```

With no telemetry, health is 1.0: Sieve does not punish a model for the absence
of evidence. A profile that wants proof sets `policy.require_telemetry`.

A 429 counts in the rate-limit term **only**. Counting it as an error as well
would make a throttled model score worse than one that failed outright, which is
backwards — the queue is the gateway's, and it clears.

## 8. Whether the top pick may change

The incumbent keeps the seat unless one of these is true:

- a challenger clears `margin` points on a 0–100 scale;
- the incumbent's tenure exceeds `max_tenure_days`, at which point the margin is
  waived and the seat is re-contested;
- the incumbent's health has fallen below `suspend_below_health`.

Every evaluation writes a decision row, including the ones where nothing
happened:

```
hold:    challenger z-ai/glm-5.3 +1.4 inside margin 3.0
switch:  anthropic/claude-opus-5 over z-ai/glm-5.3 by +4.2, margin 3.0 (agentic_coding +0.31)
switch:  tenure 15 d > 14 d, margin waived, openai/gpt-5.2 leads by +0.1
suspend: z-ai/glm-5.3 health 0.61 < 0.75, anthropic/claude-opus-5 takes primary
```

Without hysteresis two models within noise of each other trade places every
hour, every trade rewrites a gateway config, and nobody trusts the tool.

## 9. The flip line

Position 1 carries one sentence naming the smallest single weight change that
would put position 2 on top, with the other weights renormalised:

```
raise cost to 0.31 and z-ai/glm-5.3 leads
```

If no single weight can do it, the line says so — which is the more useful
answer, because it means the lead does not hinge on one opinion.

## 10. Checking any of this

`tests/fixtures/rank_case.json` is 12 models over six axes with hand-set values
and the expected result. `tests/test_scoring.py` asserts the Python against it
and `web/tests/weigh.test.ts` asserts the TypeScript in the browser against the
same file, to 1e-6. They have to agree, or the profile editor lies about what
saving would do.
