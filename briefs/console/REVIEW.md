Verdict: build after fixes

Evidence refers to the console branch reviewed before this report. Only this report changes; these are replacement instructions, not implemented fixes.

1. MUST-FIX — A, B — Complete the richer-row mapping, including absent cached axes.
Evidence: briefs/CONSOLE.md:279-299 promises one entry for every proposed axis and calls this serialization. sieve/contracts.py:361-377 has score, not raw, and no factor; sieve/profiles/control.py:457-478 retains only axes already present in the cache.
Replacement instruction: emit raw=rank.score, score=rank.final and factor=prefix_factor(rank.local_ids, proposed.prefix_weights). Iterate proposed.weights, not rank.axes; for an absent cached axis emit value=null, coverage=0, contribution=0. Pass proposed settings explicitly into the preview serializer. Test an added axis absent from the stored ranking, plus every row collection named in §4.2. Keep the two arithmetic invariants.

2. MUST-FIX — A, B, E — Capability provenance needs inventory evidence and conflict semantics.
Evidence: briefs/CONSOLE.md:325-333 specifies a source-table SELECT but also promises inventory evidence. sieve/profiles/control.py:490-507 merges fresh inventory after source capabilities; sieve/store/db.py:555-565 orders sources before merging. The acceptance at briefs/CONSOLE.md:928-929 incorrectly assumes no capability-table rows means all answers unknown.
Replacement instruction: gather source capabilities and fresh matching inventory capabilities separately; populate yes/no with has() applied to each observation, retaining conflicting witnesses. Compute answer with the existing capability_map merge, not a vote or an OR over witnesses. A model with no table rows but inventory evidence must report that evidence. Test no evidence anywhere, inventory-only evidence, explicit false, and conflicting source/inventory statements. Keep capability behavior unchanged in this additive package. Scope served_by inventory to the caller's connectors consistently with store.local_ids(owner_id), including stale entries only for those connectors; do not expose another owner's inventory labels.

3. MUST-FIX — B, F — Stored chains are not proof of current gateway state, and cached rankings can be stale.
Evidence: briefs/CONSOLE.md:120 and :240 call a stored chain what the gateway holds now; :252-259 deliberately forbid fresh ranking. sieve/api/routes/v1.py:769-783 invalidates a stored ranking after hand-score changes before previewing. sieve/profiles/control.py:481-486 uses cached reachable flags.
Replacement instruction: label live data as the last stored/applied chain, not a verified gateway read. In /seats, when hand.changed_at exceeds ranking.computed_at, return lineup/in_step/changes=null without ranking. Document that other comparisons use the cached ranking snapshot; do not claim current inventory agreement. Add a hand-score invalidation test and expose/cache timestamp wording if stale snapshot data is displayed.

4. MUST-FIX — A, C, D, E, F — Publish usable shared interfaces and dependency injection before parallel components.
Evidence: briefs/CONSOLE.md:579 gives SeatSession no SeatsStore dependency although :583 and :990 require patching it; :606 supplies no URL adapter despite the no-DOM state rule at :364; :613 lacks fallback row input and error detail; :541 references an undefined PaletteContext. E must compile against interfaces before D at :913.
Replacement instruction: A owns a shared console/contracts.ts with structural session/selection interfaces, PaletteContext and typed context keys; C may extend it only through an explicit handoff. Inject an onSaved/onShipped callback into SeatSession, and URL read/replace callbacks into Selection. Define CardCache.get(id, modality, fallbackRow), with error details and state, keyed by both modality and id. Layout provides structural interfaces/factories, not imports of not-yet-built concrete classes. D/E/F test with these same interfaces.

5. MUST-FIX — A, B, C, D, E, F, G — Fix ownership and acceptance ordering.
Evidence: briefs/CONSOLE.md:909-915 assigns placeholders to A and their replacement to D/F, gives A a contrast audit outside its enumerated files, gives G responsive/motion work but only e2e/deletion ownership, and gives only G HANDOFF ownership although :904-905 requires every package to append. C consumes B's fixture but depends only on A (:911, :936). A/D/E/F require e2e assertions while G owns all e2e files.
Replacement instruction: explicitly authorize A's temporary route files, then transfer named route ownership to D/F after A; allow A package manifests/lockfile for fonts and enumerated contrast-only edits. Give G integration ownership of console components/layout after D/E/F, plus e2e files. Each package owns a distinct package-named test file and its HANDOFF section. Require B before C's shared-fixture acceptance; do not duplicate that fixture. A owns shared interfaces; F gets the final layout/store wiring handoff after A. Enumerate these exceptions in package briefs before dispatch.

