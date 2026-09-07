"""Health from telemetry: what actually happened when you called the model.

    health = 1 - error rate(24h) - 0.5 * rate-limit rate(24h)

Rate limiting counts half: a 429 is a real problem for a primary, but it is the
gateway's queue rather than the model failing, and it usually clears. A 429 is
therefore counted in the rate-limit term **only** -- counting it as an error as
well would make being throttled worse than failing outright, which is backwards.

With no telemetry health is 1.0 -- Sieve does not punish a model for the absence
of evidence. A profile that wants proof sets `policy.require_telemetry`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sieve.contracts import TelemetryEvent

WINDOW_HOURS = 24
SERIES_DAYS = 7
RATE_LIMIT_WEIGHT = 0.5
RATE_LIMIT_STATUS = 429


def _is_rate_limited(event: TelemetryEvent) -> bool:
    return event.status == RATE_LIMIT_STATUS


def health(events: list[TelemetryEvent], now: datetime) -> dict[str, float]:
    """`{model: health}` over the last 24 hours, 1.0 for anything unseen."""
    since = now - timedelta(hours=WINDOW_HOURS)
    counts: dict[str, list[int]] = {}
    for event in events:
        if event.at < since:
            continue
        row = counts.setdefault(event.model, [0, 0, 0])  # total, errors, rate limits
        row[0] += 1
        if _is_rate_limited(event):
            row[2] += 1
        elif not event.ok:
            row[1] += 1

    out: dict[str, float] = {}
    for model, (total, errors, limited) in counts.items():
        if total == 0:
            out[model] = 1.0
            continue
        value = 1.0 - (errors / total) - RATE_LIMIT_WEIGHT * (limited / total)
        out[model] = max(0.0, min(1.0, value))
    return out


def health_series(
    events: list[TelemetryEvent], now: datetime, days: int = SERIES_DAYS
) -> dict[str, list[float | None]]:
    """One health number per day, oldest first, for the sparkline the API serves.

    A day with no calls is `None`, not 1.0: a flat line of ones would claim the
    model was healthy on a day nobody tried it.
    """
    buckets: dict[str, list[list[int]]] = {}
    start = now - timedelta(days=days)
    for event in events:
        if event.at < start:
            continue
        day = min(days - 1, max(0, (event.at - start).days))
        row = buckets.setdefault(event.model, [[0, 0, 0] for _ in range(days)])
        row[day][0] += 1
        if _is_rate_limited(event):
            row[day][2] += 1
        elif not event.ok:
            row[day][1] += 1

    out: dict[str, list[float | None]] = {}
    for model, row in buckets.items():
        series: list[float | None] = []
        for total, errors, limited in row:
            if total == 0:
                series.append(None)
                continue
            value = 1.0 - (errors / total) - RATE_LIMIT_WEIGHT * (limited / total)
            series.append(max(0.0, min(1.0, value)))
        out[model] = series
    return out
