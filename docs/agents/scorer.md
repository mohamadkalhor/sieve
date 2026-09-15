# Scorer: score the models no source has measured

A weekly card. Sieve ranks models on public benchmarks; some models the routers
serve have none, so they rank on price alone. Place what you quickly can, leave
the rest blank, and report.

## Budget, and the only calls you make

- **At most 12 models a run, and 2 web calls per model**: one `web_search`, and
  at most one page read. Nothing found: move on.
- **About 60 tool calls in total.** If you pass it, write what you have and finish.
- **Do not** write scripts, run python, read source code, explore the server,
  call any Sieve route not listed below, or look up how the kanban works.
  Everything you need is on this page.

```bash
. /etc/default/sieve-scorer; AUTH="Authorization: Bearer $SIEVE_SCORER_TOKEN"; J='content-type: application/json'
```

| Call | When |
|---|---|
| `curl -s "$SIEVE_URL/v1/unscored"` | once, at the start |
| `curl -s "$SIEVE_URL/v1/axes?modality=llm"` | once, for the axis names |
| `curl -s "$SIEVE_URL/v1/models?modality=llm&q=<word>&limit=20"` | to find a peer or a same-model record |
| `curl -s "$SIEVE_URL/v1/axis-values?modality=llm&models=<a>,<b>"` | the peers' values, once per model |
| `curl -s -X PUT "$SIEVE_URL/v1/aliases" -H "$AUTH" -H "$J" -d '{"alias":"<local_id>","model_id":"<id>","modality":"llm"}'` | same model, step 2 |
| `curl -s -X PUT "$SIEVE_URL/v1/hand-scores" -H "$AUTH" -H "$J" -d '{"local_id":"<local_id>","modality":"llm","name":"<name>","scores":{"<axis>":0.7}}'` | step 3 |
| `hermes kanban complete "$HERMES_KANBAN_TASK" --result "<report>"` | the last call |

## Per model

1. **Skip** it if `hand_by` names anyone but `scorer` (a person scored it), or
   if it is not a text model (music, speech: leave for a person).
2. **Search once**: `<name> benchmark`. Read one page at most: the vendor's
   model card, its Hugging Face card, or its Artificial Analysis / BenchLM page.
3. **Same model Sieve knows under another name?** Same vendor, version, size
   and mode, all four. Then alias it and move on.
4. **Otherwise score it** against 2 measured peers the page compares it with,
   or its base model for a variant (effort level, fine-tune, agent wrapper).
   Read the peers with `axis-values` and place it between them, axis by axis.
   - vendor-only numbers: take 10% off
   - a variant with no numbers: its base's values; a lower effort level loses
     a little on `reasoning`, `math`, `agentic_*`
   - only axes the page supports; **never** `cost`, `latency`, `speed`
5. **Nothing solid**: leave it, one line in the report.

## Report (the `--result`)

```
Linked: local_id -> id  [url]
Scored: local_id  axis=value ...  vs peer1, peer2  [url]  (vendor-only | independent | from base X)
Skipped (person's scores / not text): local_id ...
Nothing found: local_id ...
Not reached this week: local_id ...
```
