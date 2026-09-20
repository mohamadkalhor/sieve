/**
 * What the inspector draws, decided away from the DOM (CONSOLE.md section 6.7).
 *
 * Every number and every sentence the panel shows is chosen here, so the three
 * rules the panel is held to can be tested without a browser:
 *
 * - an absence is never drawn as a fact: an unmeasured axis says "not measured",
 *   an unanswered need says "unknown" with the sentence that explains it, and a
 *   price that was never posted says "No posted price" once;
 * - the score and the raw score are different numbers, and the caps line says
 *   which one "ships #2" is about;
 * - nothing here divides by a maximum that could be zero, so no panel can
 *   render `NaN`.
 */
import { NEEDS, type Need } from '$lib/api/client';
import type { Listed, ModelCard, PreviewResult } from '$lib/api/client';
import type { Unit } from '$lib/types';
import type { CardState } from '../contracts';
import { NEED_LABEL, NO_SOURCE_SAYS, tone, who, type Tone } from '../logic/abilities';
import type { LineupDiff } from '../logic/diff';
import { multipliers } from '../logic/explain';
import { perMillion, perTask, type Money } from '../logic/money';
import { rowAbilities } from '../state/card.svelte';

/** Section 4.2's sentence, when the server does not say how a score is made up. */
export const NO_AXES = 'This server does not say how a score is made up.';

/** Section 6.7's sentence, under the ability table. */
export const UNKNOWN_COUNTS_AS_NO = 'Unknown counts as no for this seat.';

/** Nothing to draw a price for, once the lookup came back empty-handed. */
export const NO_PRICE = 'No posted price';

/** Nothing selected, and the pane has to ask for one. */
export const NO_SELECTION = 'Select a model to see why it ranks where it does.';

/** Selected, and nothing reachable serves it, so there is no score to explain. */
export const NO_ROW_SERVED = 'Nothing reachable serves it, so it has no score here.';

/** Selected, and the seat has not sent a row for it yet. */
export const NO_ROW_YET = 'The seat has not sent a row for it yet.';

/** The block's heading, for a row the seat has a score for. */
export function whyHeading(score: number): string {
  return `Where ${twoDecimals(score)} comes from`;
}

/**
 * The equation from the bars to the number the table compares (finding 11).
 *
 * The bars are the *raw* score, axis by axis, so the heading over them is the
 * raw score and this line is what turned it into `score`: a row can lead on
 * every axis and still sit lower, and blaming an axis for a gap health or trim
 * made is the mistake the finding names. `null` when the row does not report
 * its raw score -- there is no equation to write, and the fragment "× health
 * × trim" would be arithmetic without a left-hand side.
 */
export function fitLine(row: Listed): string | null {
  const line = multipliers(row);
  if (line === null) return null;
  const raw = row.raw;
  const health = `× health ${twoDecimals(line.health)}`;
  const trim = `× trim ${twoDecimals(line.factor)}`;
  if (raw === undefined) return `${health} ${trim}`;
  return `${twoDecimals(raw)} ${health} ${trim} = ${twoDecimals(row.score)}`;
}

/** A figure we do not have is a dash, never `NaN` or `Infinity` on the page. */
export function oneDecimal(n: number): string {
  return Number.isFinite(n) ? n.toFixed(1) : '—';
}

/** A points figure with the trailing zero dropped: 45, not 45.00000000000001. */
export function points(n: number): string {
  return Number.isFinite(n) ? String(Number(n.toFixed(1))) : '—';
}

export function twoDecimals(n: number): string {
  return Number.isFinite(n) ? n.toFixed(2) : '—';
}

/**
 * A bar's width, as the share of the widest axis.
 *
 * Clamped both ways: `whyRows` already refuses to divide by a maximum of zero,
 * and this is the second lock on the same door, because a `NaN` in a style
 * attribute is a bar of no width that nobody notices.
 */
export function barWidth(share: number): string {
  if (!Number.isFinite(share)) return '0%';
  return `${Math.round(Math.min(1, Math.max(0, share)) * 100)}%`;
}

/**
 * Where the selected model stands in the seat's answer, and the row that says
 * so. The lists are read in the order a person would: the lineup it would ship
 * first, then the ones a need holds out, then the ones you removed, then the
 * ten behind it, then the ids nothing serves, then the rest of the pool.
 */
export type Place = 'lineup' | 'blocked' | 'removed' | 'next' | 'missing' | 'pool' | 'none';

export interface Standing {
  place: Place;
  /** its place in the lineup, 1-based, when it has one */
  rank: number | null;
  /** the row the seat sent; a `missing` id has a name and nothing else */
  row: Listed | null;
  name: string;
}

