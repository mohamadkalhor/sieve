/**
 * The status bar's decisions, as data (CONSOLE.md sections 4.4, 6.1).
 *
 * The rule this file exists for: **a field the server did not send is omitted,
 * not zeroed.** An older server sends no `reachable`, no `runs` and no
 * `schedules`, and a bar that printed "0 reachable" would be reporting a dead
 * gateway that nobody measured. Zero is a reading; absent is not a reading.
 *
 * The second rule is finding 15: `schedules` absent means "this server does not
 * say", while `schedules` present with no `next_fire` means "nothing is
 * scheduled" -- and only the second one may say "no schedule".
 */
import { STEP_LABEL, type ScheduleRow, type StatusRow } from '$lib/api/client';
import { ago } from '$lib/freshness';

export type Tone = 'ok' | 'bad' | 'warn' | 'muted';

export interface BarItem {
  key: string;
  text: string;
  tone: Tone;
}

export interface Bar {
  /** the left-hand items, in the order they are read */
  items: BarItem[];
  /** drawn as the "N unscored" link, or null when the server did not say */
  unscored: number | null;
  /** the right-hand item, or null when this server does not schedule anything */
  next: string | null;
}

/** Thousands, because 4015 calls is a number nobody reads. */
export function count(n: number): string {
  return n.toLocaleString('en-US');
}

/** The moment the soonest schedule is due, or null when none is. */
export function soonest(schedules: readonly ScheduleRow[] | undefined): Date | null {
  if (!schedules) return null;
  let best: Date | null = null;
  for (const row of schedules) {
    if (!row.next_fire) continue;
    const at = new Date(row.next_fire);
    if (Number.isNaN(at.getTime())) continue;
    if (!best || at < best) best = at;
  }
  return best;
}

/** "in 46m", or "due now" once the moment has passed. */
export function until(at: Date, now: Date): string {
  const minutes = Math.round((at.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0) return 'due now';
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `in ${hours}h ${rest}m` : `in ${hours}h`;
}

/** The run item: what is going now, else what finished last. */
function runItem(status: StatusRow, now: Date): BarItem | null {
  const runs = status.runs;
  if (!runs) return null;
  if (runs.running) {
    return {
      key: 'run',
      text: `running: ${STEP_LABEL[runs.running.step]}`,
      tone: 'warn'
    };
  }
  const last = runs.last;
  if (!last) return null;
  const at = new Date(last.finished ?? last.started);
  const when = Number.isNaN(at.getTime()) ? '' : ` ${ago(at, now)}`;
  if (last.ok === true) return { key: 'run', text: `run ok${when}`, tone: 'ok' };
  if (last.ok === false) return { key: 'run', text: `run failed${when}`, tone: 'bad' };
  return { key: 'run', text: `run finished${when}`, tone: 'muted' };
}

export function bar(status: StatusRow, now: Date = new Date()): Bar {
  const items: BarItem[] = [];
  const run = runItem(status, now);
  if (run) items.push(run);
  if (status.runs?.last?.summary) {
    items.push({ key: 'summary', text: status.runs.last.summary, tone: 'muted' });
  }
  if (typeof status.reachable === 'number') {
    items.push({ key: 'reachable', text: `${count(status.reachable)} reachable`, tone: 'muted' });
  }
  if (typeof status.telemetry_calls === 'number') {
    items.push({
      key: 'telemetry',
      text: `telemetry ${count(status.telemetry_calls)} calls`,
      tone: 'muted'
    });
  }

  let next: string | null = null;
  if (status.schedules) {
    const at = soonest(status.schedules);
    next = at ? `next run ${until(at, now)}` : 'no schedule';
  }

  return {
    items,
    unscored: typeof status.unscored === 'number' ? status.unscored : null,
    next
  };
}
