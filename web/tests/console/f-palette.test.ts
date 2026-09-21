/**
 * The command palette and its four providers (CONSOLE.md sections 5.2 and 6.1).
 *
 * Two things are checked here and nowhere else: that the list a person sees is
 * a function of the stores rather than of the last thing the palette was told
 * -- the open seat is *attached*, so its models have to appear in the list the
 * moment it is -- and that a command is offered only when pressing it would do
 * something.
 */
import { describe, expect, it, vi } from 'vitest';
import type { Listed } from '../../src/lib/api/client';
import type {
  CommandLike,
  PaletteContext,
  SeatSessionLike,
  SeatsStoreLike
} from '../../src/lib/console/contracts';
import type { Settings } from '../../src/lib/console/logic/settings';
import {
  PROVIDERS,
  actionsProvider,
  modelsProvider,
  seatsProvider,
  sectionsProvider
} from '../../src/lib/console/palette/providers';
import { attachSeat, Palette } from '../../src/lib/console/state/palette.svelte';
import { SeatsStore } from '../../src/lib/console/state/seats.svelte';

/* -------------------------------------------------------------------------- */
/* fixtures                                                                    */
/* -------------------------------------------------------------------------- */

function listed(id: string, over: Partial<Listed> = {}): Listed {
  return { id, name: id, local_ids: [], score: 1, ...over };
}

function settings(over: Partial<Settings> = {}): Settings {
  return {
    weights: {},
    order: [],
    locked: [],
    ship: 5,
    mode: 'auto',
    manual: [],
    pinned: [],
    removed: [],
    needs: [],
    prefixWeights: {},
    ...over
  };
}

function session(over: Partial<Record<string, unknown>> = {}): SeatSessionLike {
  return {
    name: 'coder',
    profile: { modality: 'llm' },
    preview: { models: [], pool: [] },
    settings: settings(),
    button: { label: 'Ship 5', disabled: false },
    ship: vi.fn(),
    edit: vi.fn(),
    ...over
  } as unknown as SeatSessionLike;
}

/** The smallest thing the palette's context reads of the seats store. */
function seatsStore(rows: SeatsStoreLike['rows'] = null): SeatsStoreLike {
  return {
    rows,
    error: null,
    loading: false,
    degraded: false,
    load: async () => {},
    patch: () => {}
  };
}

function context(over: Partial<PaletteContext> = {}): PaletteContext {
  return {
    goto: async () => {},
    seats: seatsStore(),
    status: { row: null, unreachable: false, failed: null, start: () => () => {} },
    selection: { id: null, select: () => {}, adopt: () => {} },
    session: () => null,
    inspect: () => {},
    ...over
  };
}

function seatRow(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    modality: 'llm',
    purpose: 'code',
    mode: 'auto',
    ship: 5,
    live: [],
    lineup: [],
    in_step: true,
    changes: 0,
    shipped_at: null,
    ...over
  } as never;
}

/* -------------------------------------------------------------------------- */
/* the palette                                                                 */
/* -------------------------------------------------------------------------- */