export function standingOf(id: string, preview: PreviewResult | null): Standing {
  if (!preview) return { place: 'none', rank: null, row: null, name: id };

  const lineup = preview.models ?? [];
  const at = lineup.findIndex((row) => row.id === id);
  if (at >= 0) return { place: 'lineup', rank: at + 1, row: lineup[at], name: lineup[at].name };

  const lists: { place: Place; rows: Listed[] }[] = [
    { place: 'blocked', rows: preview.blocked ?? [] },
    { place: 'removed', rows: preview.removed ?? [] },
    { place: 'next', rows: preview.next ?? [] },
    { place: 'pool', rows: preview.pool ?? [] }
  ];
  for (const list of lists) {
    const found = list.rows.find((row) => row.id === id);
    if (!found) continue;
    // A row of `next` is not shipped by these weights; it is the number it
    // would take if the ten above it went, which is what "next up, #7" means.
    const rank =
      list.place === 'next' ? lineup.length + list.rows.findIndex((row) => row.id === id) + 1 : null;
    return { place: list.place, rank, row: found, name: found.name };
  }

  const missing = (preview.missing ?? []).find((row) => row.id === id);
  if (missing) return { place: 'missing', rank: null, row: null, name: missing.name };

  // Pinned and hand-listed ids that nothing reachable serves are the only way
  // to be selected and named by no list.
  return { place: 'none', rank: null, row: null, name: id };
}

/** Both sides of the comparison were read, and they agree. */
export function inStep(diff: LineupDiff): boolean {
  return diff.known && diff.changes === 0;
}

/**
 * The caps line: where this model stands, in one line.
 *
 * "ships" is only sayable when the comparison was actually made: with no chain
 * on this browser, or with edits in flight, the same lineup is a proposal, and
 * the line says so instead of claiming the gateway already holds it.
 */
export function capsText(standing: Standing, agree: boolean): string {
  switch (standing.place) {
    case 'lineup':
      return `Selected · ${agree ? 'ships' : 'would ship'} #${standing.rank}`;
    case 'next':
      return `Selected · next up, #${standing.rank}`;
    case 'blocked':
      return 'Selected · fails Must support';
    case 'removed':
      return 'Selected · removed by you';
    case 'missing':
      return 'Selected · nothing reachable serves it';
    case 'pool':
      return standing.row?.pinned ? 'Selected · pinned · skipped' : 'Selected · not shipping';
    case 'none':
      return 'Selected';
  }
}

/** The unit of a posted price, in the words the unit uses. */
const UNIT_WORDS: Record<Unit, string> = {
  usd_per_1m_tokens: 'per 1M tokens',
  usd_per_image: 'per image',
  usd_per_second: 'per second',
  usd_per_compute_second: 'per compute second',
  usd_per_1m_chars: 'per 1M characters',
  usd_per_megapixel: 'per megapixel',
  usd_per_request: 'per request',
  usd_per_task: 'per task',
  tokens_per_s: 'tokens per second',
  seconds: 'seconds',
  elo: 'elo',
  index_0_100: 'points',
  fraction: 'share',
  count: 'count'
};

/** An unrecognised unit is printed as the server spelled it, never guessed at. */
export function unitWording(unit: string): string {
  return UNIT_WORDS[unit as Unit] ?? unit;
}

export interface Figure {
  label: string;
  money: Money;
}

/**
 * The price figures, one per number a person can act on.
 *
 * A model priced per million tokens gets three: what goes in, what comes out,
 * and what one task on this seat's own traffic has cost. A model priced by the
 * unit -- an image, a second, a megapixel -- gets one, worded by its own unit:
 * three token columns about a price per image would be three ways of saying
 * nothing. `[]` means there is no price at all, and the block says so once.
 */
export function priceFigures(
  price: ModelCard['price'],
  row: Listed | null,
  seat: string
): Figure[] {
  if (!price) return [];
  if (price.unit === 'usd_per_1m_tokens') {
    return [
      { label: 'In, per 1M', money: perMillion(price.input) },
      { label: 'Out, per 1M', money: perMillion(price.output) },
      // `perMillion` is the posted-price formatter -- the third decimal a post
      // keeps -- and a cost estimate is the same kind of number.
      { label: `One ${seat} task`, money: perTask(row?.cost_per_task) }
    ];
  }
  return [{ label: unitWording(price.unit), money: perMillion(price.per_unit) }];
}

export interface AbilityRow {
  need: Need;
  label: string;
  tone: Tone;
  /** who answered, or which of the two absences this is */
  who: string;
}

/**
 * The four needs, as the card has them -- or, when there is no card, as the
 * preview row has them.
 *
 * A row's own answers carry no source names (the row is a list entry, not the
 * card), so the who column reads "source not reported" for the ones it answered
 * and "no source says" for the ones it did not. That difference is the whole
 * point: something answered the first, nothing answered the second.
 */
export function abilityRows(card: ModelCard | null, row: Listed | null): AbilityRow[] {
  const held = card ? card.abilities : rowAbilities(row);
  return NEEDS.map((need) => {
    const entry = held[need];
    return { need, label: NEED_LABEL[need], tone: tone(entry?.answer), who: who(entry) };
  });
}

export function unknownRequired(rows: AbilityRow[], needs: readonly Need[]): boolean {
  return rows.some((row) => needs.includes(row.need) && row.tone === 'unknown');
}

/** Whether the table can say anything at all about this model. */
export function allUnknown(rows: AbilityRow[]): boolean {
  return rows.every((row) => row.tone === 'unknown' && row.who === NO_SOURCE_SAYS);
}

/** Nothing has answered yet, and there is no card to draw while it does. */
export function waiting(state: CardState, card: ModelCard | null): boolean {
  return state === 'loading' && card === null;
}
