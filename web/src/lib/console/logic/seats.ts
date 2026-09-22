/**
 * The seats list: how its rows are grouped, what its badges are allowed to say
 * (CONSOLE.md sections 5.2 and 6.2).
 *
 * Two rules, both about not overstating what the list knows.
 *
 * **A badge is a difference, and an unknown difference is no badge.** A seat
 * whose `changes` is `null` has not been compared (`in_step` is null for the
 * same reason), and drawing a "3" for it would invent the number. So the count
 * badge appears only for a count the server actually sent, and a seat in step
 * carries nothing at all: "no badge" is the list's way of saying "nothing to
 * do here", and spending it on seats that are merely unknown would make it
 * meaningless.
 *
 * **A group exists because it has a row.** Grouping by modality returns the
 * modalities present, in the catalogue's own order, each with its rows in the
 * order the server sent them (the seats call already sorts by a purpose the
 * list cannot see). An empty modality is left out rather than drawn as an empty
 * heading.
 */
import { MODALITY_OPTIONS, type SeatRow } from '$lib/api/client';
import type { Modality } from '$lib/types';

/** What a modality is called in front of a person. */
export const MODALITY_LABEL: Record<Modality, string> = {
  llm: 'Text',
  'text-to-image': 'Image',
  'image-editing': 'Image editing',
  'text-to-video': 'Video',
  'image-to-video': 'Image to video',
  'video-editing': 'Video editing',
  'text-to-speech': 'Speech',
  'speech-to-text': 'Transcript',
  'speech-to-speech': 'Voice',
  music: 'Music'
};

/** A modality and the seats it has, in that order. */
export interface SeatGroup {
  modality: Modality;
  label: string;
  rows: SeatRow[];
}

/** What a row's badge says, and which of the two weights it is drawn in. */
export interface Badge {
  text: string;
  /** the change count: a claim about the seat, worth the accent colour */
  tone: 'accent' | 'muted';
}

/** Only rows for modalities the catalogue still lists, in its order. */
export function groupByModality(rows: readonly SeatRow[]): SeatGroup[] {
  const byModality = new Map<Modality, SeatRow[]>();
  for (const row of rows) {
    const mine = byModality.get(row.modality);
    if (mine) mine.push(row);
    else byModality.set(row.modality, [row]);
  }
  const listed = MODALITY_OPTIONS.filter((modality) => byModality.has(modality));
  // a modality this build does not know still gets a heading of its own, so a
  // row the server sent is never silently dropped
  const unlisted = [...byModality.keys()].filter((modality) => !listed.includes(modality));
  return [...listed, ...unlisted].map((modality) => ({
    modality,
    label: MODALITY_LABEL[modality],
    rows: byModality.get(modality) ?? []
  }));
}

/**
 * The seat `/seats` opens: the one you last worked in, else the first with
 * changes waiting, else the first seat -- "first" in the pane's order, so the
 * seat that opens is the one at the top of the list beside it, not whichever
 * the server happened to sort first (a video seat, alphabetically).
 */
export function landingSeat(
  rows: readonly SeatRow[],
  remembered: string | null
): SeatRow | undefined {
  const ordered = groupByModality(rows).flatMap((group) => group.rows);
  return (
    ordered.find((row) => row.name === remembered) ??
    ordered.find((row) => (row.changes ?? 0) > 0) ??
    ordered[0]
  );
}

/**
 * The badge beside a seat's name.
 *
 * The count is the difference between what ships and what the connector holds,
 * so it is drawn only when the server compared the two; the "hand" badge is the
 * seat's own mode, which is always known. A seat with neither gets nothing: a
 * row of zeroes and dashes would bury the two seats that do want attention.
 */
export function badge(row: SeatRow): Badge | null {
  if (typeof row.changes === 'number' && row.changes > 0) {
    return { text: `${row.changes}`, tone: 'accent' };
  }
  if (row.mode === 'manual') return { text: 'hand', tone: 'muted' };
  return null;
}
