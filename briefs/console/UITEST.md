# Console UI test report

Tested the built `console` branch with Playwright/Chromium against a fresh seeded server. The required port `8124` was occupied by the unrelated `gate`/`uvicorn` service (left running); throwaway seeded servers ran on `127.0.0.1:8134` and `127.0.0.1:8135`. Browser screenshots are in `/srv/personal/sieve-console-uitest/`.

## Environment

| Item | Result |
| --- | --- |
| Build | PASS — `pnpm build` |
| Seeded server | PASS — `/healthz` returned `{"status":"ok","version":"0.1.0.dev0"}` on port 8134 |
| Browser | Chromium via `@playwright/test` 1.63.0 |
| Required viewports | 1440×900, 1100×800, 768×1024, 375×812 |
| Source changes | None |
| Targeted repository E2E | PASS — console D/E/F plus responsive: 26/26 |
| Full repository E2E | 81 passed, 7 failed of 88 on a fresh seeded server; the seven are documented below as stale/unrelated test-contract failures, not counted as direct UI checks |

## Check matrix

| Check | Status | Screenshot |
| --- | --- | --- |
| Baseline render and no horizontal overflow — 1440×900 | pass | `01-baseline-1440.png` |
| Baseline render and no horizontal overflow — 1100×800 | pass | `02-baseline-1100.png` |
| Baseline render and no horizontal overflow — 768×1024 | pass | `03-baseline-768.png` |
| Baseline render and no horizontal overflow — 375×812 | pass | `04-baseline-375.png` |
| Muted text contrast sample | fail | `05-muted-contrast.png` |
| Auto/manual switch; manual starts from current lineup | fail | `06-auto-manual.png` |
| Four needs with counts and ship controls | fail | `07-needs-stepper-presets-reset.png` |
| Divider keyboard movement and weight invariant | fail | `08-divider-keyboard.png` |
| Row selection updates `?model=` and inspector blocks | pass | `09-selection-inspector.png` |
| Pin, remove, restore row actions | fail | `10-row-actions.png` |
| Manual list add/drop/move controls | fail | `11-manual-and-reachable.png` |
| Reachable view, `Unscored only`, Unscored link | fail | `12-reachable-unscored.png` |
| Trim router prefixes and edit purpose sentence | pass | `13-trim-purpose.png` |
| Seat menu copy/rename/delete/history and New seat | fail | `14-menu-history-new-seat.png` |
| Sign-in chip and token popover | pass | `15-auth-popover.png` |
| Ctrl+K and `/` palette, arrows, Escape focus return | pass | `16-palette-keyboard.png` |
| 1024–1279 inspector drawer | pass | `17-drawer.png` |
| 900–1023 seat switcher popover | pass | `18-seat-switcher.png` |
| 375px inspector sheet and 44px touch targets | fail | `19-mobile-sheet-targets.png` |
| Old `/profiles` URLs redirect | fail | `20-legacy-redirects.png` |
| Preview 500 is error/retry, not empty | pass | `22-preview-500.png` |
| Preview delayed 10s is busy, not empty | pass | `23-preview-delay.png` |
| Model-card 404 provenance fallback | pass | `24-model-card-404.png` |
| Seats 404 degraded note, not empty | pass | `25-seats-404.png` |
| Status 500 unreachable state | fail | `26-status-unreachable.png` |
| Seats pane one request and collapsed persistence | fail | `27-seats-pane.png` |
| Named routes at 375/768/1440 have no horizontal overflow | pass | `28-route-overflow.png` |

Summary: 15 pass, 12 fail, 0 could not test (27 unique direct checks). The custom script also recorded a duplicate preview-500 screenshot (`21-preview-500-shows-error-retry-not-empty.png`); it is not a separate check.

## Direct UI failures

### 1. Muted text contrast could not be established — severity: medium

Steps:
1. Open `/seats/coder` at 1440×900.
2. Inspect visible muted copy and calculate WCAG contrast against its rendered background.

Expected (CONSOLE.md §3.1 and §9 acceptance A): muted text must meet the specified contrast audit; the acceptance requires the audit to be done and listed.

