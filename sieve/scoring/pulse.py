"""What the gateway's own traffic says about a model, for the Pulse screen.

Health answers one question -- is this model working for *you*, today -- and
compresses it to a number the ranking can multiply by. Pulse is the same events
read the other way: the figures a person looks at when the number moved and they
want to know why.

Nothing here reads anyone's gateway logs. It reads only what callers reported
through `POST /v1/telemetry`, which is the only place this evidence exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sieve.contracts import TelemetryEvent
from sieve.scoring.health import _is_rate_limited


def percentile(values: list[float], share: float) -> float | None:
    """The nearest-rank percentile of `values`, or None when there are none.

    Nearest-rank, not interpolated: with a handful of calls an interpolated p95
    invents a latency nobody measured, and this number is read as "how slow does
    it get", which should be a real observation.
    """
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(share * len(ordered) + 0.5) - 1))
    return ordered[index]


class Pulse:
    """One model's traffic over one window. Built by `pulse()`."""

    __slots__ = ("errors", "events", "latencies", "rate_limited", "tokens_out")

    def __init__(self) -> None:
        self.events = 0
        self.errors = 0
        self.rate_limited = 0
        self.latencies: list[float] = []
        self.tokens_out: list[int] = []

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "events": self.events,
            "ok_rate": (self.events - self.errors - self.rate_limited) / self.events
            if self.events
            else None,
            "rate_limited_share": self.rate_limited / self.events if self.events else None,
            "p50_latency_ms": percentile([*self.latencies], 0.50),
            "p95_latency_ms": percentile([*self.latencies], 0.95),
            "median_tokens_out": percentile([float(t) for t in self.tokens_out], 0.50),
        }


def pulse(
    events: list[TelemetryEvent], now: datetime, hours: int = 24
) -> dict[str, dict[str, float | int | None]]:
    """`{model: figures}` over the last `hours`.

    A model with no events in the window is absent rather than present with
    zeroes: "nobody called it" and "everybody's call failed" are opposite facts
    and must not look alike.
    """
    since = now - timedelta(hours=hours)
    rows: dict[str, Pulse] = {}
    for event in events:
        if event.at < since:
            continue
        row = rows.setdefault(event.model, Pulse())
        row.events += 1
        if _is_rate_limited(event):
            row.rate_limited += 1
        elif not event.ok:
            row.errors += 1
        if event.latency_ms is not None:
            row.latencies.append(float(event.latency_ms))
        if event.tokens_out is not None and event.tokens_out > 0:
            row.tokens_out.append(event.tokens_out)
    return {model: row.as_dict() for model, row in rows.items()}


def observed_tokens_out(
    events: list[TelemetryEvent], now: datetime, days: int = 7, min_calls: int = 5
) -> dict[str, float]:
    """`{model: median output tokens}` from real calls, for costing a task.

    PLAN §2.1: every effort mode of a model is served at the same price per
    token, so the *rate* can never separate them -- only the tokens burned can,
    and no source we read publishes a per-task token count. The gateway's own
    traffic is the only place that number exists.

    `min_calls` guards against pricing a model off a handful of calls: below it
    the model is absent and costing falls back to the profile's declared shape,
    which is at least a stated assumption rather than a small sample dressed up
    as a measurement.
    """
    since = now - timedelta(days=days)
    seen: dict[str, list[float]] = {}
    for event in events:
        if event.at < since or not event.ok:
            continue
        if event.tokens_out is not None and event.tokens_out > 0:
            seen.setdefault(event.model, []).append(float(event.tokens_out))

    out: dict[str, float] = {}
    for model, values in seen.items():
        if len(values) < min_calls:
            continue
        median = percentile(values, 0.50)
        if median is not None and median > 0:
            out[model] = median
    return out
