# C — web scaffold, Field, Rankings, Sources (opus)

You own the `web/` scaffold, `web/src/lib/{tokens,scroll,chart,api}/**`,
`web/src/routes/(app)/{field,rankings,sources}/**` and the components
`Rail`, `Kpi`, `Scatter`, `AxisBars`, `ConfDots`. Read CONTRACTS §8 and
PLAN §8 first. The web talks only to `/v1`; types come from
`web/src/lib/types.ts` — never hand-edit it.

## Scaffold

SvelteKit (Svelte 5, runes) + TypeScript strict + Tailwind v4 + D3 (scale,
axis, shape only) + Lenis + Motion. `pnpm`. Adapter-static with SPA
fallback so FastAPI can serve `web/build`. `PUBLIC_SIEVE_API` env for the
base URL; `lib/api/client.ts` typed fetch with the error envelope of
CONTRACTS §6 and an SSE hook for `/v1/events`.

Tokens (`lib/tokens/tokens.css`, exposed to Tailwind as theme colours):
bg `#0B0C10`, panel `#13151B`, panel2 `#1A1D25`, rule `#242833`, ink
`#ECE8E1`, muted `#7C8290`, accent `#E9A23B`, reach `#7FA6FF`, good
`#5DBB8A`, warn `#E0704A`, bad `#E05A5A`. Fonts via Google Fonts:
Fraunces (display, 300/500 + italic), Hanken Grotesk (UI), IBM Plex Mono
(numbers and ids). Numbers always `tabular-nums`. Dark only; paint the
body background explicitly.

Layout: left rail 168 px (logo, Field · Profiles · Rankings · Chains ·
Sources · Pulse, footer with last pull time and next run from
`/v1/sources`), main pane; page transitions crossfade the main pane only.
Lenis for scroll; every motion behind `prefers-reduced-motion`.

## Field (`/field?modality=llm`)

Modality tabs from `/v1/modalities`. One Canvas scatter: y = chosen axis
(default `intelligence` for llm, `quality` for media), x = cost per task
for the *selected profile's* shape (log), all measured models as faint
points, reachable ones in `reach`, the current primaries of every profile
in `accent` with a ring. Hover tooltip (id, axis value, cost); click opens
the model in Rankings. 644 points at 60 fps — draw once, redraw on hover
via a hit index, not per frame. Axis pickers list only axes used by at
least one profile. KPI row: measured · reachable · matched · profiles.

## Rankings (`/rankings/[profile]`)

Profile picker grouped by modality. Table rows from `/v1/rankings/{p}`:
position, id + local ids, `AxisBars` (one bar per weighted axis, height =
axis value, accent for cost), score, `ConfDots` (5 dots = confidence
quintiles), health sparkline (7 d series when telemetry exists, else a
dash), reachable dot, "dominated by X" / "excluded: min_confidence" rows
dimmed at the bottom. Row 1 shows the flip line under the id. Sticky
header; virtualised list above 200 rows.

## Sources (`/sources`)

Cards from `/v1/sources`: enabled, last pull, rows stored, rate-limit
remaining and reset, warnings (new AA keys). "Pull now" posts to
`/v1/sources/{name}/pull` (needs a token; show the 401 politely). Below,
unmatched inventory ids from `/v1/inventory?unmatched=true` with an
"alias to…" combobox over `/v1/models?q=` that PUTs `/v1/aliases`.

## Done when

`pnpm lint`, `pnpm check`, `pnpm test` green; the three routes render on
the real snapshot with no console errors; Lighthouse ≥ 90 performance and
accessibility on Field; nothing scrolls horizontally at 390 px; keyboard
focus visible everywhere.