6. MUST-FIX — D, F, G — Serialize writes and prevent shipping an unsaved or outdated draft.
Evidence: briefs/CONSOLE.md:582-598 specifies debounced save/preview and one generation counter; :987-990 requires immediate sidebar patching. The old page launches save and refresh concurrently at web/src/routes/(app)/profiles/[name]/+page.svelte:324-338, and applies even when save fails at :531-547.
Replacement instruction: assign a revision on every edit immediately, invalidating prior preview eligibility before the debounce fires. Serialize/coalesce settings writes so an older PUT cannot finish last on the server. Track preview revision and successful saved revision independently from session-lifetime generation. Ship clears the debounce, drains pending writes, saves its immutable snapshot and aborts on save failure; permit apply only for the matching successful preview. Disable edits during apply or explicitly retain later edits as an unsaved draft. Drop late open/link/history/ship UI effects after close. Patch sidebar lineup/counts only from a preview corresponding to the successfully saved revision, never from whichever preview happens to be visible. Test reversed responses, edit-during-debounce, save failure with zero apply calls, and navigation during apply.

7. MUST-FIX — C, D — Bound split geometry and define threshold crossing.
Evidence: briefs/CONSOLE.md:474-484 reserves fixed stubs/gaps without a minimum bar width; :933-936 demands layout sums and a round-trip. A stub changing to a proportional segment changes the pixel/share mapping mid-drag.
Replacement instruction: define sum(segment.px)+GAP_PX*(n-1)=barPx for the weight track only, excluding the Add button. Use floating-point widths. When stubs/gaps cannot fit, render a wrapping list of exact-percent controls instead of negative widths or horizontal overflow; disable divider dragging in that presentation. Freeze parties, weights, segments and pxToShare scale at pointerdown and compute each move from total displacement against that snapshot. Clamp transfer to the pair total; zero movable width yields no transfer. Test narrow/all-stub/one-axis layouts and crossing the stub threshold. Apply round-trip assertions only within the same layout regime with a specified floating-point tolerance, not across discontinuities.

8. MUST-FIX — C, D — The reused renormalise helper can violate weight invariants with locks.
Evidence: briefs/CONSOLE.md:441 and :465-467 require unit sums; web/src/lib/rank/weigh.ts:80-100 clamps to 1 rather than the unlocked room and applies drift to a free axis. Reset restores loaded keys without filtering locks at web/src/routes/(app)/profiles/[name]/+page.svelte:442-445.
Replacement instruction: in settings.moveWeight clamp the requested value to [0,1-sum(other locked weights)]; if no other unlocked axis exists, retain the current weight. Reject non-finite values. Rebuild reset order from loaded keys and intersect locks with restored keys. Preserve preset/add/remove behavior otherwise. Add nonnegative-weight assertions in addition to sum assertions and tests for all-other-axes-locked and reset after adding/locking a new axis. Do not change the shared legacy helper as an unowned side effect.

9. MUST-FIX — A, E — A missing old-server field or failed lookup must not become a missing price.
Evidence: Listed.cost_per_task is optional at briefs/CONSOLE.md:286, yet money accepts only number|null at :552-554 and ModelRow passes it directly at :772. Model-card fallback uses a fuzzy query with limit 5 (:335-338). History has no failure state (:837-839); the existing page maps failure to [] at web/src/routes/(app)/profiles/[name]/+page.svelte:562-563.
Replacement instruction: distinguish absent cost field ('server does not report task cost') from explicit null ('no cost available'). For fallback price lookup include modality and accept only an exact id+modality match; paginate until found/end or use the existing exact model route. Preserve lookup errors instead of returning price=null. Give history and card explicit error+retry states and preserve old data as stale during refresh. Do not show 'No posted price' until a successful price lookup actually says null.

10. MUST-FIX — C, D, E — Missing/leaving rows cannot satisfy Listed without invented scores.
Evidence: preview.missing is only {id,name} at sieve/api/routes/v1.py:825-826; ModelRow requires Listed at briefs/CONSOLE.md:762-765. :787-789 asks for leaving models not necessarily in pool. :789-792 claims every empty lineup fails needs, even though manual=[] or removals can also empty it.
Replacement instruction: introduce a discriminated display-row union for ranked rows versus identity-only missing/leaving rows. Identity-only rows show unknown score/cost/capabilities and disable unsupported actions, never synthesize score=0. Deduplicate leaving ids against all displayed collections, not only next. Use neutral empty text ('Nothing would ship with these settings') unless an explicit returned reason supports a narrower claim. Test empty manual list, all removed, and an unreachable leaving id.

11. MUST-FIX — C, E — The explanation must distinguish raw contributions from final score ordering.
Evidence: briefs/CONSOLE.md:514-519 uses raw contributions/carriedBy but :815-820 heads them with the final score and attributes the final gap mostly to an axis. sieve/profiles/control.py:474-476 multiplies raw score by health and prefix factor; pins/manual can place a lower-scoring row first.
Replacement instruction: title the axis block with the raw score and separately show the multiplier equation to final score. Compare signed final gaps; do not say 'behind' for a higher-scoring pinned/manual follower or a tie. Label carriedBy output as the raw-axis gap, and when multipliers determine the final order explain that explicitly rather than attributing it to an axis. Test identical axes/different trim, a tie, and a pinned lower-score leader. Handle zero maximum weight without division by zero.