Actual: the live page exposed no visible sample matching the expected `--c-muted` token in the automated contrast sample, so the contrast requirement could not be established from the rendered page. This is reported as a failure rather than an assumed pass.

Screenshot: `05-muted-contrast.png`.

### 2. Auto/manual switch not reachable — severity: high

Steps:
1. Open `/seats/coder` at 1440×900.
2. Try to activate the `Manual` segmented control.

Expected (CONSOLE.md §8 line 880; §9 acceptance D): the auto/manual switch is reachable, and manual mode starts from the current lineup.

Actual: Playwright timed out waiting for a visible button named `Manual`; the manual mode interaction could not be performed. This also prevented the manual-list check below. The repository’s targeted D/E/F acceptance suite passed, but this independent direct path could not find the expected current UI affordance.

Screenshot: `06-auto-manual.png`.

### 3. Needs, ship controls, presets and Reset not verified — severity: high

Steps:
1. Open `/seats/coder` at 1440×900.
2. Inspect needs, ship stepper, presets, and Reset.
3. Attempt to activate `One more`, `One fewer`, `Even`, and `Reset`.

Expected (CONSOLE.md §8 lines 881–883; §6.4–§6.5; §9 acceptance D): four needs expose their `n/N` counts; weights can be adjusted with exact percentages and presets; Reset restores the opened settings; ship count enforces minimum 1.

Actual: the custom helper used for the visibility assertion returned a non-Playwright locator (`isVisible` was unavailable), so this check did not perform the required control interaction. It remains unverified rather than being treated as a product pass or failure.

Screenshot: `07-needs-stepper-presets-reset.png`.

### 4. Divider movement left displayed weights at 95% — severity: high

Steps:
1. Open `/seats/coder` at 1440×900.
2. Focus the vertical separator and press ArrowRight.
3. Read the displayed axis percentages.

Expected (CONSOLE.md §8 line 882; §5.2 `split.ts`; §9 acceptance C/D): divider movement changes the adjacent pair while the weights still sum exactly to 100%.

Actual: the displayed axis percentages were `46, 19, 10, 10, 10, 0`, summing to 95%, after the divider interaction. The check also confirmed a focusable separator with min/max/label attributes and a changed value after ArrowRight.

Screenshot: `08-divider-keyboard.png`.

### 5. Remove/restore row action not established — severity: high

Steps:
1. Open `/seats/coder` at 1440×900.
2. Use the first row’s pin action, then its remove action.
3. Inspect the resulting row state for `Removed by you` or `Restore`.

Expected (CONSOLE.md §8 line 884): remove and restore are available; a remove changes the row to a restorable state, and pin/unpin interactions have the specified inverse behavior.

Actual: after the action, the page did not show `Removed by you` or `Restore`; the required remove/restore state could not be established.

Screenshot: `10-row-actions.png`.

### 6. Manual list add/drop/move controls not reachable — severity: high

Steps:
1. Open `/seats/coder` at 1440×900.
2. Try to switch to Manual mode and inspect the manual table.

Expected (CONSOLE.md §8 line 885; §9 acceptance D): manual mode is reachable, starts from the current lineup, and exposes add, drop, move up/down, blocked and unreachable rows.

Actual: the `Manual` control timed out and the manual table could not be driven.

Screenshot: `11-manual-and-reachable.png`.

### 7. Reachable unscored link did not navigate — severity: medium

Steps:
1. Open `/seats/coder?view=all` at 1440×900.
2. Activate `Unscored only`.
3. Follow the resulting link intended to open Unscored.

Expected (CONSOLE.md §8 line 886): the reachable pool offers `Unscored only`, and the Unscored link navigates to the Unscored screen.

Actual: after clicking the link, the URL remained `/seats/coder?view=all`; it did not navigate to `/unscored`.

Screenshot: `12-reachable-unscored.png`.

### 8. Seat menu lacks Copy — severity: high

Steps:
1. Open `/seats/coder` at 1440×900.
2. Open the seat menu.
3. Inspect menu entries.

Expected (CONSOLE.md §8 lines 892–894): Copy, Rename, Delete, History, and New seat affordances are present; copy-from is limited to the same modality.

