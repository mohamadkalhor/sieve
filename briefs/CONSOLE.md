# Console — the web UI rebuild

This file is the whole brief. A builder who has never seen this project can work
from it and the repository alone. Read §0 and §1, then only the package you were
given (§9), plus every section that package names.

The picture it describes is saved beside this file as
`briefs/console/Console.reference.html`. It is a static mockup: open it as text
for the exact colours, sizes and copy. It will not run in a browser on its own
(it was written for a design canvas), and its numbers are placeholders.

---

## 0. Ground rules (they override anything below)

1. **Independent open-source project.** Nothing organisation-specific in the
   tree: no hostnames, no key values, no people's names, no other product's
   name. Examples use `localhost`.
2. **An absence must never render as a fact.** "Unknown" is drawn as unknown.
   "Still loading" is never drawn as "empty". "In step" is said only when both
   sides of the comparison were actually read. When a number cannot be filled
   honestly the element is replaced, not thinned. This rule has caused almost
   every past bug in this project; every component below has an explicit
   unknown/loading/failed state because of it.
3. **The list on the page is the list that ships.** The browser never decides
   what ships. `POST /v1/profiles/{name}/preview` decides; the page draws it.
   Client-side arithmetic is allowed only for things the server already agrees
   with to 1e-6 (`web/src/lib/rank/weigh.ts`) and for pure presentation.
4. **The only way to `/v1` is `web/src/lib/api/client.ts`.** Every failure comes
   back as `Result<T>`; screens show the error, never an empty table.
5. Node 22, `pnpm`, SvelteKit 2 + Svelte 5 runes + TypeScript + Tailwind v4
   (tokens only; component styles are scoped `<style>` blocks, as today).
   Python 3.12, `ruff`, `mypy --strict`, `pytest`.
6. **Commit per unit of work** (a module that imports and has a test; a
   component that renders). Never one large save at the end.
7. Own only the files your package lists. Anything you need from another
   package goes in `briefs/HANDOFF.md` under that package's id.
8. Do not run `sieve apply` against anything but the `file` target, and do not
   point any target at a live gateway.
9. No new runtime dependency except the two font packages in §3.2.

---

## 1. What is being built

### 1.1 The idea

Today the web app is a left rail and one page per noun. Tuning a profile means
Profiles list → profile page (one 2,308-line file) → scroll between a column of
panels and the list they produce. Why a model ranks where it does is not on
screen at all.

Console is one fixed frame with three panes and no page changes while tuning:

```
+--------------------------------------------------------------------------+
| TopBar  logo | find a model, a seat or an action  Ctrl K | Seats Field … |  52px
+-----------+----------------------------------------------+---------------+
| SeatsPane | SeatPane                                     | Inspector     |
| 260px     | flex                                         | 340px         |
|           |  header: name, purpose, Auto/Manual, Ship    |               |
| Text 9    |  weights: ONE split bar, drag a divider      | selected model|
|  · coder  |  must: need chips · ships [- 5 +]            | where its     |
|  · judge  |  table: # model score per-task can-do via    | score comes   |
|  …        |  ---- ships above this line ----             | from, what it |
| Image 4   |  next up (dimmed) · blocked · removed        | can do and who|
| Video 3   |                                              | says so, price|
| New seat  |                                              | Pin · Never   |
+-----------+----------------------------------------------+---------------+
| StatusBar  run ok 14m ago · 21 ranked · 9 shipped · 87 reachable · …     |  28px
+--------------------------------------------------------------------------+
```

"Seat" is the UI word for a profile (the existing code comments already use it).
In code and in the API the noun stays `profile`. Do not rename API types.

### 1.2 Scope

In scope:

- a new shell (top bar, status bar, command palette) around **every** screen;
- the Seats workspace (three panes), replacing `/profiles` and
  `/profiles/[name]` with full feature parity (§8 is the parity checklist);
- three small, additive API changes (§4);
- new design tokens and self-hosted fonts (§3); every other screen keeps its
  logic and is only re-tokened.

Out of scope (do not start): redesigning Field, Sources, Unscored, Connectors,
Runs, Axes, Guide or Pulse beyond tokens and the shell; a light theme; changing
scoring, selection or apply; `min_axis` editing; anything in `PLAN.md` phase 3.

### 1.3 Routes

| route | what |
| --- | --- |
| `/` | redirect to `/seats` |
| `/seats` | desktop: redirect to the last opened seat (`localStorage sieve:last-seat`), else the first seat with changes waiting, else the first seat. Below 900px: the seats list full-screen |
| `/seats/[name]` | the workspace on that seat. `?model=<catalogue id>` is the inspector's selection. `?view=all` shows the all-reachable table (§6.6) |
| `/profiles`, `/profiles/[name]`, `/profiles?open=x`, `/rankings/*`, `/chains/*` | redirect into `/seats/...`, `replaceState`. Old bookmarks must keep working; the e2e suite checks them |
| `/field`, `/sources`, `/unscored`, `/connectors`, `/runs`, `/axes`, `/axes/[name]`, `/guide`, `/pulse` | unchanged pages inside the new shell |

The app is built with `adapter-static` as a single-page app; dynamic segments
already work that way (`/profiles/[name]` exists today).

---

## 2. Vocabulary (use these words, in code comments and in UI copy)

| word | meaning |
| --- | --- |
| seat | a profile, in the UI |
| lineup | the models that would ship: `preview.models` |
| ship line | the divider after the last lineup row |
| next up | `preview.next`: the ten behind the line |
| blocked | pinned or hand-listed, reachable, but fails a need: `preview.blocked` |
| missing | pinned or hand-listed, nothing reachable serves it: `preview.missing` |
| removed | taken off by the person: `preview.removed` |
| pool | every reachable model of the modality: `preview.pool` |
| unlinked | a router id that matched nothing in the catalogue: `preview.unlinked` |
| live | what the gateway holds now: `chain.primary` + `chain.fallbacks` |
| in step | lineup ids equal live ids, in order (`same()` in `lib/profile/tune.ts`) |
| need | one of `vision`, `reasoning`, `tools`, `structured_output` |
| trim | `prefix_weights`: router prefix → multiplier on the score |

---

## 3. Design system

### 3.1 Tokens — `web/src/lib/tokens/tokens.css` (rewrite)

