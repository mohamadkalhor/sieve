"""A little real traffic in the smoke store, before the plan is computed.

Effort does not change the rate. Every mode of `gpt-6-astra` is served at
$10/$50 per million tokens, so on posted price its six modes are one vertical
line -- six dots stacked over one x. The only thing that separates them is how
many tokens come back, and that number exists nowhere except a gateway's own
calls.

So the smoke store gets some. Without it the cost-per-task axis has nothing
measured on it, and the node shape that says "this one was measured, the rest
are posted prices" would ship untested.

Written straight into the store rather than posted to `/v1/telemetry`, because
`sieve plan --store` computes the ranking that the Field reads, and telemetry
arriving after that plan is not in it.

    uv run python web/e2e/seed-telemetry.py <store.db>
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sieve.contracts import TelemetryEvent
from sieve.store import Store

#: Median output tokens per mode. Rising with effort is the whole phenomenon:
#: a mode that thinks longer returns more tokens at the same rate per token.
MEDIAN: dict[str, int] = {
    "non-reasoning": 120,
    "low": 260,
    "medium": 480,
    "high": 900,
    "xhigh": 1500,
    "max": 2400,
}

#: Two families that publish every mode at one rate -- the case the Field's
#: cost-per-task axis exists to make visible.
FAMILIES = ("openai/gpt-6-astra", "openai/gpt-5.6-luna")

#: `observed_tokens_out` ignores a model with fewer than five calls, so a small
#: sample is never dressed up as a measurement. Eight clears it.
CALLS = 8


def main(db: str) -> int:
    store = Store(Path(db))
    now = datetime.now(UTC)
    events: list[TelemetryEvent] = []
    for model in store.models("llm"):
        if model.family not in FAMILIES or model.effort not in MEDIAN:
            continue
        median = MEDIAN[model.effort]
        for i in range(CALLS):
            events.append(
                TelemetryEvent(
                    model=model.id,
                    ok=True,
                    status=200,
                    latency_ms=200 + i,
                    tokens_in=1200,
                    tokens_out=median + (i - CALLS // 2) * 5,
                    at=now - timedelta(minutes=i * 7),
                )
            )
    added = store.add_telemetry(events)
    print(f"telemetry: {added} calls over {len({e.model for e in events})} models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