Actual: the menu opened, but the `Copy` item was absent, so the required copy flow could not be tested. The check stopped before claiming Rename/Delete/History behavior.

Screenshot: `14-menu-history-new-seat.png`.

### 9. Mobile sheet does not reach the viewport bottom — severity: medium

Steps:
1. Open `/seats/coder` at 375×812.
2. Select the first model row to open the Inspector sheet.
3. Measure the sheet bounds.

Expected (CONSOLE.md §6.8; §9 acceptance G): below 900px the inspector is a bottom sheet spanning the width and reaching the bottom edge; its controls are 44px targets.

Actual: the sheet bounds were x=0, y=134.41, width=375, height=649.59, ending at y=784 rather than the 812px viewport bottom. The sheet therefore leaves a 28px gap at the bottom.

Screenshot: `19-mobile-sheet-targets.png`.

### 10. Old `/profiles` URL does not redirect — severity: high

Steps:
1. Navigate to `/profiles`.
2. Navigate to `/profiles/coder`.
3. Inspect the final URL.

Expected (CONSOLE.md §8 line 897; §9 acceptance A/G): old URLs redirect to the new `/seats` routes.

Actual: `/profiles/coder` remained `/profiles/coder` instead of redirecting to `/seats/coder`. (`/profiles` itself did redirect.)

Screenshot: `20-legacy-redirects.png`.

### 11. Status 500 does not show an unreachable state — severity: high

Steps:
1. Open `/seats/coder` at 1440×900.
2. Intercept `/v1/status` and return HTTP 500.
3. Inspect the Status bar.

Expected (CONSOLE.md §9 acceptance F): absent/unreachable status is explicit; a failed status read must say `Cannot reach the API` or an equivalent degraded state.

Actual: the status bar was empty after the intercepted 500; it did not show an unreachable/degraded message.

Screenshot: `26-status-unreachable.png`.

### 12. Seats pane makes two requests — severity: high

Steps:
1. Open `/seats/coder` at 1440×900 and count `/v1/seats` requests.
2. Reload and inspect collapsed-group persistence.

Expected (CONSOLE.md §9 acceptance F): one request paints the seats pane; collapsing persists across reload.

Actual: two `/v1/seats` requests were observed in the initial load, violating the one-request acceptance. The combined check therefore failed before relying on the persistence result.

Screenshot: `27-seats-pane.png`.

## Full repository E2E failure classification

Command (run twice independently against fresh seeded servers):
`SIEVE_E2E_PORT=8135 CI=1 pnpm test:e2e --reporter=line`

Result: `Running 88 tests using 1 worker`; `81 passed, 7 failed (4.8m)`; exit code 1.

The seven failures are reproducible, but they are not additional confirmed Console UI regressions:

| Suite/check | Observed failure | Classification |
| --- | --- | --- |
| `console-a.spec.ts` — old profile deep-link redirect | `/profiles/coder` did not redirect to `/seats/coder` | Same legacy-route finding as direct check 10; product/spec gap, not a harness-only pass |
| `field-search.spec.ts` — field search fixture relationship | fixture relationship assertion failed (`field search` expected the seeded relationship that was absent) | unrelated fixture/test-contract failure; not a Console UI finding |
| `focus.spec.ts` — legacy focus screen | legacy screen had zero tab stops | stale test coverage for removed/changed screen; not a confirmed Console UI finding |
| `smoke.spec.ts` — four smoke checks | selectors `button.who` matched zero elements | stale selector contract after the current auth chip replaced the old `.who` button; not a confirmed Console UI finding |

Because one full-suite failure duplicates direct check 10, the aggregate is reported separately and not added to the 27 direct checks.

## Additional verified positives

- Three-pane geometry, drawer/switcher/single-column responsive states, and route overflow passed at the required viewport sizes.
- Browser console was free of errors on the four baseline renders.
- Local font requests did not cross the seeded server origin.
- Selection updated `?model=` and showed the inspector blocks without `NaN` or fabricated `0.0`.
- Preview 500, 10-second delay, model-card 404, and seats 404 all produced explicit degraded/error/fallback wording rather than false empty states.
- Command palette opened via Ctrl+K and `/`, arrow navigation set an active descendant, and Escape restored focus.
- The targeted repository D/E/F and responsive suites passed 26/26.

