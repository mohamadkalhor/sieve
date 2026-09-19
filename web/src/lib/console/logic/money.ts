/**
 * Prices and what one task costs, in the two shapes the inspector draws
 * (CONSOLE.md section 5.2, REVIEW.md finding 9).
 *
 * Three different absences, three different answers:
 *
 * - a number is a number, and money never prints a dash for one;
 * - `null` is the server saying there is no price for this model ("no price,
 *   so no cost"), which may only be shown after a lookup actually said so;
 * - `undefined` is a field the server does not have at all -- an old deployment
 *   with no `cost_per_task` -- and saying "no price" there would turn a missing
 *   field into a fact about the model.
 *
 * That is why both functions take `number | null | undefined` and why they
 * return a `title` with the text: the dash is the same character in both
 * absences and only the hover explains which one it is.
 *
 * `cost_per_task` is money, not a score, so it is rounded to the cent; a
 * fraction of a cent is shown as `<$0.01` rather than as `$0.00`, which would
 * read as free. Posted per-million prices are not money spent, so a cheap model
 * keeps its third decimal ($0.075) instead of being rounded to a price nobody
 * charges.
 */
export interface Money {
  /** what the pane draws: `$1.25`, `<$0.01`, or `—` when there is no number */
  text: string;
  /** why, for the dash's `title`; empty when there is a number */
  title: string;
}

/** The server does not report this field at all: not an answer about price. */
const ABSENT_COST = 'server does not report task cost';
const ABSENT_PRICE = 'no posted price field from this server';
const NO_COST = 'no price, so no cost';
const NO_PRICE = 'no posted price';

/** One number, with the decimals it needs to stay honest. `n` carries a unit. */
function decimalsFor(n: number): number {
  if (n === 0 || n >= 0.1) return 2;
  if (n >= 0.01) return 3;
  return 4;
}

/**
 * The dash, and which absence it stands for.
 *
 * `null` is the server answering "none"; `undefined` is a server that does not
 * have the field; anything else that is not a finite number is a figure that
 * could not be read, which is also not a price.
 */
function absent(n: number | null | undefined, noPrice: string, noField: string): Money {
  if (n === null) return { text: '—', title: noPrice };
  if (n === undefined) return { text: '—', title: noField };
  return { text: '—', title: 'unreadable figure' };
}

/**
 * What one task on this model costs, as the seat's own traffic measured it.
 *
 * Rounded to the cent: a figure that claims to know a third decimal about a
 * cost estimate is claiming more than the estimate knows. Anything that rounds
 * away to nothing is shown as `< $0.01` so that it does not read as free.
 */
export function perTask(n: number | null | undefined): Money {
  if (typeof n !== 'number' || !Number.isFinite(n)) return absent(n, NO_COST, ABSENT_COST);
  if (n === 0) return { text: '$0.00', title: '' };
  if (n > 0 && n < 0.01) return { text: '<$0.01', title: '' };
  return { text: `$${n.toFixed(2)}`, title: '' };
}

/**
 * A posted price per million tokens.
 *
 * Real posts come in thirds of a cent ($0.075), so below a dollar the figure
 * keeps enough decimals to be the posted number. Free is "$0.00", which is a
 * price, not a missing one.
 */
export function perMillion(n: number | null | undefined): Money {
  if (typeof n !== 'number' || !Number.isFinite(n)) return absent(n, NO_PRICE, ABSENT_PRICE);
  return { text: `$${n.toFixed(decimalsFor(Math.abs(n)))}`, title: '' };
}