Dark only, one palette (the file's existing header comment stays true). New
names on the left. The old names on the right **must stay defined as aliases**
because eight untouched pages use them.

```css
:root {
  /* surfaces */
  --c-bg: #0d0f12;         /* app ground */
  --c-pane: #101317;       /* inspector, side surfaces */
  --c-panel: #15181d;      /* inputs, segmented track, popovers */
  --c-raised: #1c2027;     /* selected row, active nav, bar tracks */
  --c-row-sel: #161a20;    /* selected table row */
  /* lines */
  --c-rule: #23272e;       /* pane borders */
  --c-rule-soft: #1a1d23;  /* table row separators */
  --c-rule-strong: #2c313a;/* input borders, idle pills */
  --c-rule-hover: #3a404b; /* outline buttons */
  /* text */
  --c-ink: #e6e8eb;
  --c-ink-2: #c3c8d0;      /* secondary numbers, labels in the inspector */
  --c-muted: #98a0ad;      /* 6.4:1 on --c-bg; do not go lighter-weight than this */
  --c-dim: #5b6472;        /* bar fills below the line; NEVER text */
  /* signal */
  --c-accent: #b6e35c;     /* the decision: ships, selected, primary button */
  --c-accent-ink: #0d0f12; /* text on accent */
  --c-warn: #e0a15a;       /* unknown, blocked, busy */
  --c-bad: #f08c8c;        /* failed, delete */
  /* axis palette, by position in the seat's axis order (index mod 8) */
  --c-axis-0: #b6e35c; --c-axis-1: #5cc8e3; --c-axis-2: #e3b65c; --c-axis-3: #c9a0f0;
  --c-axis-4: #f0a0a0; --c-axis-5: #aeb6c2; --c-axis-6: #7fd6b0; --c-axis-7: #f0c08c;
  /* type */
  --f-ui: 'IBM Plex Sans', system-ui, -apple-system, sans-serif;
  --f-mono: 'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace;
  /* measures */
  --h-top: 52px; --h-status: 28px; --w-seats: 260px; --w-inspect: 340px;
  --r-1: 3px; --r-2: 4px; --r-3: 6px; --r-pill: 14px;
  /* text sizes: 11 caps-label, 12 meta, 13 body, 15 mono-figure, 20 inspector title, 22 seat name */

  /* aliases for the untouched pages */
  --bg: var(--c-bg); --panel: var(--c-panel); --panel2: var(--c-raised);
  --rule: var(--c-rule); --ink: var(--c-ink); --muted: var(--c-muted);
  --accent: var(--c-accent); --reach: #5cc8e3; --good: var(--c-accent);
  --warn: var(--c-warn); --bad: var(--c-bad);
  --display: var(--f-ui); --ui: var(--f-ui); --mono: var(--f-mono);
  --rail: 0px; --radius: 6px;
}
```

Mirror the colours in the `@theme` block the way the file does today.

`web/src/app.css` changes: body `font-size: 13px`; `h1, h2, .display` become
`font-family: var(--f-ui); font-weight: 600; letter-spacing: 0` (they were a
300-weight serif). The caps label used all over the console is one utility:

```css
.caps { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--c-muted); }
```

**Audit after the alias swap:** `--accent` moved from amber to lime and
`--display` from a serif to a sans. Grep every untouched page and component for
`var(--accent)` used as a *background under light text* and for `.display`
sizes that assumed the serif; fix contrast (text on accent is `--c-accent-ink`).
Text contrast is 4.5:1 minimum, 3:1 at 24px and up.

### 3.2 Fonts

Add `@fontsource/ibm-plex-sans` (400, 500, 600) and `@fontsource/ibm-plex-mono`
(400, 500); import the five CSS files at the top of `app.css`. Delete the
Google Fonts `preconnect` and stylesheet lines from `web/src/app.html`. No
request may leave the origin for a font (this closes a known leftover).

### 3.3 Primitives — `web/src/lib/console/ui/`

Small, styled, logic-free. Every one forwards `class` and rest props, renders a
real `<button>`, `<a>` or `<input>`, and shows `:focus-visible` (the global
rule in `app.css` already draws it).

| file | props | notes |
| --- | --- | --- |
| `Button.svelte` | `variant: 'primary'\|'outline'\|'ghost'\|'danger'`, `size: 'sm'\|'md'` (28 / 36px high; 40px in the inspector footer), `disabled`, `title` | primary = accent fill, `--c-accent-ink` text, weight 600 |
| `IconButton.svelte` | `label` (required → `aria-label`), `pressed?: boolean` (→ `aria-pressed`), `size` 28/30 | children = an `<Icon>` |
| `Icon.svelte` | `name: 'search'\|'pin'\|'x'\|'lock'\|'plus'\|'minus'\|'chevron'\|'dots'\|'menu'\|'check'\|'grip'\|'up'\|'down'`, `filled?` | inline stroke SVG, 16 box, `aria-hidden`. Copy the paths from the reference file and from the current profile page (`pinIcon`, the lock). Never an emoji or a text glyph like `✕` or `☰` |
| `Segmented.svelte` | `options: {value,label}[]`, `value`, `onchange`, `label` | `role="radiogroup"`, arrow keys move |
| `Pill.svelte` | `pressed`, `count?: string`, `onclick` | the need chips: pressed = accent outline + accent text |
| `Bar.svelte` | `value: number\|null` (0..1), `tone: 'accent'\|'dim'\|string` | `null` draws a hatched track and no fill (rule 2) |
| `Kbd.svelte` | children | the `Ctrl K` hint |
| `Popover.svelte` | `open`, `anchor: HTMLElement`, `onclose`, `label` | positioned under the anchor, clamps to the viewport, Esc and outside-click close, focus moves in and returns. No portal library; a fixed-position div |
| `Skeleton.svelte` | `rows`, `height` | loading rows; never reuse the empty state for loading |
| `Toast.svelte` | `message: {ok:boolean,text:string}\|null` | `aria-live="polite"`, sits under the seat header |

---

## 4. API changes (package B; contract first)

All additive. An older server that lacks them must not break the UI: each has a
stated fallback, and the fallback never invents a value.

### 4.1 `GET /v1/seats` — new

Every profile as the left pane lists it, in **one** request. It replaces 1 + 2N
requests (profiles, then a chain and a ranking per row).

```ts
export interface SeatRow {
  name: string;
  modality: Modality;
  purpose: string;
  mode: 'auto' | 'manual';
  ship: number;
  /** what the gateway holds now, in order; null when no chain was ever stored */
  live: { id: string; name: string }[] | null;
  /** what the SAVED settings would ship now, in order; null when there is no stored ranking */
  lineup: { id: string; name: string }[] | null;
  /** lineup ids == live ids, in order. null whenever either side is null */
  in_step: boolean | null;
  /** how many ids differ (added + removed + moved); null with in_step */
  changes: number | null;
  shipped_at: string | null;   // chain.computed_at
}
```

Server rules: read only stored rankings (`store.ranking(name, None, owner_id)`)
— **never** call `rank_profile` here; apply the saved settings with
`control.rerank_cached` + `sieve.scoring.select.select` and
`control.capability_map` (compute the map once per modality, not per profile);
names from `store.model_names(modality)`; respect `owner_of(request)` exactly as
`GET /v1/profiles` does, including `control.seed`. Budget: under 300 ms on a
store with 20 profiles and 1,000 models; add a pytest that asserts it makes no
ranking call (monkeypatch `rank_profile` to raise).

`changes` is `len(set(live) ^ set(lineup)) + moved`, where `moved` counts ids in
both whose index differs. Put the function in `sieve/scoring/select.py` as
`changes(live: list[str], lineup: list[str]) -> int`; §5.2 `diff.ts` must agree
with it on the shared fixture `tests/fixtures/lineup_diff.json` (create it:
eight cases, both suites assert against it).

Fallback (404): `api.profiles()` then `api.chain(name)` per seat;
`lineup`, `in_step`, `changes` are `null` and the pane shows no badge at all.

### 4.2 `POST /v1/profiles/{name}/preview` — richer rows

`listed()` in `sieve/api/routes/v1.py` gains, for every row in `models`,
`next`, `blocked`, `removed` **and** `pool`:

```ts
export interface Listed {
  // existing: id, name, local_ids, score, abilities, lacks, pinned, scored
  /** one entry per axis the PROPOSED weights name, in that order */
  axes?: { axis: string; value: number | null; coverage: number; contribution: number }[];
  /** score before health and trim; `score` stays the final number */
  raw?: number;
  health?: number;
  /** what trim multiplied by; 1 when no prefix_weights apply */
  factor?: number;
  confidence?: number;
  cost_per_task?: number | null;
  cost_from?: 'shape' | 'telemetry' | null;
}
```

All of it is already on the `Rank` rows `rerank_cached` returns; this is
serialisation, not computation. `factor` is
`prefix_factor(rank.local_ids, proposed.prefix_weights)`. Filter `axes` to the
axes in `proposed.weights` and order them as the weights dict is ordered. Size
check: assert the JSON for a 300-model pool stays under 400 KB.

Invariant to test (pytest): for every row,
`abs(sum(a.contribution) - raw) < 1e-9` and
`abs(raw * health * factor - score) < 1e-9`.

Fallback (fields absent): the inspector's "where the score comes from" block is
replaced by the sentence "This server does not say how a score is made up." —
not by empty bars.

### 4.3 `GET /v1/model-card?id=<catalogue id>&modality=<modality>` — new

What one model can do and **who says so**, its posted price, and where it is
served. (A path route is not possible: `/v1/models/{model_id:path}` already
swallows every suffix.)

```ts
export interface ModelCard {
  id: string; name: string; creator: string; modality: Modality;
  effort: string | null; family: string | null;
  price: { unit: Unit; input: number | null; output: number | null; per_unit: number | null;
           source: string; observed_at: string } | null;
  /** one entry per need, always all four */
  abilities: Record<Need, { answer: boolean | null; yes: string[]; no: string[] }>;
  context_window: number | null;
  served_by: { local_id: string; prefix: string; inventory: string; stale: boolean }[];
  scored: boolean;
}
```

`yes` / `no` list who said so: a source name from the `capabilities` table
(`aa_llm`, `openrouter`, …) or `inventory:<inventory name>` for a router's own
model list. New store method
`Store.capabilities_by_source(model_id, modality) -> list[tuple[str, Capability]]`
(one `SELECT source, capability ... ORDER BY source`); apply
`sieve.scoring.select.has()` per source. `answer` **must equal** what
`abilities(control.capability_map(...)[id])` gives — assert that in a test over
the fixture store, so the card can never disagree with the list. 404
`not_found` for an unknown id. `scored` from `hand.scored_ids`.

Fallback (404 on the route): abilities come from the preview row
(`Listed.abilities`) with the "who" column reading "source not reported";
price from `api.models({ q: id, limit: 5 })`; `served_by` from
`Listed.local_ids` (prefix = text before the first `/`).

### 4.4 `GET /v1/status` — two more numbers

`reachable: number` (fresh, matched inventory rows, distinct `model_id`) and
`unscored: number` (the length of what `/v1/unscored` would return, computed
without building the rows). Both must be single cheap queries — this route is
polled. Fallback (absent): the status bar omits those two items.

### 4.5 Housekeeping

Regenerate nothing by hand: `web/src/lib/types.ts` is generated from
`sieve/contracts.py` by `sieve export-types`. The four shapes above are
hand-written route shapes, so — like every other route shape — their TypeScript
lives in `web/src/lib/api/client.ts`, and the client gains `api.seats()`,
`api.modelCard(id, modality)`. Document all four in `docs/api.md`.

---

## 5. Front-end architecture

Everything new lives under `web/src/lib/console/`. Three layers, and imports
only point downward:

```
components/   Svelte files. Read state, call commands. No fetch, no arithmetic.
state/        *.svelte.ts classes holding $state. Do IO through lib/api/client. No DOM.
logic/        Pure TypeScript. No Svelte, no fetch, no Date.now() (time is an argument).
```

`logic/` is where correctness lives and is covered by vitest (`web/tests/`,
`environment: 'node'`). `state/` classes take their collaborators in the
constructor so a test can pass fakes. Existing modules are reused, not copied:
`lib/profile/tune.ts`, `lib/rank/weigh.ts`, `lib/freshness.ts`, `lib/who.ts`,
`lib/session.svelte.ts`, `lib/refresh.svelte.ts`.

### 5.1 File map

```
web/src/lib/console/
  logic/
    settings.ts      the editable settings as a value + every pure transition
    split.ts         split-bar geometry and divider arithmetic
    diff.ts          lineup against live: per-row move, leaving, change count
    explain.ts       contributions as points of 100, and the vs-leader sentence
    abilities.ts     need labels, tri-state wording, counts
    seats.ts         grouping, ordering, modality labels, badge text
    commands.ts      command registry, matching, ordering
    money.ts         cost per task and price per 1M as text
  state/
    seats.svelte.ts      SeatsStore
    seat.svelte.ts       SeatSession   (one open seat: settings + preview + ship)
    selection.svelte.ts  Selection     (the inspected model, mirrored to ?model=)
    card.svelte.ts       CardCache     (model cards by id, fetched once)
    status.svelte.ts     StatusStore   (polls /v1/status, listens to /v1/events)
    palette.svelte.ts    Palette       (open, query, results, active index)
  ui/                    §3.3
  shell/
    Shell.svelte  TopBar.svelte  NavLinks.svelte  UserMenu.svelte
    StatusBar.svelte  CommandPalette.svelte
  seats/
    SeatsPane.svelte  SeatLink.svelte  NewSeatForm.svelte
  seat/
    SeatPane.svelte  SeatHeader.svelte  SeatMenu.svelte  AskBar.svelte
    WeightsBlock.svelte  SplitBar.svelte  AxisPopover.svelte  AddAxisPopover.svelte
    NeedChips.svelte  ShipStepper.svelte  TrimRow.svelte
    LineupTable.svelte  ModelRow.svelte  ShipLine.svelte  ManualList.svelte
    AllReachable.svelte  UnlinkedRow.svelte  ListStateView.svelte
    HistoryDrawer.svelte
  inspector/
    Inspector.svelte  WhyBars.svelte  AbilityTable.svelte  PriceBlock.svelte
    ServedBy.svelte  InspectorActions.svelte
web/src/routes/
  +layout.svelte                (unchanged)
  (app)/+layout.svelte          mounts Shell instead of Rail
  (app)/seats/+page.svelte      redirect / mobile list
  (app)/seats/[name]/+page.svelte   SeatsPane | SeatPane | Inspector
  (app)/seats/[name]/+page.ts       export const load = ({ params }) => ({ name: params.name })
  (app)/profiles/+page.ts , (app)/profiles/[name]/+page.ts   redirects
```

Deleted at the end (package F, only after parity is proven):
`lib/components/Rail.svelte`, `lib/components/ProfileRow.svelte`,
`routes/(app)/profiles/+page.svelte`, `routes/(app)/profiles/[name]/+page.svelte`.

### 5.2 `logic/` contracts

**`settings.ts`**

```ts
export interface Settings {
  weights: Record<string, number>;   // shares, sum 1
  order: string[];                   // axis order as added; never re-sorted by weight
  locked: string[];                  // page-only, persisted to localStorage by the session
  ship: number;
  mode: ProfileMode;
  manual: string[]; pinned: string[]; removed: string[];
  needs: Need[];
  prefixWeights: Record<string, number>;
}
export function fromServer(profile: Profile, held: ProfileSettings | null, locks: string[]): Settings;
export function toPatch(s: Settings, everyAxis: string[]): SettingsPatch;  // includes remove_axes
// every edit is (Settings, ...args) => Settings, and returns the SAME object when nothing changed
export function moveWeight(s, axis, value): Settings;          // renormalise() with s.locked held
export function transferWeight(s, left, right, delta): Settings; // split-bar drag, see split.ts
export function setExact(s, axis, percentText): Settings;
export function toggleLock(s, axis): Settings;
export function preset(s, kind: Preset): Settings;
export function resetTo(s, loaded: Record<string, number>): Settings;
export function addAxis(s, axis): Settings;                     // tune.addAxis + order
export function dropAxis(s, axis): Settings | { error: string }; // refuses the last axis
export function setShip(s, n): Settings;                        // clampShip
export function setMode(s, mode, currentLineupIds: string[]): Settings; // manual starts from the lineup
export function toggleNeed(s, need): Settings;
export function setTrim(s, prefix, rawText): Settings;          // blank, non-finite, <0 or 1 removes the key
export function pin(s, id): Settings;                           // toggles; un-removes
export function remove(s, id): Settings;                        // un-pins
export function restore(s, id): Settings;
export function addManual(s, id): Settings;  export function dropManual(s, id): Settings;
export function nudgeManual(s, id, by: number): Settings;       // tune.shift
```

These are the functions in today's profile page (`move`, `exact`, `lock`,
`preset`, `drop`, `add`, `setShip`, `pin`, `remove`, `restore`, `addManual`,
`dropManual`, `nudge`, `setMode`, `setPrefixWeight`, `toggleNeed`, `patch`)
moved out verbatim in behaviour. Port them by reading
`routes/(app)/profiles/[name]/+page.svelte` lines 278–508; behaviour changes
are bugs. Invariant tested after every transition: weights sum to 1 within
1e-9, every `locked` and `order` entry is a key of `weights`, no id is in both
`pinned` and `removed`.

