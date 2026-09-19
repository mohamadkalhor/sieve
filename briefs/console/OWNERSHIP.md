# Console — who may edit what

This settles finding 5 of `REVIEW.md`. It overrides the ownership column of the
table in `CONSOLE.md` section 9 wherever the two differ. The cards run one after
another, in this order: A, B, C, D1, D2, E, F, G, then the UI test.

## Every package

- Appends under its own heading in `briefs/HANDOFF.md` (create the heading;
  never edit another package's lines).
- Owns its own tests: `web/tests/console/<package>-*.test.ts` and, where its
  acceptance names a browser check, `web/e2e/console-<package>.spec.ts`
  (`<package>` is `a`, `b`, `c`, `d`, `e`, `f`). The e2e files that existed
  before this work belong to G alone.

## A

- Also owns `web/package.json` and `web/pnpm-lock.yaml` (the two font packages
  only), and `web/src/lib/console/contracts.ts`: the structural interfaces the
  other packages compile against (`SeatSessionLike`, `SelectionLike`,
  `CardCacheLike`, `SeatsStoreLike`, `PaletteContext`, the typed context keys),
  shaped as `REVIEW.md` finding 4 says.
- Creates `routes/(app)/seats/+page.svelte`, `routes/(app)/seats/[name]/+page.svelte`
  and `+page.ts` as placeholders with three named slots (seats, seat,
  inspector), and wires `(app)/+layout.svelte` through factories, not through
  classes that do not exist yet.
- May make contrast-only edits in the untouched pages (a colour or a font
  weight, nothing else); lists every such edit in HANDOFF.

## After A, these files change hands

| file | new owner | what the others may do |
| --- | --- | --- |
| `routes/(app)/seats/[name]/+page.svelte` | D1 | D2 replaces the table slot only; E fills the inspector slot only |
| `routes/(app)/seats/+page.svelte` | F | nothing |
| `(app)/+layout.svelte` (store creation and context) | F | nothing |
| `console/shell/StatusBar.svelte`, `console/shell/CommandPalette.svelte` | F | nothing |

## B, C

- B owns `tests/fixtures/lineup_diff.json`. C reads it and never rewrites it;
  if it is missing, C blocks its card instead of inventing it.
- C may only append to `console/contracts.ts`, and says so in HANDOFF.

## G

- Runs last, and from then on may edit anything under `web/src/lib/console/`,
  `web/src/routes/`, `web/e2e/` and `web/tests/` to integrate, fix and make
  the suites green. It deletes the old files last, after the parity checklist.
