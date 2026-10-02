# EFF-A notes — where the back end chose, and why

Each choice keeps EFFORT.md section 1: a row's id never changes with effort.

1. **How a row reads another row.** The engine copies the `scored_as` row's
   observations and price over the reached model's bucket in the ranking's
   *working* observation table, before cost and axes are computed. So cost
   per task, router price multipliers (keyed on the reached model's prefixes)
   and measured tokens (keyed on `model_id`) all apply as before, and the
   shared cached table is never touched. A hand score given on the reached id
   still wins over the copied value, as hand scores always do.
2. **Unreachable rows are not resolved.** A model no router reaches is scored
   on its own row whatever the seat says (`effort_how: null`, `scored_as` =
   its id, `effort` = its own mode). It has no position, and reading another
   mode into a catalogue row would mislabel it.
3. **`modes` holds single-mode families too.** `family_modes` keeps every row
   that declares an effort, so a family with one published mode gives
   `(id, that effort, "one")` rather than `(id, None, "one")`.
4. **`-max` as an id suffix.** `effort_in_id` adds `max` to `_SLUG_SUFFIXES`
   for rule 3 only; `family_of` is unchanged (AA never writes `-max`).
5. **There is no PATCH settings route.** `PUT /v1/profiles/{name}/settings`
   already merges (it is the patch door); `effort` goes through it.
6. **Preview with no stored ranking at another effort** ranks into the LRU,
   not the store: the stored ranking stays the saved settings'.
7. **Seats `lineup` is null** when the stored ranking's effort differs from
   the saved one, the same rule as a hand-score change: the pane never ranks.
   `multi_mode` looks at the models in `live` and `lineup`.
8. **Model-card ladder `score`** is each row scored on the seat's weights with
   no effort applied (every row read as published), from the seat's ranking
   at "any" — the stored one if it is at any, else one ranked into the LRU.
   `reachable` means a router id matched that exact row (the bare row is
   reachable through `gw/sol`, the medium row usually is not, even though
   the seat is scored on it). `intelligence` reads
   `aa_llm:artificial_analysis_intelligence_index`.
9. **Settings writes do not touch YAML**, as before; `PUT /v1/profiles/{name}`
   (the write-back) writes `effort:` right after `ship`.
10. **`web/src/lib/types.ts` was regenerated** (`sieve export-types`): it is a
    generated file and `test_types_ts_is_current` fails otherwise. It is the
    only file under `web/` this branch touches; on a merge conflict,
    regenerate it.
11. **Pre-existing bugs fixed in passing:** `sieve score` crashed reading the
    retired `Rank.dominated_by`; the stdio MCP bridge sent the path's `name`
    in `set_ship`'s body, which `ProfileSettings` (extra=forbid) refused.