**`split.ts`** — the split bar is the one new interaction, so its arithmetic
is isolated and heavily tested.

```ts
export interface Segment { axis: string; weight: number; px: number; stub: boolean; color: string }
export const STUB_PX = 14;          // a share under STUB_SHARE still gets a grabbable stub
export const STUB_SHARE = 0.02;
export const GAP_PX = 3;
/** lay the weights out across `barPx`; stubs are fixed width, the rest share what is left in proportion */
export function layout(weights, order, barPx): Segment[];
/** the two axes a divider moves weight between: the nearest UNLOCKED axis on each side, or null if either side has none */
export function parties(order, locked, dividerIndex): { left: string; right: string } | null;
/** pixels to share: deltaPx / (barPx - stubs - gaps) * (sum of non-stub weights) */
export function pxToShare(deltaPx, segments, barPx): number;
/** move `delta` from right to left (negative: the other way), clamped so neither goes below 0 nor the pair's total changes */
export function transfer(weights, left, right, delta): Record<string, number>;
export const KEY_STEP = 0.01; export const KEY_STEP_BIG = 0.05;
export function label(segment: Segment, axisLabel: string): { text: string; number: string }; // what fits at this width
```

Semantics, stated once: **dragging a divider moves weight between the two
nearest unlocked neighbours only**; every other share is untouched, so the sum
is 1 by construction. Typing an exact percentage (in the axis popover) keeps
today's rule — the unlocked others follow in proportion (`renormalise`). A
divider whose `parties()` is null renders without a handle and is not focusable.
Label fitting: `px >= 96` label + number; `>= 40` number only; else nothing
(the `title` and `aria-label` always carry both).