describe('the palette', () => {
  it('shows the providers\u2019 commands in the group order, before anything is typed', () => {
    const palette = new Palette({
      providers: [
        () => [
          { id: 'b', group: 'Actions', title: 'Zeta', run: () => {} },
          { id: 'a', group: 'Seats', title: 'Open coder', run: () => {} }
        ]
      ],
      ctx: () => context()
    });

    expect(palette.query).toBe('');
    expect(palette.results.map((one) => one.id)).toEqual(['a', 'b']);
    expect(palette.active).toBe(0);
  });

  it('ranks what was typed, and starts again at the first row for each new query', () => {
    const palette = new Palette({
      providers: [
        () => [
          { id: 'go', group: 'Go to', title: 'Go to seats', run: () => {} },
          { id: 'open', group: 'Seats', title: 'Open coder', run: () => {} }
        ]
      ],
      ctx: () => context()
    });

    palette.move(1);
    expect(palette.active).toBe(1);

    palette.query = 'cod';

    expect(palette.results.map((one) => one.id)).toEqual(['open']);
    expect(palette.active).toBe(0);
  });

  it('wraps in both directions, and walks over nothing without a list', () => {
    const palette = new Palette({
      providers: [
        () => [
          { id: 'a', group: 'Go to', title: 'A', run: () => {} },
          { id: 'b', group: 'Go to', title: 'B', run: () => {} }
        ]
      ],
      ctx: () => context()
    });

    palette.move(-1);
    expect(palette.active).toBe(1);
    palette.move(1);
    expect(palette.active).toBe(0);

    const bare = new Palette({ providers: [() => []], ctx: () => context() });
    expect(bare.active).toBe(-1);
    bare.move(1);
    expect(bare.active).toBe(-1);
    bare.run();
    expect(bare.open).toBe(false);
  });

  it('closes and clears when it is opened again, rather than remembering', () => {
    const palette = new Palette({
      providers: [() => [{ id: 'a', group: 'Go to', title: 'A', run: () => {} }]],
      ctx: () => context()
    });

    palette.toggle();
    expect(palette.open).toBe(true);
    palette.query = 'a';
    palette.open = false;

    palette.toggle();

    expect(palette.open).toBe(true);
    expect(palette.query).toBe('');
    expect(palette.active).toBe(0);
  });

  it('runs what is under the cursor, and closes first', () => {
    const ran = vi.fn();
    const palette = new Palette({
      providers: [() => [{ id: 'a', group: 'Actions', title: 'Run now', run: ran }]],
      ctx: () => context()
    });

    palette.open = true;
    palette.run();

    expect(ran).toHaveBeenCalledTimes(1);
    expect(palette.open).toBe(false);
  });

  it('refuses a command that says it is disabled', () => {
    const ran = vi.fn();
    const command: CommandLike = {
      id: 'ship',
      group: 'Actions',
      title: 'Ship coder',
      enabled: false,
      run: ran
    };
    const palette = new Palette({ providers: [() => [command]], ctx: () => context() });

    palette.open = true;
    palette.run();

    expect(ran).not.toHaveBeenCalled();
    expect(palette.open).toBe(true);
  });

  it('reads the stores again: a seat that appears is in the list', async () => {
    let rows: never[] = [];
    const store = new SeatsStore({
      api: {
        seats: async () => ({ ok: true, value: { rows, degraded: false } })
      }
    });
    const palette = new Palette({ providers: PROVIDERS, ctx: () => context({ seats: store }) });

    // nothing but the nav and the two moves that need no seat
    expect(palette.results.map((one) => one.title)).not.toContain('Open coder');

    rows = [seatRow('coder')];
    await store.load();

    expect(palette.results.map((one) => one.title)).toContain('Open coder');
  });

  it('takes the seat the route hands it, and gives it back when the route leaves', () => {
    // the app's own context reads the attached seat, which is the whole point
    // of attaching it rather than passing it in once
    const self: { palette: Palette | null } = { palette: null };
    const palette = new Palette({
      providers: PROVIDERS,
      ctx: () => context({ session: () => self.palette?.here?.session() ?? null })
    });
    self.palette = palette;
    const here = {
      selection: { id: null, select: () => {}, adopt: () => {} },
      session: () => session({ preview: { pool: [listed('m1')], models: [] } }),
      inspect: () => {}
    };

    expect(palette.results.map((one) => one.id)).not.toContain('model:m1');

    attachSeat(palette, here);
    expect(palette.results.map((one) => one.id)).toContain('model:m1');

    attachSeat(palette, null);
    expect(palette.results.map((one) => one.id)).not.toContain('model:m1');
  });
});

/* -------------------------------------------------------------------------- */
/* the providers                                                               */
/* -------------------------------------------------------------------------- */

describe('seatsProvider', () => {
  it('offers one seat per row, and opens it where it lives', async () => {
    const goto = vi.fn(async () => {});
    const commands = seatsProvider(
      context({
        goto,
        seats: seatsStore([seatRow('coder'), seatRow('caf\u00e9 noir', { modality: 'music' })])
      })
    );

    expect(commands.map((one) => one.title)).toEqual(['Open coder', 'Open caf\u00e9 noir']);
    expect(commands[0].hint).toBe('Text');
    expect(commands[1].hint).toBe('Music');

    commands[1].run();
    await Promise.resolve();
    expect(goto).toHaveBeenCalledWith('/seats/caf%C3%A9%20noir');
  });

  it('has nothing to offer before the list arrives', () => {
    expect(seatsProvider(context({ seats: seatsStore() }))).toEqual([]);
    expect(seatsProvider(context({ seats: seatsStore([]) }))).toEqual([]);
  });
});