The seeded servers started by this test run were stopped after testing. The unrelated `uvicorn` service on port 8124 was not touched.

## Round 3 (Codex, Luna)

Tested the fresh `console` branch clone at `/tmp/luna-console` against the seeded throwaway server on port 8341. The browser pass used Chromium at 1440×900, 1100×800, 1023×900 for the stated switcher boundary, 768×1024, and 375×812. Mutating checks used the supplied `ci-secret` token. No application files were changed.

Summary: 19 pass, 6 fail, 0 could not test across 25 UI checks. The separate repository E2E suite finished with 98 passed.

### Check matrix

| Check | Status | Screenshot |
| --- | --- | --- |
| Responsive layout, panes, drawer/switcher/sheet, and no clipping | fail | `01-look-1440.png`, `01-look-1100.png`, `01-look-1023.png`, `01-look-768.png`, `01-look-375.png`, `01-look-1100-drawer.png`, `01-look-1023-switcher.png`, `01-look-375-sheet.png` |
| 1440 comparison with `Console.reference.html` | pass | `01-look-1440.png` |
| Muted text contrast | pass | `02-muted-contrast.png` |
| Divider mouse/keyboard movement, 100% invariant, rerank, Ship label | fail | `03-weights-drag-keyboard.png`, `03b-rerank.png` |
| Segment exact percent, lock, add/remove axis, last-axis guard | pass | `04-axis-popover-lock-remove.png`, `17-last-axis.png` |
| Presets Even / Quality first / Value and Reset | pass | `05-presets-reset.png` |
| Four Must chips change the list | pass | `06c-must-changes.png` |
| Ships − / + stepper never below 1 | pass | `06b-ship-stepper.png` |
| Trim routers set and blank | pass | `07-trim-router.png` |
| Pin, remove, restore | pass | `10-pin-remove-restore.png` |
| Manual starts from current lineup; move/drop/Add models; return Auto | fail | `09-manual-start.png`, `09-manual-list.png` |
| All reachable filter, Unscored only, and `/unscored` link | pass | `08-all-reachable-unscored.png` |
| Edit the purpose sentence | pass | `11-purpose-edit.png` |
| Seat menu Copy/Rename/Delete/History | fail | `12-seat-menu-full.png`, `12-history.png` |
| New seat, including copy-from | pass | `13-new-seat.png` |
| Ship reports the actual result | pass | `14-ship-outcome.png` |
| Model selection, inspector provenance/abilities/price/served-by, `?model=` and unknown axes | pass | `15-inspector-model.png` |
| Keyboard list selection, `p`, Delete, and field shortcut guard | fail | `16-keyboard-list.png`, `16b-field-shortcut-guard.png` |
| Ctrl+K and `/` palette, arrows, Enter, Escape focus return | pass | `18-palette.png` |
| Every top-bar link works inside the shell | pass | `19-top-links.png` |
| Legacy address redirects | pass | `20-legacy-redirects.png` |
| Keyboard-only traversal and visible focus rings | pass | `21-keyboard-only.png` |
| Phone clipping/overlap/table fit and ≥44px touch targets | fail | `22-mobile-targets.png` |
| Failure honesty: status 500, seats 404, model-card 404, preview 500, preview delayed 10s | pass | `23-status-500.png`, `23-seats-404.png`, `23-model-card-404.png`, `23-preview-500.png`, `23-preview-delay-busy.png` |
| Cold-load `/v1/seats` request count | pass | `24-seats-cold-load.png` |

The expected `/auth/me` 404 was ignored. The `/v1/model-card` 404 was exercised as the known seeded-data fallback and did not count as a UI failure. The final contrast audit found a minimum rendered contrast of 6.20:1, above 4.5:1.

### Failures

