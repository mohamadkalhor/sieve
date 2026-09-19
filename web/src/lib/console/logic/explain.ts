/**
 * Why a model ranks where it does (CONSOLE.md sections 5.2 and 6.7, REVIEW.md
 * finding 11).
 *
 * Two different numbers live in a row and the page must not confuse them.
 *
 * The **raw** score is what the axes gave: `raw = Σ contribution`, each
 * contribution a weight times what the axis measured. The **final** score is
 * what the seat compares, and the server makes it by multiplying the raw score
 * by health and by the trim factor (`sieve/profiles/control.py`). So a row can
 * lead on the axes and still sit lower, and two rows with identical axes can
 * sit in different places because their multipliers differ. The page says which
 * of the two it is talking about, in words, rather than blaming an axis for a
 * gap the multipliers made.
 *
 * And the axis gaps this module reports are *raw* gaps. `carriedBy` names the
 * axis where the leader's raw contributions are furthest ahead, and the amount
 * is that difference -- not points of the final score. Both are stated in
 * points of 100, which is the unit the headings use ("points of 100"), so a
 * sentence that mixes them has to say which is which.
 *
 * Nothing here divides by a maximum that could be zero.
 */
import type { Listed } from '$lib/api/client';
import { carriedBy } from '$lib/rank/weigh';

/** One axis of a row's raw score, as the inspector draws it. */
export interface WhyRow {
  axis: string;
  /** what the axis is called in front of a person */
  label: string;
  /** what this axis gave, in points of 100 */
  got: number;
  /** what a model scoring 100% on this axis would have got, in the same unit */
  of: number;
  /** `got / max(of)` across the rows: the bar's fill, 0 when nothing is named */
  share: number;
  /** false when no source measured this axis for this model */
  measured: boolean;
  /** the CSS colour token for this axis, by its position */
  color: string;
}

function axisColor(index: number): string {
  return `var(--c-axis-${index % 8})`;
}

/**
 * The rows of "Where 0.88 comes from", in the order the proposed weights name
 * them, or `null` when the server does not say how a score is made up.
 *
 * An axis the weights name but the row does not report is drawn, at 0 and
 * unmeasured: leaving it out would make the bars silently disagree with the
 * weights beside them. An axis the row reports but the weights do not name is
 * kept too -- it is part of the raw score, and dropping it would break the sum
 * the heading claims.
 */
export function whyRows(
  row: Listed,
  weights: Record<string, number>,
  order: readonly string[],
  labels: Record<string, string>
): WhyRow[] | null {
  const axes = row.axes;
  if (!axes) return null;

  const reported = new Map(axes.map((axis) => [axis.axis, axis]));
  const named = order.filter((axis) => axis in weights);
  const extra = axes.map((axis) => axis.axis).filter((axis) => !named.includes(axis));
  const list = [...named, ...extra];

  const rows: WhyRow[] = list.map((axis, index) => {
    const held = reported.get(axis);
    return {
      axis,
      label: labels[axis] ?? axis,
      got: held ? held.contribution * 100 : 0,
      of: (weights[axis] ?? 0) * 100,
      share: 0,
      measured: held ? held.value !== null : false,
      color: axisColor(index)
    };
  });

  const most = rows.reduce((max, entry) => Math.max(max, entry.of), 0);
  return rows.map((entry) => ({ ...entry, share: most > 0 ? entry.got / most : 0 }));
}

/** The multiplier line: how the raw score became the final one. */
export interface Multipliers {
  health: number;
  factor: number;
}

/**
 * Health and trim, when either of them is doing something.
 *
 * `null` when both are 1, which is when the line would be arithmetic nobody
 * needs. A row that does not report them counts as 1 each: the equation stays
 * whole, and neither figure is drawn as an absence.
 */
export function multipliers(row: Listed): Multipliers | null {
  const health = row.health ?? 1;
  const factor = row.factor ?? 1;
  if (health === 1 && factor === 1) return null;
  return { health, factor };
}

/**
 * A comparison with the row at the top, with the two gaps kept apart.
 *
 * `behindBy` is signed, in points of the final score: positive means this row
 * is behind the leader, zero means they are level, and negative means this row
 * is ahead (which is possible -- a pinned or hand-listed model can lead while
 * scoring lower -- and must never be printed as "behind"). `rawBehindBy` is the
 * same difference measured before the multipliers; when the two disagree in
 * sign, `byMultipliers` says so, and then no axis is blamed for the order.
 */
export interface Versus {
  leaderName: string;
  behindBy: number;
  /** the axis the leader is ahead on in the raw score, when it leads at all */
  mostlyOn: string | null;
  /** the axis this row leads on, in the raw score, with its gap in points */
  aheadOn: { axis: string; by: number } | null;
  /** health and trim, not the axes, decide the order between the two */
  byMultipliers: boolean;
  /** the same gap measured on raw scores; null when the server does not say */
  rawBehindBy: number | null;
}

function contributions(row: Listed): Record<string, number> {
  const out: Record<string, number> = {};
  for (const axis of row.axes ?? []) out[axis.axis] = axis.contribution;
  return out;
}

export function versusLeader(
  row: Listed,
  leader: Listed | null,
  labels: Record<string, string>
): Versus | null {
  if (!leader || leader.id === row.id) return null;
  if (!row.axes || !leader.axes) return null;

  const mine = contributions(row);
  const theirs = contributions(leader);
  const behindBy = (leader.score - row.score) * 100;
  const rawBehindBy =
    typeof row.raw === 'number' && typeof leader.raw === 'number'
      ? (leader.raw - row.raw) * 100
      : null;

  const behind = behindBy > 0;
  const carried = carriedBy(theirs, mine);
  const mineLeads = carriedBy(mine, theirs);
  const byMultipliers =
    rawBehindBy !== null &&
    (rawBehindBy === 0 ? behindBy !== 0 : Math.sign(rawBehindBy) !== Math.sign(behindBy));

  return {
    leaderName: leader.name,
    behindBy,
    mostlyOn:
      behind && !byMultipliers && carried ? (labels[carried.axis] ?? carried.axis) : null,
    aheadOn: mineLeads
      ? { axis: labels[mineLeads.axis] ?? mineLeads.axis, by: mineLeads.delta * 100 }
      : null,
    byMultipliers,
    rawBehindBy
  };
}

/**
 * The sentence under the bars (section 6.7).
 *
 * It says "behind" only when the row is behind, and when the multipliers
 * decided the order it says that instead of naming an axis. The second half is
 * about the raw score and says so by naming the axis, not by calling it a
 * points gap on the final one.
 */
export function versusText(versus: Versus | null): string {
  if (!versus) return '';
  const gap = Math.abs(versus.behindBy).toFixed(1);
  const lead =
    versus.behindBy > 0
      ? `Behind ${versus.leaderName} by ${gap} points`
      : versus.behindBy < 0
        ? `Ahead of ${versus.leaderName} by ${gap} points`
        : `Level with ${versus.leaderName}`;
  const why =
    versus.behindBy > 0
      ? versus.byMultipliers
        ? ': health and trim decide that gap, not the axes'
        : versus.mostlyOn
          ? `, mostly on ${versus.mostlyOn}`
          : ''
      : '';
  const ahead = versus.aheadOn
    ? ` Ahead on ${versus.aheadOn.axis} by ${versus.aheadOn.by.toFixed(1)}.`
    : '';
  return `${lead}${why}.${ahead}`;
}