**`diff.ts`**

```ts
export type MoveKind = 'same' | 'up' | 'down' | 'new';
export interface RowMove { id: string; kind: MoveKind; by: number; text: '' | `up ${number}` | `down ${number}` | 'new' }
export interface LineupDiff { moves: Record<string, RowMove>; leaving: string[]; changes: number; known: boolean }
/** `live === null` (no chain read) gives known:false and every text '' — never "new" for everything */
export function diffLineup(live: string[] | null, lineup: string[]): LineupDiff;
export function shipLabel(d: LineupDiff, state: ShipState): string; // 'Ship now' | 'Ship 1 change' | 'Ship n changes' | state.label when disabled
```

`changes` must equal the server's `changes()` (§4.1) on the shared fixture.

**`explain.ts`**

```ts
export interface WhyRow { axis: string; label: string; got: number; of: number; share: number; measured: boolean; color: string }
/** got = contribution*100, of = weight*100, share = got / max(of over all rows) for the bar width */
export function whyRows(row: Listed, weights, order, labels): WhyRow[] | null;   // null when row.axes is absent
export interface Versus { leaderName: string; behindBy: number; mostlyOn: string | null; aheadOn: { axis: string; by: number } | null }
/** against the lineup's first row; null for the leader itself or when either side lacks axes. Uses weigh.carriedBy both ways */
export function versusLeader(row: Listed, leader: Listed | null, labels): Versus | null;
export function multipliers(row: Listed): { health: number; factor: number } | null; // shown only when either != 1
```

An unmeasured axis (`value === null`) is a row with `measured:false`, drawn as a
hatched track and the text "not measured" — not as `0.0`.

**`abilities.ts`** — `NEED_LABEL`, `NEED_TAG` (move them from the profile
page), `tone(answer)` → `'yes' | 'no' | 'unknown'`, `who(entry)` → text
("OpenRouter, gateway" / "no source says"), `supportCount(pool)`,
`describable(pool)`.

**`seats.ts`** — `groupByModality(rows)`, `MODALITY_LABEL` (`llm` → "Text",
`text-to-image` → "Image", … one short label each; groups with no seats are not
listed), seats alphabetical inside a group (stable positions matter more than
urgency; urgency is the badge), `badge(row)` →
`{ text: string; tone: 'accent' | 'muted' } | null`: `changes > 0` → the count
in accent; `mode === 'manual'` → "hand"; `in_step === null` → `null` (say
nothing); else `null`.

**`commands.ts`**

```ts
export interface Command { id: string; group: 'Seats' | 'Models' | 'Go to' | 'Actions'; title: string; hint?: string; keywords?: string; run(): void | Promise<void>; enabled?: boolean }
export interface Provider { (ctx: PaletteContext): Command[] }
export function match(query: string, commands: Command[], limit = 12): Command[]; // case-insensitive subsequence; prefix and word-start hits rank first; empty query → a fixed starter list
```

