/**
 * The status bar's decisions (CONSOLE.md sections 4.4 and 6.1).
 *
 * The rule under test is omission: a server that never sent `reachable`, `runs`
 * or `schedules` must not see them invented here. "0 reachable" and "no
 * schedule" are both claims, and neither may be made about a field that never
 * arrived.
 */
import { describe, expect, it } from 'vitest';
import type { RunRow, ScheduleRow, StatusRow } from '../../src/lib/api/client';
import { bar, count, soonest, until } from '../../src/lib/console/shell/statusbar';

const NOW = new Date('2026-09-19T04:20:00Z');

function run(over: Partial<RunRow> = {}): RunRow {
  return {
    id: 'run-1',
    step: 'full',
    requested_by: 'scheduler',
    started: '2026-09-19T04:00:00Z',
    finished: '2026-09-19T04:06:00Z',
    running: false,
    ok: true,
    summary: 'shipped 12 seats',
    error: null,
    seconds: 60,
    has_log: true,
    ...over
  };
}

interface StatusParts {
  pulled_at?: string | null;
  ran_at?: string | null;
  schedule?: string;
  sources_enabled?: number;
  telemetry_calls?: number;
  telemetry_at?: string | null;
  reachable?: number;
  unscored?: number;
  runs?: StatusRow['runs'];
  schedules?: ScheduleRow[];
}

/** The required fields are filled in; everything optional is only there when
 *  a test puts it there, which is the whole point of this file. */
function status(parts: StatusParts = {}): StatusRow {
  return {
    pulled_at: '2026-09-19T04:00:00Z',
    ran_at: '2026-09-19T04:06:00Z',
    schedule: 'hourly',
    sources_enabled: 3,
    telemetry_calls: 4015,
    telemetry_at: '2026-09-19T04:06:00Z',
    ...parts
  };
}

function schedule(over: Partial<ScheduleRow> = {}): ScheduleRow {
  return {
    step: 'full',
    mode: 'hourly',
    at_minute: 0,
    at_time: '04:00',
    timezone: 'UTC',
    last_fired: '2026-09-19T04:00:00Z',
    next_fire: '2026-09-19T05:00:00Z',
    ...over
  };
}

describe('bar', () => {
  it('reads a finished run, its summary, and the two numbers', () => {
    const drawn = bar(
      status({
        reachable: 1234,
        unscored: 12,
        runs: { running: null, last: run(), last_by_step: {} },
        schedules: [schedule()]
      }),
      NOW
    );

    expect(drawn.items.map((one) => one.key)).toEqual(['run', 'summary', 'reachable', 'telemetry']);
    expect(drawn.items[0]).toMatchObject({ text: 'run ok 14 min ago', tone: 'ok' });
    expect(drawn.items[1].text).toBe('shipped 12 seats');
    expect(drawn.items[2].text).toBe('1,234 reachable');
    expect(drawn.items[3].text).toBe('telemetry 4,015 calls');
    expect(drawn.unscored).toBe(12);
    expect(drawn.next).toBe('next run in 40m');
  });

  it('omits reachable, runs and the next run when the server did not send them', () => {
    const drawn = bar(status(), NOW);

    expect(drawn.items.map((one) => one.key)).toEqual(['telemetry']);
    expect(drawn.unscored).toBeNull();
    expect(drawn.next).toBeNull();
  });

  it('says a zero the server sent, and only a zero the server sent', () => {
    const drawn = bar(status({ reachable: 0, unscored: 0 }), NOW);

    expect(drawn.items.map((one) => one.text)).toContain('0 reachable');
    expect(drawn.unscored).toBe(0);
  });

  it('puts a run that is going now in front of the last one', () => {
    const drawn = bar(
      status({
        runs: {
          running: run({ running: true, finished: null, step: 'harvest_connectors' }),
          last: run(),
          last_by_step: {}
        }
      }),
      NOW
    );

    expect(drawn.items[0]).toMatchObject({
      key: 'run',
      text: 'running: Harvest connectors',
      tone: 'warn'
    });
  });

  it('tells a failed run from one that finished and said nothing', () => {
    const failed = bar(
      status({ runs: { running: null, last: run({ ok: false }), last_by_step: {} } }),
      NOW
    );
    expect(failed.items[0]).toMatchObject({ text: 'run failed 14 min ago', tone: 'bad' });

    const quiet = bar(
      status({ runs: { running: null, last: run({ ok: null, summary: null }), last_by_step: {} } }),
      NOW
    );
    expect(quiet.items.map((one) => one.key)).toEqual(['run', 'telemetry']);
    expect(quiet.items[0]).toMatchObject({ text: 'run finished 14 min ago', tone: 'muted' });
  });

  it('times the run from when it finished, or from when it started', () => {
    const drawn = bar(
      status({
        runs: { running: null, last: run({ finished: null, started: '2026-09-19T04:10:00Z' }), last_by_step: {} }
      }),
      NOW
    );
    expect(drawn.items[0].text).toBe('run ok 10 min ago');
  });

  it('says "no schedule" only when the server sent schedules and none is next', () => {
    const empty = bar(status({ schedules: [] }), NOW);
    expect(empty.next).toBe('no schedule');

    const stale = bar(status({ schedules: [schedule({ next_fire: null })] }), NOW);
    expect(stale.next).toBe('no schedule');

    // absent is not the same news, and the bar says nothing rather than guessing
    expect(bar(status(), NOW).next).toBeNull();
  });
});

describe('soonest', () => {
  it('picks the earliest moment and ignores the ones that are missing or unreadable', () => {
    const at = soonest([
      schedule({ next_fire: null }),
      schedule({ next_fire: 'later that day' }),
      schedule({ next_fire: '2026-09-19T09:00:00Z' }),
      schedule({ next_fire: '2026-09-19T05:30:00Z' })
    ]);

    expect(at?.toISOString()).toBe('2026-09-19T05:30:00.000Z');
  });

  it('has nothing to pick from when the server did not send schedules', () => {
    expect(soonest(undefined)).toBeNull();
    expect(soonest([])).toBeNull();
  });
});

describe('until', () => {
  it('counts the way a person reads a clock', () => {
    expect(until(new Date('2026-09-19T04:19:30Z'), NOW)).toBe('due now');
    expect(until(new Date('2026-09-19T04:20:00Z'), NOW)).toBe('due now');
    expect(until(new Date('2026-09-19T04:46:00Z'), NOW)).toBe('in 26m');
    expect(until(new Date('2026-09-19T06:00:00Z'), NOW)).toBe('in 1h 40m');
    expect(until(new Date('2026-09-19T06:20:00Z'), NOW)).toBe('in 2h');
  });
});

describe('count', () => {
  it('groups the thousands nobody reads', () => {
    expect(count(4015)).toBe('4,015');
    expect(count(0)).toBe('0');
  });
});