describe('modelsProvider', () => {
  const pool = [listed('m1', { name: 'Coder One', local_ids: ['openai/gpt'] }), listed('m2')];

  it('says nothing without an open seat, or without a pool', () => {
    expect(modelsProvider(context())).toEqual([]);
    expect(
      modelsProvider(context({ session: () => session({ preview: { pool: [], models: [] } }) }))
    ).toEqual([]);
  });

  it('offers each model: look at it, pin it, keep it out', () => {
    const commands = modelsProvider(
      context({ session: () => session({ preview: { pool, models: [] } }) })
    );

    expect(commands.map((one) => one.id)).toEqual([
      'model:m1',
      'pin:m1',
      'never:m1',
      'model:m2',
      'pin:m2',
      'never:m2'
    ]);
    expect(commands[0]).toMatchObject({ title: 'Coder One', hint: 'm1', group: 'Models' });
  });

  it('does not offer to pin what the server says is already pinned', () => {
    const commands = modelsProvider(
      context({
        session: () =>
          session({ preview: { pool: [listed('m1', { pinned: true })], models: [] } })
      })
    );

    expect(commands.map((one) => one.id)).toEqual(['model:m1', 'never:m1']);
  });

  it('offers nothing that would edit settings it does not have', () => {
    const commands = modelsProvider(
      context({ session: () => session({ preview: { pool, models: [] }, settings: null }) })
    );

    expect(commands.map((one) => one.id)).toEqual(['model:m1', 'model:m2']);
  });

  it('looks at a model through the context, since only the route can select', () => {
    const inspect = vi.fn();
    const commands = modelsProvider(
      context({ inspect, session: () => session({ preview: { pool, models: [] } }) })
    );

    commands[0].run();

    expect(inspect).toHaveBeenCalledWith('m1', 'llm');
  });

  it('pins and removes through the seat\u2019s own edit, not by writing itself', () => {
    const edit = vi.fn();
    const current = settings({ pinned: [] });
    const commands = modelsProvider(
      context({
        session: () =>
          session({ preview: { pool: [listed('m1')], models: [] }, settings: current, edit })
      })
    );

    commands[1].run(); // pin
    expect(edit).toHaveBeenCalledWith(expect.objectContaining({ pinned: ['m1'] }));

    commands[2].run(); // never ship
    expect(edit).toHaveBeenLastCalledWith(expect.objectContaining({ removed: ['m1'] }));
  });
});

describe('sectionsProvider', () => {
  it('offers the eight sections and goes to them', async () => {
    const goto = vi.fn(async () => {});
    const commands = sectionsProvider(context({ goto }));

    expect(commands).toHaveLength(8);
    expect(commands.map((one) => one.title)).toContain('Go to Unscored');

    commands[0].run();
    await Promise.resolve();
    expect(goto).toHaveBeenCalledWith('/seats');
  });
});

describe('actionsProvider', () => {
  it('offers the two moves that need no seat', async () => {
    const goto = vi.fn(async () => {});
    const commands = actionsProvider(context({ goto }));

    expect(commands.map((one) => one.id)).toEqual(['new-seat', 'run-now']);

    commands[0].run();
    await Promise.resolve();
    expect(goto).toHaveBeenCalledWith('/seats?new=1');
  });

  it('offers Ship only when the button would do something', () => {
    const ready = actionsProvider(context({ session: () => session() }));
    expect(ready.map((one) => one.id)).toContain('ship:coder');

    const blocked = actionsProvider(
      context({ session: () => session({ button: { label: 'in step', disabled: true } }) })
    );
    expect(blocked.map((one) => one.id)).not.toContain('ship:coder');
  });

  it('ships the open seat', async () => {
    const ship = vi.fn(async () => {});
    const commands = actionsProvider(context({ session: () => session({ ship }) }));

    commands.find((one) => one.id === 'ship:coder')?.run();
    await Promise.resolve();

    expect(ship).toHaveBeenCalledTimes(1);
  });

  it('switches the mode the other way, keeping what is already in the lineup', () => {
    const edit = vi.fn();
    const auto = actionsProvider(
      context({
        session: () =>
          session({
            settings: settings({ mode: 'auto' }),
            preview: { pool: [], models: [listed('m1')] },
            edit
          })
      })
    );

    auto.find((one) => one.id === 'mode:coder')?.run();

    expect(edit).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'manual', manual: ['m1'] })
    );

    const manual = actionsProvider(
      context({ session: () => session({ settings: settings({ mode: 'manual' }), edit }) })
    );
    manual.find((one) => one.id === 'mode:coder')?.run();
    expect(edit).toHaveBeenLastCalledWith(expect.objectContaining({ mode: 'auto' }));
  });
});