Providers: seats (open), models of the open seat's pool (select in the
inspector; with a seat open also "Pin …", "Never ship …" as separate
commands), sections (the nav), actions ("New seat", "Ship <seat>" when the
button is enabled, "Switch to manual/auto", "Run now" → `/runs`). No provider
fetches; they read stores.

**`money.ts`** — `perTask(n | null)` → `$0.06`, `<$0.01`, or `—` with title
"no price, so no cost"; `perMillion(n | null)`. `—` is only ever produced from
`null`.

### 5.3 `state/` contracts

```ts
// seats.svelte.ts
class SeatsStore {
  rows: SeatRow[] | null; error: ApiError | null; loading: boolean; degraded: boolean; // degraded = fallback path, no lineup data
  constructor(deps: { api: typeof api });
  load(): Promise<void>;                 // called by the layout's $effect, re-run on runPulse.seen()
  patch(name: string, part: Partial<SeatRow>): void;   // after a ship, without a refetch
}

// seat.svelte.ts  — everything the old profile page held in `let x = $state(...)`
class SeatSession {
  readonly name: string;
  profile: Profile | null; chain: Chain | null; everyAxis: AxisRow[]; prefixes: string[];
  settings: Settings | null; loaded: Record<string, number>;
  preview: PreviewResult | null;          // models/next/blocked/removed/missing/pool/unlinked live here
  pending: boolean; waitedMs: number; failed: string | null;
  shipping: boolean; linking: string | null; said: { ok: boolean; text: string } | null;
  gone: ApiError | null; loading: boolean;
  // derived (getters over $derived)
  listing: ListState; live: string[] | null; diff: LineupDiff; button: ShipState; shipText: string;
  labels: Record<string, string>; meanings: Record<string, string>; spare: AxisRow[];
  constructor(name: string, deps: { api; storage: Storage | null; setTimeout; clearTimeout; now(): number; token(): string | undefined });
  open(): Promise<void>;                  // profile + settings + chain, then axes + cost multipliers, then preview
  close(): void;                          // clears timers; a late answer is dropped (generation counter)
  edit(next: Settings | { error: string }): void;  // sets settings, clears `said`, schedules save + preview after DEBOUNCE_MS
  ship(): Promise<void>;                  // save, applyProfile, adopt the chain, SeatsStore.patch, refresh preview
  linkThen(localId: string, then: (id: string) => Settings): Promise<void>;
  copy(to): Promise<string | null>; rename(to): Promise<string | null>; destroy(): Promise<boolean>;
  history(): Promise<HistoryRow[]>;
}
```

Rules carried over from the current page and not to be "simplified":
- one in-flight generation counter; an answer for a seat that has been left, or
  for an older request, is dropped;
- `pending`/`waitedMs` tick every 500 ms so `listState` can say "busy" after
  `SLOW_MS`; "nothing ships" only when an answer came back empty;
- a failed save sets `said`; a failed preview sets `failed`, and the previous
  lineup stays on screen dimmed, not cleared;
- locks persist under `sieve:locks:<name>` and are filtered to existing axes;
- `runPulse.seen()` re-opens the seat's data after a finished run.

No `$effect` inside `state/` classes (they must work outside a component); the
route component owns the single `$effect` that creates a session for
`data.name` and closes the previous one.

```ts
// selection.svelte.ts
class Selection { id: string | null; select(id): void; /** default when the URL names none */ adopt(session: SeatSession): void }
```
`adopt` picks, in order: the `?model=` id if it is in the pool; the first
lineup row whose move is not `same`; the first lineup row; else null. Selecting
writes `?model=` with `replaceState` (no history spam).

```ts
class CardCache { get(id, modality): { card: ModelCard | null; state: 'loading' | 'ready' | 'fallback' | 'error' } }
class StatusStore { row: StatusRow | null; unreachable: boolean; start(): () => void }  // poll 60 s; refetch on any /v1/events message via client.subscribe(); stop on teardown. Also feeds session.adopt() and bumps runPulse when runs.running goes from a row to null
class Palette { open: boolean; query: string; results: Command[]; active: number; toggle(); move(by); run() }
```

### 5.4 Dependency graph (what may import what)

```
routes/(app)/seats/[name] ─► seat/SeatPane, seats/SeatsPane, inspector/Inspector
components ─► state ─► logic ─► lib/profile/tune, lib/rank/weigh
components ─► ui            state ─► lib/api/client
inspector ─► SeatSession (read only) + Selection + CardCache     — it never edits settings except through session.edit(pin/remove)
seats pane ─► SeatsStore only          shell ─► StatusStore, Palette, session
```
Stores are created once in `(app)/+layout.svelte` and handed down with
`setContext`; keys live in `console/context.ts`. No module-level singletons
for the new stores (tests need fresh ones); `session` and `runPulse` stay the
singletons they are.

---

## 6. Component specifications

Sizes are CSS px. "caps" is the §3.1 utility. Every interactive element is
reachable by Tab and has a visible focus ring.

### 6.1 Shell

`Shell.svelte`: `display:grid; grid-template-rows: var(--h-top) minmax(0,1fr) var(--h-status); height:100dvh`.
The middle row is the only thing that scrolls, and on the seats route it does
not scroll at all — each pane scrolls itself. On every other route the middle
row is one `overflow:auto` element with padding `20px 28px 64px`.

**Known trap:** `lib/components/Scatter.svelte` sizes itself from
`globalThis.innerHeight`, `getBoundingClientRect().top` and `scrollY`
(lines ~86–89). Inside the shell the page no longer scrolls the window and
28px of status bar sits below. Subtract `--h-status` there and use the scroll
container's `scrollTop`, then confirm `e2e/field-*.spec.ts` still pass.

`TopBar.svelte` (52px, padding 0 16, border-bottom `--c-rule`): logo block
228px wide (a 14px accent square + `sieve` in mono 15/500, links to `/seats`);
the palette trigger (a `<button>` that looks like the search field: 36px high,
max-width 620, `--c-panel`, border `--c-rule-strong`, radius 6, search icon,
placeholder text "Find a model, a seat or an action", `Kbd` "Ctrl K" — "⌘ K"
on Apple platforms); `NavLinks` right-aligned: Seats, Field, Sources, Unscored,
Connectors, Runs, Axes, Guide (active = `--c-raised` fill, radius 6; others
`--c-muted`); `UserMenu`.

`UserMenu.svelte`: **port the `who` block of the current
`lib/components/Rail.svelte` exactly** — read the file as it is on `main`; it
asks the same-origin auth endpoint for the sign-in chip, the admin link and the
sign-out token, and none of that logic changes. It becomes a button showing the
person's name (or "Sign in") opening a `Popover` with role, sign out, and the
pasted-token field bound to `session.token`. "API unreachable" moves to the
status bar.

`StatusBar.svelte` (28px, mono 11, `--c-muted`): dot + "run ok 14m ago" /
"run failed 14m ago" (`--c-bad`) / "running: <step>" (`--c-warn`) from
`status.runs`; `runs.last.summary` verbatim when present; "N reachable";
"telemetry N calls"; link "N unscored" → `/unscored`; right-aligned "next run
in 46m" from the soonest `schedules[].next_fire`, or "no schedule". Times via
`lib/freshness.ts` `ago()`. Any field the server did not send is omitted, not
zeroed. `unreachable` replaces the whole bar with "Cannot reach the API" in
`--c-bad`.

