# EFFORT — a seat runs at one reasoning effort, and Sieve scores it there

Rev 1, 2026-10-02. Mockup: `briefs/effort/design.html` (open it in a browser;
it is the UI spec — pill looks, control placement, inspector ladder, phone).
This file outranks the mockup where they disagree.

## 0. The bug this fixes

AA publishes one row per effort and Sieve keeps them apart (PLAN 2.1a,
`sieve/catalog/effort.py`). A router id that names no effort —
`cx/gpt-6-astra` — matches the family's **bare** row, which is its top mode
(max). Hermes then calls the model at the profile's `reasoning_effort`
(`build` runs medium). So the seat is ranked on effort it never uses:
GPT-6 Sol intelligence 47.6 at max, 39.8 at medium. 15 live LLM seats ship a
multi-effort model; 11 have one first.

## 1. The one rule

**A row's id never changes with effort.** `Rank.model_id`, `local_ids`, pins,
removals, manual lists, chains and 9router combos stay keyed on the model the
router reaches (the matched id). Effort only changes **which catalogue row's
observations are read to score it**. Switching a seat medium → high must not
lose a pin and must not change the router ids shipped.

## 2. Types (`sieve/contracts.py`, mirror in CONTRACTS.md §1)

```python
Effort = Literal["non-reasoning","minimal","low","medium","high","xhigh","max"]
EffortHow = Literal["any","exact","nearest_below","nearest_above","id","one"]

class ProfileSettings:            # add
    effort: Effort | None = None  # None = "any": today's behaviour, score the matched row

class Rank:                       # add, all optional so stored rankings still load
    scored_as: str | None = None  # catalogue id whose observations made the score
    effort: Effort | None = None  # the effort of scored_as (None = one setting)
    effort_how: EffortHow | None = None
```

Validation: `effort` must be one of the seven or null; set on a non-llm
profile → `bad_settings` 400 ("effort applies to llm seats only"). Profile
YAML may carry `effort:` at top level next to `ship` (round-trips with the
write-back). A change is logged as a decision, reason `effort any → medium`.

## 3. Resolving (`sieve/catalog/effort.py`, pure, doctested)

```python
def resolve(matched_id: str, local_ids: list[str], seat: Effort | None,
            modes: dict[str, str]) -> tuple[str, str | None, EffortHow]:
    """modes: effort -> catalogue id for every published effort of the
    matched model's family (empty when the family has one row)."""
```

In this order:
1. `seat is None` → `(matched_id, effort_of_matched, "any")`.
2. family has ≤ 1 mode → `(matched_id, its effort or None, "one")`.
3. **every** local id names an effort itself (slug ends in `-low`, `-medium`,
   `-high`, `-xhigh`, `-max`, `-minimal`, `-non-reasoning`; reuse
   `_SLUG_SUFFIXES` + `max`) → `(matched_id, effort_of_matched, "id")`.
   The router id says what it is; the seat does not override it.
4. `seat in modes` → `(modes[seat], seat, "exact")`.
5. nearest published effort **below** seat on `EFFORT_ORDER` →
   `"nearest_below"`; else nearest above → `"nearest_above"`.

Families come from `models.family` (fallback `family_of(id)`); build the
`family -> {effort: id}` map once per ranking from the store.

## 4. Engine

`rank_profile` resolves each reachable model before scoring and scores the
`scored_as` row's observations (axes, coverage, cost-per-task from that row's
price). Two matched ids that resolve to the same `scored_as` stay two rows
(they are different router models). `cheapest`/health/telemetry still key on
`model_id`. `control.rerank_cached` stays weights-only: a preview whose
`effort` differs from the stored ranking's ranks fresh and keeps the result in
an in-process LRU keyed `(owner, profile, effort, snapshot)` (max 64).
Store which effort a stored ranking was computed at (field on `Ranking`:
`effort: Effort | None = None`) so the cache check is exact.

## 5. API (`sieve/api/routes/v1.py`, document in CONTRACTS.md §6)

