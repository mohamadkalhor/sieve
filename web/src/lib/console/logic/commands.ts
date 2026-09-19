/**
 * The command palette's search and its providers (CONSOLE.md sections 5.2 and
 * 6.1).
 *
 * A provider turns the context into commands; it never fetches anything. The
 * context hands it the stores it may read and the two moves it may make
 * (`goto`, `inspect`), which is what keeps the palette from becoming a second
 * place where the app talks to the server.
 *
 * `match` ranks the way a person expects a jump-list to: what starts with what
 * they typed, then what starts a word with it, then what merely contains the
 * letters in order (`gc` finds "Go to coder"), and never anything else. Ties go
 * to the title, not to the order the providers happened to be registered in, so
 * the same keypress gives the same first row.
 */
import type { CommandLike, PaletteContext } from '../contracts';

/** One thing the palette can run. The palette's own seam type (see contracts). */
export type Command = CommandLike;

/** A source of commands: given the context, the commands it knows about. */
export type Provider = (ctx: PaletteContext) => Command[];

/** How many results a query may return before it stops being a list. */
export const MATCH_LIMIT = 12;

/** Rank 0: the whole query is a prefix. Rank 1: it starts a word. Rank 2: its
 *  letters appear in order somewhere. */
function rankOf(command: Command, needle: string): number | null {
  const haystack = `${command.title} ${command.hint ?? ''} ${command.keywords ?? ''}`.toLowerCase();
  if (haystack.startsWith(needle)) return 0;
  for (let at = haystack.indexOf(needle); at !== -1; at = haystack.indexOf(needle, at + 1)) {
    if (!/[a-z0-9]/.test(haystack[at - 1] ?? '')) return 1;
  }
  let cursor = 0;
  for (const character of needle) {
    cursor = haystack.indexOf(character, cursor);
    if (cursor === -1) return null;
    cursor += 1;
  }
  return 2;
}

/** With nothing typed: the list's own order, in the palette's group order. */
const GROUP_ORDER: Command['group'][] = ['Seats', 'Models', 'Go to', 'Actions'];

function starter(commands: readonly Command[], limit: number): Command[] {
  return [...commands]
    .sort(
      (a, b) =>
        GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group) ||
        a.title.localeCompare(b.title)
    )
    .slice(0, Math.max(0, limit));
}

/**
 * The commands a query matches, best first.
 *
 * An empty query is not a search: it is the list a person sees before they
 * type, so it comes back in a fixed order rather than in an arbitrary one.
 */
export function match(
  query: string,
  commands: readonly Command[],
  limit: number = MATCH_LIMIT
): Command[] {
  const needle = query.trim().toLowerCase();
  const capped = Math.max(0, limit);
  if (!needle) return starter(commands, capped);

  const hits: { command: Command; rank: number; at: number }[] = [];
  commands.forEach((command, at) => {
    const rank = rankOf(command, needle);
    if (rank !== null) hits.push({ command, rank, at });
  });
  hits.sort(
    (a, b) =>
      a.rank - b.rank || a.command.title.localeCompare(b.command.title) || a.at - b.at
  );
  return hits.slice(0, capped).map((hit) => hit.command);
}