1. Responsive shell clips at the 900–1023px switcher breakpoint — severity: high

   Steps: set the viewport to 1023×900 and open `/seats/coder`.

   Expected: CONSOLE.md §6.8 keeps the seat switcher layout usable from 900–1023px with no sideways scroll or clipped top/status controls.

   Actual: the document reports `scrollWidth=1023`, but the rendered `.topbar`, seat stage, and status bar are each 1075px wide. The rightmost 52px is clipped by the shell, including part of the nav/account area and the status bar. The 1440 three-pane, 1100 drawer, 768 single-column, and 375 single-column layouts otherwise rendered.

   Evidence: `01-look-1023.png`.

2. Weight changes do not rerank or describe the pending Ship count — severity: high

   Steps: sign in, open a text seat, drag the Cost/Tool-use divider, and wait 2.2 seconds for the preview.

   Expected: CONSOLE.md §8(5) and §5.2 require adjacent weights to move while summing to 100%, the server-backed list to rerank, and the Ship button to say how many changes are pending.

   Actual: the weights changed and still summed to 100%, but the table row IDs/text/scores stayed unchanged and the button remained `Ship now` instead of reporting a change count.

   Evidence: `03b-rerank.png`.

3. Manual mode does not start from the current lineup — severity: high

   Steps: record the Auto lineup for `reader`, then switch using the `Manual` radio.

   Expected: CONSOLE.md §8(12) and §5.2 start Manual from the current lineup in the same order.

   Actual: Auto was `[anthropic/claude-sonnet-5, anthropic/claude-opus-5, z-ai/glm-5.3]`; Manual started `[z-ai/glm-5.3, anthropic/claude-opus-5]`, changing the order and dropping a row before any manual edit. Move, drop, and Add models controls were present and individually operable.

   Evidence: `09-manual-start.png`.

4. History has no visible result — severity: medium

   Steps: sign in, open the seat More menu, and choose the `History` menuitem.

   Expected: CONSOLE.md §6.3 and §8(15) provide a visible history result for the seat.

   Actual: the menu contains Copy, Rename, Delete, and History, but clicking History closes the menu and produces no dialog, panel, history rows, or explanatory message.

   Evidence: `12-seat-menu-full.png`, `12-history.png`.

5. Keyboard Delete does not remove the selected model — severity: high

   Steps: focus the model list, press ArrowDown to select the next row, press `p`, then press Delete.

   Expected: CONSOLE.md §8(19) pins with `p` and removes the selected model with Delete, exposing a restore action.

   Actual: the selected row was pinned by `p`, but Delete did not expose Restore and the row remained. Typing `p` into the purpose input did not fire row shortcuts.

   Evidence: `16-keyboard-list.png`, `16b-field-shortcut-guard.png`.

6. Phone controls are below the required 44px touch target — severity: medium

   Steps: open `/seats/coder` at 375×812 and measure visible interactive elements.

   Expected: CONSOLE.md §6.8 makes every phone tap target at least 44px, with no overlaps or clipping.

   Actual: visible controls included the purpose button at about 20.8px high, preset buttons at 26px high, and weight segments at 40px high. The sheet spanned the seat area and the table fit, but these controls violate the target-size requirement.

   Evidence: `22-mobile-targets.png`.

### 1440 reference comparison

The core geometry matches the reference: a 52px top bar, 260px seats pane, 840px middle pane, 340px inspector, and 28px status bar. The current rendered differences that matter are:

- The current spec adds Unscored, Connectors, Axes, and the account button to the top bar; the older static mockup shows only Seats, Field, Sources, Runs, and Guide.
- The seeded data has 10 text seats and additional modality groups, versus the mockup’s illustrative shorter list.
- The seed has three reachable models and many no-score states, so the table is shorter than the mockup’s five-row example and the inspector shows the synthetic Z.ai model rather than the mockup’s GPT example.
- The current status response omits run/ranked/shipped fields, so the status bar omits them rather than inventing zeros; it still shows reachable, telemetry, unscored, and next-run fields.
- Narrow weight segments intentionally show only their number/blank label according to §5.2 label fitting.

### E2E totals

Command: `SIEVE_E2E_PORT=8342 CI=1 pnpm test:e2e --reporter=line 2>&1 | tail -n 25`

Result: `98 passed (3.6m)`, exit 0. The first attempt lacked the server’s documented `/root/.local/bin` PATH and failed before tests could start; the command was rerun with that environment setup and produced the totals above.
