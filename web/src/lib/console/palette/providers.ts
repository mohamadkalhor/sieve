/**
 * The palette's four providers (CONSOLE.md section 5.2).
 *
 * A provider is a function of the context, not a registry object: it reads the
 * stores it was handed and answers with the commands that make sense right now,
 * which is why the same call with a seat open offers a different list than the
 * same call without one. No provider fetches, and no provider decides anything
 * the logic packages do not already decide.
 */
import type { PaletteContext, CommandLike, SeatSessionLike } from '../contracts';
import type { Provider } from '../logic/commands';
import { pin, remove, setMode } from '../logic/settings';
import { MODALITY_LABEL } from '../logic/seats';

/**
 * The eight sections, in the order the top bar shows them.
 *
 * `NavLinks.svelte` keeps its own copy for the bar itself (its package owns that
 * file); these two lists are the same eight pairs and want to become one when
 * the palette's package is allowed to edit the shell's.
 */
export const SECTIONS: readonly { href: string; label: string }[] = [
  { href: '/seats', label: 'Seats' },
  { href: '/field', label: 'Field' },
  { href: '/sources', label: 'Sources' },
  { href: '/unscored', label: 'Unscored' },
  { href: '/connectors', label: 'Connectors' },
  { href: '/runs', label: 'Runs' },
  { href: '/axes', label: 'Axes' },
  { href: '/guide', label: 'Guide' }
];

/** One seat per row, by name: opening it is the whole command. */
export function seatsProvider(ctx: PaletteContext): CommandLike[] {
  return (ctx.seats.rows ?? []).map((row) => ({
    id: `seat:${row.name}`,
    group: 'Seats' as const,
    title: `Open ${row.name}`,
    hint: MODALITY_LABEL[row.modality],
    keywords: `${row.name} ${row.purpose}`,
    run: () => void ctx.goto(`/seats/${encodeURIComponent(row.name)}`)
  }));
}

/**
 * The open seat's pool: pick one in the inspector, pin it, or keep it out.
 *
 * Only what the seat has actually listed -- the pool is the full modality
 * catalogue the server sent, and a model nothing is known about is still a
 * model somebody may want to look at, so it is offered and the inspector says
 * what is unknown.
 */
export function modelsProvider(ctx: PaletteContext): CommandLike[] {
  const session = ctx.session();
  const pool = session?.preview?.pool;
  const modality = session?.profile?.modality;
  if (!session || !pool?.length || !modality) return [];
  return pool.flatMap((row) => [
    {
      id: `model:${row.id}`,
      group: 'Models' as const,
      title: row.name,
      hint: row.id,
      keywords: row.local_ids.join(' '),
      run: () => ctx.inspect(row.id, modality)
    },
    ...pinnedCommand(session, row.id, row.name, row.pinned === true),
    ...removedCommand(session, row.id, row.name)
  ]);
}

function pinnedCommand(
  session: SeatSessionLike,
  id: string,
  name: string,
  already: boolean
): CommandLike[] {
  const settings = session.settings;
  if (already || !settings) return [];
  return [
    {
      id: `pin:${id}`,
      group: 'Models' as const,
      title: `Pin ${name}`,
      hint: 'keep it in the lineup',
      run: () => session.edit(pin(settings, id))
    }
  ];
}

function removedCommand(session: SeatSessionLike, id: string, name: string): CommandLike[] {
  const settings = session.settings;
  if (!settings) return [];
  return [
    {
      id: `never:${id}`,
      group: 'Models' as const,
      title: `Never ship ${name}`,
      hint: 'keep it out of the lineup',
      run: () => session.edit(remove(settings, id))
    }
  ];
}

/** The nav, as commands. */
export function sectionsProvider(ctx: PaletteContext): CommandLike[] {
  return SECTIONS.map((section) => ({
    id: `go:${section.href}`,
    group: 'Go to' as const,
    title: `Go to ${section.label}`,
    run: () => void ctx.goto(section.href)
  }));
}

/**
 * The moves that are not a place: make a seat, ship the open one, take it off
 * auto, run the loop.
 *
 * Each is offered only when it would do something -- a "Ship" that is disabled
 * is not a command, and the palette's job is to leave it out rather than to
 * offer a press that does nothing.
 */
export function actionsProvider(ctx: PaletteContext): CommandLike[] {
  const commands: CommandLike[] = [
    {
      id: 'new-seat',
      group: 'Actions',
      title: 'New seat',
      hint: 'a profile and a purpose',
      run: () => void ctx.goto('/seats?new=1')
    },
    {
      id: 'run-now',
      group: 'Actions',
      title: 'Run now',
      hint: 'harvest, pull, ship',
      run: () => void ctx.goto('/runs')
    }
  ];

  const session = ctx.session();
  if (session) {
    const settings = session.settings;
    if (!session.button.disabled) {
      commands.push({
        id: `ship:${session.name}`,
        group: 'Actions',
        title: `Ship ${session.name}`,
        hint: session.button.label,
        run: () => void session.ship()
      });
    }
    if (settings) {
      const next = settings.mode === 'auto' ? 'manual' : 'auto';
      commands.push({
        id: `mode:${session.name}`,
        group: 'Actions',
        title: next === 'manual' ? `Switch ${session.name} to manual` : `Switch ${session.name} to auto`,
        hint: 'who chooses the lineup',
        run: () =>
          session.edit(setMode(settings, next, (session.preview?.models ?? []).map((row) => row.id)))
      });
    }
  }

  return commands;
}

/** What the palette is built over, in the order `match` sorts groups. */
export const PROVIDERS: readonly Provider[] = [
  seatsProvider,
  modelsProvider,
  sectionsProvider,
  actionsProvider
];
