# D — web Profiles, Chains, client-side rank (opus)

You own `web/src/routes/(app)/{profiles,chains}/**`, `web/src/lib/rank/**`,
the components `WeightSlider`, `Chip`, `ChainCard`, `Diff`, `Timeline`, and
`web/tests/**`. Read CONTRACTS §3, §6, §8 first. Use C's scaffold, tokens
and `lib/api/client.ts`; if C has not landed yet, build against the
contract and rebase.

## Client rank (`lib/rank/weigh.ts`)

A TypeScript port of `sieve/scoring/weigh.py`: given axis values per model
(from `/v1/rankings/{p}` rows, which carry `axes[].value` and `coverage`)
and a weight vector, return score, confidence and contributions. A vitest
asserts it reproduces `tests/fixtures/rank_case.json` exactly (order and
scores to 1e-6). This is what makes the sliders feel instant; the server's
`evaluate` is the truth when the user clicks Evaluate.

## Profiles (`/profiles`, `/profiles/[name]`)

Index: cards grouped by modality — name, purpose, primary model, incumbent
since, a mini bar of the weight vector. "New profile" clones one.

Editor, two columns. Left: name/purpose, `WeightSlider` per axis of the
modality (weights renormalise to 1 as one moves; show 0.00–1.00 and lock
toggles), constraint `Chip`s (tools, reasoning, ctx ≥ n, input modalities,
min_appearances for media, min_axis floor), shape fields for the modality,
policy fields (margin, tenure, chain, min_confidence, suspend threshold,
auto_apply, require_telemetry), targets multiselect. Right: the ranked list
re-ranking live with FLIP (Motion `animate` on layout change), row 1
outlined in accent, rows past `chain` dimmed, and a "why" line: gap #1 to
#2, whether it clears the margin, the axis carrying most of the gap, models
excluded by constraints. Buttons: **Evaluate** (POST `/evaluate`, shows the
server's ranking and decision beside the client's — they must agree), **Save**
(PUT with a `profiles:write` token; show the decision it logged), **Reset**.

## Chains (`/chains`, `/chains/[profile]`)

`ChainCard` per profile: primary + fallbacks with reach dots, health
sparkline, incumbent tenure. `Diff` against each of the profile's targets
(`current()` vs computed): added / removed / moved rows with colours from
the tokens. Apply bar: "Apply writes N chains to <target names>" → POST
`/v1/apply` with an `apply` token; confirmation names the targets; result
per target. `Timeline`: `/v1/decisions?profile=` newest first, kind as a
pill, reason verbatim, actor.

## Tests (`web/tests`)

vitest: `weigh.ts` fixture equality; slider renormalisation keeps the sum
at 1; FLIP does not run under reduced motion. Playwright smoke: open
`/profiles/coder`, move one slider, see the list reorder, click Evaluate,
see the server decision text.

## Done when

`pnpm lint`, `pnpm check`, `pnpm test`, Playwright smoke green; editor
re-ranks under 16 ms per input on a 60-row list; Save and Apply show the
401 path cleanly without a token.