`CommandPalette.svelte`: modal dialog, 640 wide, top 12vh; input is
`role="combobox"` with `aria-controls`, `aria-activedescendant`; list is
`role="listbox"` grouped by `Command.group`; ↑/↓ move, Enter runs, Esc closes
and returns focus. Opened by Ctrl/Cmd+K anywhere and by `/` when focus is not
in a field. One `svelte:window` key handler, in the Shell.

### 6.2 SeatsPane (260px, border-right, padding 12 8, scrolls)

Per modality group: a caps header row with the count right-aligned in mono.
`SeatLink` (46px, radius 6, selected = `--c-raised`, `aria-current="page"`): a
6px dot (accent when `changes > 0`, else `--c-rule-hover`), name in mono 13,
under it the first live model's name in 11 `--c-muted` with ellipsis — or
"nothing shipped yet" when `live` is `[]`, or nothing when `live` is `null`;
right side `badge()`. Groups collapse on header click; collapsed state in
`localStorage sieve:seats-collapsed`. Bottom, pinned: dashed "New seat" button
opening `NewSeatForm` in a `Popover` — **port the create form from
`routes/(app)/profiles/+page.svelte`** (name, modality, copy-from limited to
the same modality, `api.newProfile` with the `createProfile` fallback, inline
error), then `goto('/seats/<name>')`.
States: loading → 6 skeleton rows; error → the `explainError` text and Retry;
`degraded` → a one-line note "This server cannot say which seats are in step."

### 6.3 SeatHeader (padding 16 20 12)

`h1` seat name, mono 22/500. Purpose under it, `--c-muted`; click turns it into
an input saved with `PATCH /v1/profiles/{name}` `{purpose}` (the API has this;
add `api.setPurpose`). Right: `Segmented` Auto/Manual; `Button` primary with
`session.shipText`, `disabled` + `title` from `session.button`; `SeatMenu`
(dots) → Copy, Rename, Delete, History. Copy/Rename/Delete open `AskBar` under
the header (the current page's `.ask` block: one input + confirm + cancel;
Delete asks once and retries with `force` on `in_use`, as today). After
copy/rename `goto` the new seat. `Toast` under the header shows `session.said`.

### 6.4 WeightsBlock (padding 0 20 14) — auto mode only

Row 1: caps "Weights", hint "one bar, drag a divider", then right-aligned the
preset chips from `PRESETS` (disabled rule unchanged: non-`even` presets need
the cost axis) and "Reset".

Row 2, `SplitBar.svelte`: a 40px-high flex row, `GAP_PX` gaps.
- Each segment is a `<button>` (radius 3, fill `--c-axis-N`, text
  `--c-accent-ink` 12/600, label left, mono number right, per `split.label`);
  `aria-label="Agentic coding, 45 percent, locked"`; click opens `AxisPopover`.
  A locked segment shows a 10px lock icon before the number.
- Between segments, when `parties()` is not null, a divider handle: a
  transparent 11px-wide hit area centred on the gap, `cursor: col-resize`,
  `role="separator"`, `aria-orientation="vertical"`, `tabindex="0"`,
  `aria-valuenow` = left party's percent, `aria-label="Between Cost and Tool use"`.
  Pointer: `pointerdown` → `setPointerCapture`, track `clientX` deltas against
  the bar's width measured at drag start, `edit(transferWeight(...))` on every
  `pointermove` (rAF-throttled), release on `pointerup`/`pointercancel`.
  Keyboard: ←/→ `KEY_STEP`, with Shift `KEY_STEP_BIG`, Home/End to the limits.
  While dragging, the two party segments show their live numbers even if narrow.
- Bar width comes from a `ResizeObserver`; never assume 100% = a constant.
- Last item: a 40×40 outline `IconButton` "Add an axis" → `AddAxisPopover`
  listing `session.spare` (label + meaning); hidden when `spare` is empty.

`AxisPopover.svelte`: axis label, its `meaning`, exact percent `<input
type="number" min=0 max=100 step=1>` with a visible `<label>` (commit on
change → `setExact`; disabled while locked), a Lock toggle, and "Remove axis"
(`dropAxis`; its error becomes `said`). Axis order never changes while
weights move.

### 6.5 Needs, ship count, trim (same block, under the bar)

`NeedChips`: caps "Must", then four `Pill`s from `NEEDS` with
`NEED_TAG` text and `supportCount` ("tools 58"); `title` = full label + "of N
reachable". When `!describable(pool)`: replace the pills with "no source
describes what these models can do"; while the pool is null: "waiting for the
list…". `ShipStepper` right-aligned: "ships" − n + (28px buttons, mono n,
`aria-label`s "One fewer"/"One more"; `setShip`, min 1, no max; hidden in
manual mode, where the text is "N in the list · no limit"). `TrimRow`
(collapsed by default; a ghost button "Trim routers" that shows the count of
prefixes ≠ 1): per prefix from `session.prefixes`, a mono label and a number
input (step 0.05, placeholder `1`), `setTrim`; helper text "multiplies the
score of every model that router serves; the best router counts".

### 6.6 The table

Header row (30px, caps, borders top and bottom): `#` 24 · Model 210 · Score
flex · Per task 70 right · Can do 110 · Via 50 · actions 64. Left of the
header, a view switch: "Lineup" | "All reachable N" (`?view=all`).

