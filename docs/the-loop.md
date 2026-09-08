# The loop

Phase 1 could answer *which model*. This is the part that keeps answering with
nobody at the keyboard: **measurement in, decision out, on a schedule, with the
outcome reported back.**

One command does all of it.

```bash
sieve run
```

That is what the timer calls, and it is the whole loop:

```
  pull every enabled source
        │  a source that is down does not stop the run
        v
  evaluate every profile        ranking, per-axis contribution, coverage
        │
        v
  decide                        switch · hold · suspend, one row per profile
        │
        v
  apply  ── only where policy.auto_apply is true ──> your targets
```

## What it may change without asking

**Nothing, unless a profile says so.** `policy.auto_apply` is per profile and
defaults to `false`. A profile without it is still pulled, ranked, decided and
recorded — the decision row is written either way — and then the chain is held
rather than shipped. Adding a target does not put it in charge of anything.

So the honest summary of an unattended install is:

| Always | Only with `auto_apply: true` |
| --- | --- |
| new observations stored | the chain written to your targets |
| every profile re-ranked | a live gateway's routing changed |
| a decision row per profile, per run | |

To let one seat ship on its own, put it in that profile's policy:

```yaml
policy:
  auto_apply: true
  margin: 3.0            # how many points a challenger needs to take the seat
  suspend_below_health: 0.75
```

Start with one profile you do not mind being wrong about, watch a few runs of
`sieve decisions`, then widen it.

## Turning it on

```bash
sudo cp deploy/sieve-run.service deploy/sieve-run.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sieve-run.timer
```

Check it:

```bash
systemctl list-timers sieve-run.timer     # when it next fires
journalctl -u sieve-run.service -n 50     # what the last run did
sieve decisions --limit 20                 # ...and why
```

## Turning it off

```bash
sudo systemctl disable --now sieve-run.timer
```

Nothing else has to be undone. The store is a file, the last chains stay where
they were written, and your gateway keeps routing exactly as it was — Sieve
writes a chain and then gets out of the way.

To stop only the *shipping* while keeping the measurement, set `auto_apply:
false` on every profile instead. The loop keeps running and keeps a record; it
just stops changing anything.

## The rate limit arithmetic

Artificial Analysis allows **1,000 requests a day** on the free tier and
publishes `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`
on every response.

One `sieve run` makes **six** AA requests:

| endpoint | requests |
| --- | --- |
| `/data/llms/models` | 1 |
| `/data/media/text-to-image` | 1 |
| `/data/media/image-editing` | 1 |
| `/data/media/text-to-video` | 1 |
| `/data/media/image-to-video` | 1 |
| `/data/media/text-to-speech` | 1 |
| **total per run** | **6** |

Hourly, that is `6 × 24 = 144` a day — about **14%** of the budget, leaving room
for manual runs, retries and the free-tier endpoints if you enable them (four
more per run, so `10 × 24 = 240`, still under a quarter).

Do not raise the frequency without redoing this. At every 15 minutes it is 576 a
day, and a second machine sharing the key puts you over: **the limit is per key,
not per host.** `RandomizedDelaySec=180` in the timer spreads a fleet across
three minutes so they do not all ask at the same second, but it does not change
the total.

OpenRouter and fal need no key and are not counted here.

### What happens when you do hit it

The HTTP client reads `X-RateLimit-Reset` and, on a throttled response, **waits
until that moment** rather than retrying immediately — three fast retries before
the reset are three more refusals charged against the same budget. The wait is
capped at 120 seconds: a reset an hour away should fail the run and let the next
timer pick it up, not hold a systemd job open for an hour.

`sieve pull` prints the remaining budget after every source, and `/v1/sources`
carries it, so you can see the headroom without waiting to be cut off.

## Watching it happen

`GET /v1/events` is a server-sent event stream with four kinds:

| event | when |
| --- | --- |
| `pull` | a source finished, with how many observations were added |
| `ranking` | a profile was re-ranked, with the new leader |
| `decision` | switch, hold or suspend, with the reason |
| `apply` | a chain was written to a target |

```bash
curl -N http://localhost:8110/v1/events
```

An agent can hold that open and react to a switch the moment it is decided,
rather than polling `/v1/recommend`.

**One honest limit:** the bus is in-process. Events are published by the API,
so a `sieve run` from a *timer* is a different process and its events do not
appear on a server's stream. What the timer does is still visible — every run
writes decision rows, and `/v1/decisions` serves them — but the live stream
covers work done through the API. Watch `journalctl -u sieve-run.service` for
the timer's own account of itself.

## When a source is down

The run continues. That is deliberate: yesterday's observations are still in the
store, and a ranking computed from slightly stale data beats no ranking at all —
your agents are still asking which model to use, and "the benchmark site is
having a bad morning" is not a reason to stop answering.

What you get instead:

- the failure is named on stdout and in the journal,
- the run still ranks every profile and writes every decision,
- and it **exits non-zero**, so `systemctl` shows the unit as failed and any
  monitor watching it fires.

A timer that can never fail is a timer nobody checks.

## Reading a run

```
cheap_bulk: switch: alibaba/qwen3-8-flash-next takes primary, no incumbent  [schedule]
quick_chat: switch: alibaba/qwen3-8-flash-next takes primary, no incumbent  [schedule]
out: wrote /opt/sieve/out/cheap_bulk.json, /opt/sieve/out/chains.csv
held (no auto_apply): quick_chat
run: 2 ranked, 2 decided, 1 shipped, 0.2s
```

Both profiles decided the same thing. One shipped because it opted in; the other
is on the last line, held. A run that changed nothing is as loud as one that
changed everything — otherwise "the schedule is working" and "the schedule is
stuck" look identical in a log.

## Trying it without consequences

```bash
sieve run --dry-run          # decide and report, write nothing
sieve run --no-pull          # evaluate what is already stored, ask nobody
sieve run --profile coder    # one seat
```

`--dry-run` needs no confirmation, because it writes nothing. `sieve apply`,
which always writes, needs `--yes`.