| Route | Change |
| --- | --- |
| `POST /v1/profiles/{name}/preview` | body accepts `effort`; every row (models, next, blocked, removed, pool) gains `effort`, `effort_how`, `scored_as`, `family`; top level gains `effort` |
| `GET/PUT/PATCH` settings + `PUT /v1/profiles/{name}` | `effort` read and written like `ship` |
| `GET /v1/model-card?id&modality[&seat]` | gains `ladder`: list in `EFFORT_ORDER` of `{effort, id \| null, published: bool, reachable: bool, score: float \| null, intelligence: float \| null, here: bool}`; one entry per effort the family publishes **plus** the seat's effort if unpublished (`published:false`). `score` = that row scored on the seat's weights (null without `seat`); `here` = the row the seat's score used. Empty list for a one-setting model |
| `GET /v1/seats` | each row gains `effort` and `multi_mode` (a shipped model's family publishes > 1 effort) |
| `GET /v1/chains/{profile}` | gains `effort` (from settings) so a consumer can apply it; Sieve writes no Hermes config |

CLI: `sieve score <profile> --effort <e>`; the profile-settings CLI and MCP
settings tool pass `effort` through. `sieve check`: **warn** (not fail) on an
llm seat with `effort` null that ships a multi-effort model.

## 6. Web (`web/src`)

- `client.ts`: types for the fields above (`Listed.effort/effort_how/scored_as/family`, `ProfileSettings.effort`, `ModelCard.ladder`, `SeatRow.effort/multi_mode`).
- `console/seat/EffortControl.svelte` (new): label **Runs at**, segmented `any none low medium high xhigh max` (`none` = non-reasoning; `minimal` only in the phone sheet) on desktop; one 44px row opening a sheet on the single-column shape. Under the seat title row, above the weights. Part of the dirty-settings diff: Ship counts it, preview re-runs on change. Hidden for media seats. Hint text: "Was any: scored at each model's top effort. Moves N rows."
- `console/seat/EffortPill.svelte` (new) after the name in `ModelRow` and in the inspector header. Looks (mockup §2): exact = solid accent text `medium`; nearest = dashed amber `low ↓` / `max ↑`; id = cool `high · id`; one = muted `one setting`; any = plain effort text muted, or nothing if null. Title text explains each. Text + title from pure functions in `seat/rows.ts`, unit-tested.
- `console/inspector/EffortLadder.svelte` (new) first block of the inspector: rows `effort · bar · score · intelligence`, reachable dot, unpublished rows greyed "not published", `here` row marked with the accent edge; one sentence under it ("The seat runs at medium. GLM 5.3 publishes no medium row, so it is scored at low…"); then the cost note "Same price at every effort…". Skipped when `ladder` is empty.
- Seats pane: second line "Model · effort"; a note `effort?` (amber, title explains) when `effort` null and `multi_mode`. Status bar: "N seats have no effort set" (link to the first one) when N > 0.
- Command palette: `effort <level>` for the open seat.
- Field page: when opened with `?seat=<name>`, ring the point at that seat's effort on each family line.
- Phone (375/414): pill wraps under the name; no control under 44px; nothing past the edge.

## 7. Tests (Done when)

Back end: doctests for `resolve` covering all six hows; engine test on a
fixture with a 3-effort family + one-setting model + an id-named mode, seat
at medium shows the medium row's score, pin survives medium→high; API tests
for preview `effort`, model-card ladder, seats fields, media 400; `ruff`,
`mypy`, `pytest` green.
Web: unit tests for pill text/title and the ladder view; e2e: set Runs at,
rows reorder + pills change, Ship counts 1 change, inspector ladder marks the
row; `pnpm check && pnpm lint && pnpm test && pnpm build` green, e2e green.

## 8. Ownership

| Package | Files |
| --- | --- |
| EFF-A back end | `sieve/**`, `tests/**`, `profiles/` loader, CONTRACTS.md §1 §6 |
| EFF-B front end | `web/**` (incl. `web/e2e`, seed fixtures under web) |

EFF-B works against §5 before EFF-A lands: stub responses in unit tests;
the e2e seed needs EFF-A merged, so B runs e2e last after merging `effort-be`.
