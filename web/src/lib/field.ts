/**
 * The Field's search and effort-line arithmetic, kept out of the component.
 *
 * A model's reasoning effort is a different model (PLAN 2.1a): same price per
 * token, different number of tokens burned, different score. The Field joins
 * one family's modes into a line so the shape of that trade is visible.
 *
 * The uncomfortable measurement this module exists to be honest about: on
 * **posted** price, most families charge the same rate for every mode, so most
 * of those lines are vertical. That is the truth about a price list, and
 * `sameRateShare` counts it from the data on screen rather than asserting it.
 */
import { EFFORT_ORDER } from '$lib/types';
import type { ModelRow } from '$lib/api/client';

/** Where a mode sits on the ladder. A row that declares none sorts last. */
export function effortRank(effort: string | null | undefined): number {
  if (!effort) return EFFORT_ORDER.length;
  const at = (EFFORT_ORDER as readonly string[]).indexOf(effort);
  return at === -1 ? EFFORT_ORDER.length : at;
}

/**
 * A model's posted rate, per million tokens.
 *
 * Input and output are two numbers and an axis needs one, so they are blended
 * at a declared 3:1 — the ratio a chat-shaped workload runs at. The ratio is
 * named on screen beside the axis, because a blend nobody can see is a number
 * pretending to be a fact.
 */
export const BLEND_IN = 0.75;
export const BLEND_OUT = 0.25;

export function postedPerMillion(price: ModelRow['price']): number | null {
  if (!price) return null;
  if (price.input != null || price.output != null) {
    return (price.input ?? 0) * BLEND_IN + (price.output ?? 0) * BLEND_OUT;
  }
  return price.per_unit ?? null;
}

/**
 * One posted number, exactly as published: no blend, no profile, no shape.
 *
 * Every other cost axis here is a *derived* number. `per_task` costs one named
 * profile's declared shape, `per_million` blends input and output at a ratio
 * this file chose. Both are defensible and both are arithmetic done on the
 * reader's behalf, which means neither can answer "what does this actually
 * cost per token". These two can, and they are the only axes on this screen
 * that owe nothing to a preset.
 *
 * `per_unit` is the media case -- an image or a second of video is one posted
 * number already -- and it is returned for either side, because a model that
 * charges per image has no separate input rate to be honest about.
 */
export type RawSide = 'input' | 'output';

export function rawPerMillion(price: ModelRow['price'], side: RawSide): number | null {
  if (!price) return null;
  if (price.input == null && price.output == null) return price.per_unit ?? null;
  return (side === 'input' ? price.input : price.output) ?? null;
}

/** Does this population post input and output rates at all? */
export function hasSidedPrices(models: ModelRow[]): boolean {
  return models.some((m) => m.price?.input != null || m.price?.output != null);
}

/** Every family that publishes more than one mode, its modes in effort order. */
export function familyModes(models: ModelRow[]): Map<string, ModelRow[]> {
  const out = new Map<string, ModelRow[]>();
  for (const model of models) {
    if (!model.family) continue;
    const held = out.get(model.family);
    if (held) held.push(model);
    else out.set(model.family, [model]);
  }
  for (const [family, modes] of out) {
    if (modes.length < 2) out.delete(family);
    else modes.sort((a, b) => effortRank(a.effort) - effortRank(b.effort) || a.id.localeCompare(b.id));
  }
  return out;
}

/**
 * Of the families that publish several modes, how many charge one rate for all
 * of them.
 *
 * Counted, not claimed, and counted on whatever dataset is loaded — his box and
 * these recordings are different populations and both get to be true.
 */
export function sameRateShare(models: ModelRow[]): { same: number; total: number } {
  let same = 0;
  let total = 0;
  for (const modes of familyModes(models).values()) {
    const rates = modes
      .map((m) => postedPerMillion(m.price))
      .filter((r): r is number => r != null)
      .map((r) => Math.round(r * 1e6) / 1e6);
    if (rates.length < 2) continue;
    total += 1;
    if (new Set(rates).size === 1) same += 1;
  }
  return { same, total };
}

/** Providers present in this modality, for the datalist. */
export function providers(models: ModelRow[]): string[] {
  return [...new Set(models.map((m) => m.creator).filter(Boolean))].sort();
}

/**
 * The families a provider offers, by family id, labelled with the shortest
 * name any of its modes carries.
 *
 * Nobody types `openai/gpt-5-6-terra-non-reasoning` from memory, so the model
 * input lists families, not rows.
 */
export function families(models: ModelRow[], provider?: string): { id: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const model of models) {
    if (provider && model.creator !== provider) continue;
    const id = model.family || model.id;
    const label = model.name || model.id;
    const held = seen.get(id);
    if (held === undefined || label.length < held.length) seen.set(id, label);
  }
  return [...seen].map(([id, label]) => ({ id, label })).sort((a, b) => a.id.localeCompare(b.id));
}

export interface Selection {
  provider: string;
  /** a family id, or a label the user typed that resolves to one */
  model: string;
}

/**
 * Turn what is typed into a family id, tolerantly.
 *
 * The datalist offers labels; a person may type an id, a label, or half of
 * either. Matching all three beats making them guess which one the box wants.
 */
export function resolveFamily(models: ModelRow[], typed: string, provider?: string): string | null {
  const needle = typed.trim().toLowerCase();
  if (!needle) return null;
  const scope = families(models, provider || undefined);
  return (
    scope.find((f) => f.id.toLowerCase() === needle)?.id ??
    scope.find((f) => f.label.toLowerCase() === needle)?.id ??
    scope.find((f) => f.id.toLowerCase().includes(needle))?.id ??
    scope.find((f) => f.label.toLowerCase().includes(needle))?.id ??
    null
  );
}

export interface Marked {
  /** ids to draw lit */
  lit: Set<string>;
  /** ids to draw at all — everything, unless a model is pinned */
  drawn: Set<string> | null;
  /** family id -> its mode rows, in effort order, for the lines */
  lines: Map<string, ModelRow[]>;
}

/**
 * What the search means for the chart.
 *
 * Provider set: its models lit, everything else **still drawn** and dimmed. He
 * asked for grey rather than hidden and he was right — the rest of the field is
 * what makes the green mean anything.
 *
 * Model set: only that family is drawn.
 */
export function mark(models: ModelRow[], selection: Selection): Marked {
  const provider = selection.provider.trim().toLowerCase();
  const family = resolveFamily(models, selection.model, selection.provider.trim());

  if (family) {
    const rows = models.filter((m) => (m.family || m.id) === family);
    const ids = new Set(rows.map((m) => m.id));
    return {
      lit: ids,
      drawn: ids,
      lines: rows.length > 1 ? new Map([[family, ordered(rows)]]) : new Map()
    };
  }

  if (!provider) return { lit: new Set(), drawn: null, lines: new Map() };

  const mine = models.filter((m) => m.creator.toLowerCase() === provider);
  return {
    lit: new Set(mine.map((m) => m.id)),
    drawn: null,
    lines: familyModes(mine)
  };
}

function ordered(rows: ModelRow[]): ModelRow[] {
  return [...rows].sort(
    (a, b) => effortRank(a.effort) - effortRank(b.effort) || a.id.localeCompare(b.id)
  );
}