`ModelRow.svelte` (44px in the lineup, 40px below the line; gap 12, padding
0 20; `--c-rule-soft` under each). Props: `row: Listed`, `rank: string`,
`move: RowMove | null`, `selected`, `dim`, `tone: 'ship' | 'next' | 'blocked' |
'removed' | 'missing'`, `actions: ('pin' | 'remove' | 'restore' | 'add' |
'up' | 'down' | 'drop')[]`.
- rank: mono `--c-muted`; blank for rows outside the ranking.
- name 13/500 with ellipsis; after it `move.text` in accent 11/600; a
  "no score" tag when `scored === false` (title: "No source has benchmarked
  it: it ranks on price alone"); for `blocked`: "fails <need labels>" in
  `--c-warn`; for `missing`: "not reachable now".
- score: `Bar` (accent above the line, `dim` below) + mono two decimals.
- per task: `money.perTask(row.cost_per_task)`; a small "measured" dot when
  `cost_from === 'telemetry'` (title: "from this seat's own traffic").
- can do: `NEED_TAG`s of abilities that are `true`, joined with " · "; if a
  *required* need is `null`, the text is "<tag> unknown" in `--c-warn`.
- via: distinct prefixes of `local_ids`, e.g. `or/` — two at most, then "+1".
- actions: 30px `IconButton`s. Pin is `aria-pressed`; "Never ship <name>".
- the whole row is selectable: click anywhere not on a button, or Enter when
  the row is focused; rows use a roving `tabindex`, ↑/↓ move focus and
  selection together, `p` pins, `Delete` removes. Selected = `--c-row-sel`
  plus `box-shadow: inset 2px 0 0 var(--c-accent)`.

`LineupTable.svelte`, auto mode, in order: lineup rows (rank 1…n) →
`ShipLine` (28px, accent caps: "Ships above this line" — rule — "next up
below") → `next` rows at `opacity .72` → `blocked` rows → a "Removed by you ·
n" caps divider with `removed` rows (Restore) → `missing` rows. When
`diff.leaving` is not empty and the leaving model is not already visible in
`next`, add it under the line with the text "leaves" so a ship's full effect is
on screen. `ListStateView` wraps the body: `ranking` → skeleton rows;
`busy` → "The server is busy; still ranking…" + Retry; `error` → the message +
Retry, old rows dimmed behind it; `empty` → "Nothing ships: no reachable model
meets every need." (only from a real empty answer).

`ManualList.svelte`, manual mode: `session.settings.manual` in order, each via
`ModelRow` with up/down/drop actions (`nudgeManual`, `dropManual`); rows that
are `blocked` or `missing` stay in place and say why. No ship line. Under it, a
button "Add models" that switches to the all-reachable view.

`AllReachable.svelte`: a filter `<input type="search">` ("Filter N reachable
models…", matches name, id, local ids), an "Unscored only · N" `Pill` (with a
link "score them" → `/unscored` when on), needs applied as a filter in manual
mode (as today). Rows: pool order = rank; `standing()` text ported from the
current page ("ships #2", "pinned · skipped", "removed", "fails Must support",
"not shipping"); actions Pin/Never (auto) or Add (manual). Below them
`UnlinkedRow`s (rank "—", tag "unknown", one button that calls
`session.linkThen` then pins/adds; "Linking…" while it runs). Render at most
200 rows and say "N more — keep typing" rather than virtualising.

### 6.7 Inspector (340px, `--c-pane`, border-left, padding 16 20, gap 18, scrolls)

Nothing selected → "Select a model to see why it ranks where it does."
1. caps line: "Selected · ships #2" / "would ship #2" when the lineup is not
   live yet / "next up, #7" / "removed by you" / "fails Must support"; `h2`
   name 20/600; first local id in mono 12 (title lists the rest).
2. `WhyBars`: heading "Where 0.88 comes from", right "points of 100"; one row
   per `whyRows` (label 96px 12 `--c-ink-2`; an 8px track with the fill in the
   axis colour at `share`; mono "41.0 / 45"). Unmeasured → hatched track + "not
   measured". If `multipliers()` is not null, a line "× health 0.97 × trim
   0.90". Then the `versusLeader` sentence: "Behind <leader> by 3.4 points,
   mostly on Agentic coding. Ahead on Cost by 5.1." `whyRows === null` → the
   §4.2 fallback sentence.
3. `AbilityTable`: heading "What it can do, and who says so"; four rows: label
   110px · answer 64px 600 (`yes` accent, `no` `--c-ink-2`, `unknown`
   `--c-warn`) · who (`--c-muted`). Under it, when any *required* need is
   unknown: "Unknown counts as no for this seat." Card loading → skeleton;
   `fallback` → the who column reads "source not reported".
4. `PriceBlock`: three figures (caps-less 11 label over mono 15): "In, per 1M",
   "Out, per 1M", "One <seat> task". Media units: one figure with the unit's
   own wording ("per image", "per second", …) from `price.unit`. No price →
   "No posted price" once, not three dashes.
5. `ServedBy`: mono list of local ids, `stale` ones struck with "gone from the
   router". Shows the trim factor beside a prefix when it is not 1.
6. `InspectorActions`, pinned to the bottom: "Pin first"/"Unpin" and "Never
   ship"/"Put back" (auto); "Add to list"/"Take off the list" (manual). 40px
   outline buttons. All go through `session.edit(...)`.

`HistoryDrawer.svelte` replaces the inspector's content while open (title
"History", close button): `api.history(name)` rows — who, `ago(when)`, what;
loading and empty ("Nothing has changed this seat yet.") states.

### 6.8 Responsive

| width | layout |
| --- | --- |
| ≥ 1280 | three panes as specified |
| 1024–1279 | inspector becomes a 340px drawer over the seat pane's right edge; selecting a row opens it; Esc or its close button closes it |
| 900–1023 | also: seats pane collapses into a seat switcher — a button in the seat header with the seat name and a chevron, opening the same `SeatsPane` in a `Popover` |
| < 900 | one column. Top bar: logo, search icon button, menu button (nav in a sheet). `/seats` is the list; `/seats/[name]` is the seat pane; the inspector is a bottom sheet (max 80dvh). Table columns: `#`, Model (with move + tags under the name), Score; per-task/can-do/via move into the sheet. Split bar stays, 48px high, hit areas 24px; touch drag uses the same pointer code. Hit targets ≥ 44px |

No horizontal overflow at 375, 768, 1440 on any route
(`e2e/responsive.spec.ts` already asserts this — extend it to `/seats/*`).

### 6.9 Motion

120 ms ease on hover fills; when a preview answer reorders the lineup, rows
move with a 160 ms FLIP translate (Svelte `animate:flip`, keyed by id). All of
it off under `prefers-reduced-motion` (`lib/motion/reduced.ts` exists). The
split bar never animates while being dragged.

---

## 7. Copy deck (exact strings; keep them)

Ship button: "Ship now" · "Ship 1 change" · "Ship 3 changes" · "Shipping…".
Disabled titles (from `shipState`): "waiting for the list" · "there is nothing
to ship" · "this is already what ships". After shipping: "Shipped as
<combo names>." or "Shipped." Ship line: "Ships above this line" / "next up
below". Moves: "up 1", "down 2", "new", "leaves". Tags: "no score", "unknown",
"hand". Needs: labels "Image input", "Thinking mode", "Tool calling",
"Structured output"; tags "vision", "thinking", "tools", "JSON". Delete ask:
"Delete <name>? The combo it ships stops being updated." Last axis: "A profile
needs at least one axis to score by." Errors always come from `explainError`.

---

## 8. Parity checklist — everything the current pages do must survive

Tick each against the running app before deleting the old pages.

- [ ] auto/manual switch; manual starts from the current lineup
- [ ] four needs with `n/N` counts; "no source describes…" and "waiting…" states
- [ ] weights: move (now by divider), exact percent, lock (persisted per seat), presets Even / Quality first / Value with the cost-axis rule, Reset to what the page opened with, add axis (even share), remove axis (not the last), axis order stable, per-axis meaning shown
- [ ] how many ship: min 1, no max; pins count towards it
- [ ] pin / unpin, remove, restore; a pin un-removes and a remove un-pins
- [ ] manual list: add, drop, move up/down, blocked and unreachable rows shown in place
- [ ] find a model across the pool, with rank and standing; "Unscored only"; link to Unscored
- [ ] unlinked router ids: link, then pin or add
- [ ] trim per router prefix; blank or 1 clears
- [ ] save 400 ms after the last edit; preview on the same timer; stale answers dropped
- [ ] list states: ranking / busy after 8 s / error with retry / empty only from a real answer
- [ ] Ship disabled with a reason; shipped message names the combos; chain adopted without a reload
- [ ] copy, rename, delete (with forced retry on `in_use`), history
- [ ] edit the purpose sentence
- [ ] new seat, with copy-from limited to the same modality
- [ ] a finished run refreshes what is on screen (`runPulse`)
- [ ] sign-in chip, role, sign out, pasted token, redirect to login on 401 (client.ts does this; do not touch it)
- [ ] old URLs redirect

---

## 9. Work packages

One builder per package. "After" is a hard order; everything else is parallel.
Each package ends green on its own checks and with a note in
`briefs/HANDOFF.md` (what landed, what is stubbed, commit hash).

| id | what | owns (nobody else edits these) | after |
| --- | --- | --- | --- |
| **A** Foundation | tokens, fonts, `app.css`, `app.html`, `ui/*`, `Shell`, `TopBar`, `NavLinks`, `UserMenu`, empty `StatusBar`, `(app)/+layout.svelte`, new routes with placeholder panes, all redirects, `console/context.ts`, the new shapes + `api.seats` / `api.modelCard` / `api.setPurpose` and the §4 fallbacks in `client.ts`, the accent/display audit of untouched pages, the Scatter height fix | `lib/tokens/*`, `app.css`, `app.html`, `console/ui/*`, `console/shell/*` (except `CommandPalette`, `StatusBar` body), `console/context.ts`, `routes/**` layout and redirect files, `lib/api/client.ts`, `lib/components/Scatter.svelte` | — |
| **B** API | §4.1–4.5 with pytest, `docs/api.md` | `sieve/api/routes/v1.py`, `sieve/scoring/select.py` (`changes` only — do not touch `select`/`behind`), `sieve/store/db.py` (one new read method), `tests/**` for those, `tests/fixtures/lineup_diff.json`, `docs/api.md` | — |
| **C** Logic | all of `console/logic/*` with vitest in `web/tests/console/*.test.ts` | `console/logic/*`, `web/tests/console/*` | A (types in `client.ts`) |
| **D** Seat pane | `state/seat.svelte.ts`, `state/selection.svelte.ts`, everything in `console/seat/*`, the `[name]/+page.svelte` composition | those files | C |
| **E** Inspector | `state/card.svelte.ts`, `console/inspector/*` | those files | C; builds against the `SeatSession` and `Selection` *interfaces* in §5.3 with a fake until D lands |
| **F** Seats pane, status, palette | `state/seats.svelte.ts`, `state/status.svelte.ts`, `state/palette.svelte.ts`, `console/seats/*`, `StatusBar`, `CommandPalette`, `/seats/+page.svelte` | those files | C |
| **G** Integrate | responsive pass (§6.8), motion, e2e rewrite, parity checklist, delete the four old files, `briefs/HANDOFF.md`, screenshots | `web/e2e/*`, deletions | D, E, F, B |

### Acceptance, per package

**A** — `pnpm check`, `pnpm lint`, `pnpm build` green. Every existing route
renders inside the shell with no console error. No request to a font CDN
(assert in an e2e: no request whose host differs from the base URL). Old URLs
redirect. Text contrast audit done and listed in the handoff note. Field e2e
specs pass.

**B** — `ruff`, `mypy --strict`, `pytest` green. New tests: `/v1/seats` shape,
owner scoping, no-ranking-call guard, `in_step`/`changes` null rules, the
300 ms budget on the fixture store; preview invariants of §4.2 and the size
cap; model-card agreement with `capability_map`, 404, a model with no
capability rows answers all-`null` with empty lists; status numbers. The shared
diff fixture passes.

**C** — vitest green; at minimum: `settings` invariants after every transition
(property-style: 200 random edit sequences from a seeded generator); `split`:
layout sums to `barPx`, stubs, `parties` with locks on either side and at the
ends, `transfer` clamps and conserves the pair total, `pxToShare` round-trips
with `layout`; `diff` against the shared fixture incl. `live === null`;
`explain` sums (`Σ got` = `raw × 100` within 1e-6), unmeasured axes, leader
returns null; `commands.match` ordering; `money` never prints `—` for a number.

**D** — with the seeded e2e server: open `/seats/coder`; drag a divider by
keyboard and see the lineup re-rank and the button read "Ship n change(s)";
weights still sum to 100; exact-percent with a lock holds the locked axis;
every list state reachable (use Playwright route interception to delay and
fail `/preview`); manual mode round trip; ship without a token shows the 401
text; discard by Reset.

**E** — selecting rows updates `?model=` and the three blocks; a row with a
`null` required need shows "unknown" and the explanatory line; with
`/v1/model-card` intercepted to 404 the fallback wording appears; with `axes`
stripped from the preview the fallback sentence appears; no `NaN`, no `0.0`
for unmeasured.

**F** — one request paints the seats pane (assert the request count in e2e);
degraded note under a 404; badge rules; collapsing persists; palette opens on
Ctrl+K and `/`, arrows + Enter run, Esc restores focus, axe-core style checks:
combobox has an accessible name, listbox options have ids; status bar omits
absent fields and shows the unreachable state.

**G** — rewrite `e2e/smoke.spec.ts` around `/seats` (keep its five intents:
deep link, re-rank says not applied, isolation between seats, 401 shown,
discard). `focus.spec.ts`, `responsive.spec.ts`, `runs.spec.ts`,
`aliases-guide.spec.ts`, `field-*.spec.ts`, `pulse.spec.ts` pass. Parity
checklist ticked. `rerank.perf.spec.ts` budget unchanged. Full validator run:
`ruff`, `mypy --strict`, `pytest`, `pnpm lint`, `pnpm check`, `pnpm test`,
`pnpm test:e2e`, `gitleaks detect`.

---

## 10. Risks the builders should expect

1. **Slow API.** Several read routes on a large store answer in seconds for a
   reason nobody has found yet. Do not add per-row requests anywhere; the seats
   pane is one request and the inspector's card is fetched once per model and
   cached. Every pane has a loading state that can stay up for 20 s without
   looking broken.
2. **Runes outside components.** `state/*.svelte.ts` classes use `$state` and
   `$derived` only. If vitest cannot compile them in the `node` environment,
   do not fight it: the logic is already in `logic/`, so test that and cover
   the classes through e2e.
3. **Pointer capture and text selection** on the split bar: set
   `touch-action: none` and `user-select: none` on the bar during a drag only.
4. **`overflow-x: hidden` on `html, body`** (in `app.css`) hides layout bugs
   instead of failing them; the responsive spec measures `scrollWidth`, keep it.
5. **Alias drift.** A later cleanup that deletes the old token names will break
   eight pages silently (CSS variables fail quietly). Leave the aliases and a
   comment saying why.
6. **Two truths for "changes".** The seats pane gets `changes` from the server
   (saved settings); the open seat computes it from the *draft*. They differ
   while a draft is unsaved for up to 400 ms. After every successful save and
   every ship, call `SeatsStore.patch` so the pane follows the open seat.

---

## 11. What "done" looks like

A person opens `/`, lands on the seat that needs them, drags one divider,
watches the lineup re-order with "up 1 / new / leaves" against what is live,
clicks a model to read exactly which axis put it there and which source said
it can call tools, and presses "Ship 2 changes" — without a page change, a
scroll between panels, or a number that was guessed.