12. MUST-FIX — D, G — Reset is not full discard; define the retained intent honestly.
Evidence: G requires smoke's discard intent at briefs/CONSOLE.md:959-961 and D says 'discard by Reset' at :945, but settings.resetTo only accepts weights (:446). The old reset changes weights/order only at web/src/routes/(app)/profiles/[name]/+page.svelte:442-445; smoke's Discard is explicit at web/e2e/smoke.spec.ts:80-105.
Replacement instruction: retain Reset weights with its existing scope and add a distinct Restore opened settings action backed by a full immutable settings snapshot if full discard parity is intended. For this build, require that action to restore weights, mode, ship, needs, manual, pins, removals and trim, then save/preview; it does not roll back an already applied chain. Test all those fields, and do not rename weight reset to discard.

13. MUST-FIX — A, D, E, F, G — Complete keyboard/touch and responsive contracts.
Evidence: briefs/CONSOLE.md:724-729 omits separator min/max, :778-780 defines row keyboard shortcuts without excluding nested controls, :848 requires both 24px handles and 44px targets, and :981 activates touch-action only during drag. Modal/drawer behavior at :678-682 and :846-848 omits focus containment/background inertness. The existing responsive suite checks 390/820/1280, not the claimed widths (web/e2e/responsive.spec.ts:14-18).
Replacement instruction: set touch-action before pointerdown on handles, not only during drag; provide equivalent exact controls with 44px mobile targets. Expose separator min=0/max=pair total and current party labels. Ignore row shortcuts from nested interactive/editable elements and IME composition. Define modal sheets/dialogs with focus entry, trap, inert background, Escape and return-focus; give collapsible group headings buttons with aria-expanded. At every intermediate width collapse nav before it overflows. Test 375/768/900/1024/1280/1440, zoom, touch cancellation, keyboard-only use and reduced motion. Add a mobile status-bar overflow strategy without hiding errors.

14. MUST-FIX — G — The existing performance test contradicts server-authoritative selection.
Evidence: briefs/CONSOLE.md:25-28 forbids client selection and :963 preserves the rerank budget. web/e2e/rerank.perf.spec.ts:233-255 deliberately returns preview 404 and expects local ranking; :283-333 requires every input to reorder rows.
Replacement instruction: rewrite the test to measure local split-control updates and separately DOM application of successful authoritative preview responses, retaining the existing numeric timing budgets for comparable DOM work. Exclude network/debounce from render timings and report them separately. On preview 404 assert an error/stale state, never local shipping selection. Keep old deep-link, reduced-motion, isolation and 401 intents as separate tests.

15. SHOULD — A, B, F — Define degraded endpoint results and status counts precisely.
Evidence: briefs/CONSOLE.md:267-268 specifies /seats fallback, but SeatsStore.degraded needs to know it ran (:560-561). :343-345 asks for the exact /unscored length without its rows; sieve/profiles/hand.py:229-269 counts unmatched inventory rows individually, deduplicates matched model/modality pairs and excludes observed models, retaining hand-scored unobserved models.
Replacement instruction: expose fallback metadata explicitly from the client; a failed chain request must retain an error rather than become a known empty chain. Implement the unscored count as the sum of unmatched fresh slash-containing rows and distinct fresh matched model/modality pairs without observations, with the same exclusions as hand.unscored. Test equality to the actual endpoint across duplicate inventory ids, multiple modalities, stale rows, hand scores and combo ids. Distinguish absent schedules from a successfully read empty/off schedule before saying 'no schedule'.

16. SHOULD — A, G — Measure Scatter against the actual scroll viewport.
Evidence: web/src/lib/components/Scatter.svelte:84-100 uses document-coordinate top and window height; briefs/CONSOLE.md:646-650 replaces only scrollY and subtracts a status constant.
Replacement instruction: pass or resolve a documented shell viewport element, measure its bottom minus the chart top and relevant padding in one coordinate system, and observe viewport resizing. Preserve the existing floor/ceiling behavior deliberately. Verify Field after internal scrolling and after control wrapping; do not mix document coordinates with scroll-container coordinates.

17. SHOULD — A, G — Retain all auth links and constrain branding to data/dependencies.
Evidence: web/src/lib/components/Rail.svelte:156-168 includes Account, owner-only Admin and a checking state, while briefs/CONSOLE.md:665-666 lists only role/sign-out/token. The generic copy rule at :16-18 conflicts literally with dependency font names (:162-163) and a hard-coded provider example in abilities.who (:527).
Replacement instruction: explicitly retain Account, role-gated Admin, checking, login return URL and CSRF logout behavior. Render capability witness names from response data, never a fixed provider/person/host string. Treat dependency names and returned source identifiers as necessary technical identifiers, not deploy-specific branding; use neutral fixtures and same-origin auth URLs. Audit newly committed copy/config for deployment identifiers without copying them into this report.

## Package A findings
1, 2, 4, 5, 9, 13, 15, 16, 17

## Package B findings
1, 2, 3, 5, 15

## Package C findings
4, 5, 7, 8, 10, 11

## Package D findings
4, 5, 6, 7, 8, 10, 12, 13

## Package E findings
2, 4, 5, 9, 10, 11, 13

## Package F findings
3, 4, 5, 6, 13, 15

## Package G findings
5, 6, 12, 13, 14, 16, 17
