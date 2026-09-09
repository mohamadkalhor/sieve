import { describe, expect, it } from 'vitest';
import type { ModelRow } from '../src/lib/api/client';
import {
  effortRank,
  families,
  familyModes,
  mark,
  postedPerMillion,
  providers,
  resolveFamily,
  sameRateShare
} from '../src/lib/field';

/**
 * The Field's search and effort-line arithmetic.
 *
 * These are the rules a person would otherwise have to verify by squinting at a
 * canvas, which is not a test.
 */

function row(partial: Partial<ModelRow> & { id: string }): ModelRow {
  return {
    modality: 'llm',
    name: partial.id,
    creator: partial.id.split('/')[0],
    aliases: [],
    effort: null,
    family: null,
    reachable: false,
    local_ids: [],
    price: null,
    ...partial
  } as ModelRow;
}

/** One family at one rate for every mode -- the common case, measured. */
function luna(): ModelRow[] {
  const at = (rate: number) => ({ input: rate, output: rate * 6, per_unit: null, unit: 'usd_per_1m_tokens' });
  return [
    row({ id: 'openai/gpt-5.6-luna', effort: 'max', family: 'openai/gpt-5.6-luna', name: 'Luna', price: at(0.2) }),
    row({ id: 'openai/gpt-5.6-luna-high', effort: 'high', family: 'openai/gpt-5.6-luna', price: at(0.2) }),
    row({ id: 'openai/gpt-5.6-luna-low', effort: 'low', family: 'openai/gpt-5.6-luna', price: at(0.2) }),
    row({
      id: 'openai/gpt-5.6-luna-non-reasoning',
      effort: 'non-reasoning',
      family: 'openai/gpt-5.6-luna',
      price: at(0.2)
    })
  ];
}

/** One family whose rate really does move with the mode. */
function oss(): ModelRow[] {
  const at = (rate: number) => ({ input: rate, output: rate, per_unit: null, unit: 'usd_per_1m_tokens' });
  return [
    row({ id: 'openai/gpt-oss-120b', effort: 'high', family: 'openai/gpt-oss-120b', price: at(0.07) }),
    row({ id: 'openai/gpt-oss-120b-low', effort: 'low', family: 'openai/gpt-oss-120b', price: at(0.249) })
  ];
}

const solo = row({ id: 'anthropic/claude-opus-5', name: 'Claude Opus 5' });

describe('the effort ladder', () => {
  it('orders the modes least effort first, and sorts an unstated mode last', () => {
    expect(effortRank('non-reasoning')).toBeLessThan(effortRank('low'));
    expect(effortRank('low')).toBeLessThan(effortRank('medium'));
    expect(effortRank('medium')).toBeLessThan(effortRank('high'));
    expect(effortRank('high')).toBeLessThan(effortRank('xhigh'));
    expect(effortRank('xhigh')).toBeLessThan(effortRank('max'));
    // a model with one setting is not a low-effort model
    expect(effortRank(null)).toBeGreaterThan(effortRank('max'));
    expect(effortRank('nonsense')).toBeGreaterThan(effortRank('max'));
  });

  it('draws a family in that order, not in the order it arrived', () => {
    const shuffled = [...luna()].reverse();
    const modes = familyModes(shuffled).get('openai/gpt-5.6-luna');
    expect(modes?.map((m) => m.effort)).toEqual(['non-reasoning', 'low', 'high', 'max']);
  });

  it('is not a family when only one mode is published', () => {
    expect(familyModes([solo, ...luna().slice(0, 1)]).size).toBe(0);
  });
});

describe('the posted rate', () => {
  it('blends input and output at the declared 3 : 1', () => {
    const price = { input: 4, output: 8, per_unit: null, unit: 'usd_per_1m_tokens' };
    expect(postedPerMillion(price)).toBeCloseTo(4 * 0.75 + 8 * 0.25, 10);
  });

  it('falls back to a flat per-unit rate, and to nothing when there is none', () => {
    expect(postedPerMillion({ input: null, output: null, per_unit: 2, unit: 'usd_per_1m_tokens' })).toBe(2);
    expect(postedPerMillion(null)).toBeNull();
  });
});

describe('how many families charge one rate for every mode', () => {
  it('counts them, rather than asserting the number in a sentence', () => {
    expect(sameRateShare([...luna(), ...oss(), solo])).toEqual({ same: 1, total: 2 });
  });

  it('ignores a family whose modes are unpriced: no price is not "the same price"', () => {
    const naked = luna().map((m) => ({ ...m, price: null }));
    expect(sameRateShare(naked)).toEqual({ same: 0, total: 0 });
  });
});

describe('the two searches', () => {
  const all = [...luna(), ...oss(), solo];

  it('offers every provider and every family, and scopes families to a provider', () => {
    expect(providers(all)).toEqual(['anthropic', 'openai']);
    expect(families(all, 'openai').map((f) => f.id)).toEqual([
      'openai/gpt-5.6-luna',
      'openai/gpt-oss-120b'
    ]);
    expect(families(all, 'anthropic').map((f) => f.id)).toEqual(['anthropic/claude-opus-5']);
  });

  it('labels a family with the shortest name any of its modes carries', () => {
    // every mode is called "Luna ..." somewhere; the bare one is the model
    expect(families(luna())[0].label).toBe('Luna');
  });

  it('resolves what was typed as an id, a label, or half of either', () => {
    expect(resolveFamily(all, 'openai/gpt-5.6-luna')).toBe('openai/gpt-5.6-luna');
    expect(resolveFamily(all, 'Luna')).toBe('openai/gpt-5.6-luna');
    expect(resolveFamily(all, 'oss')).toBe('openai/gpt-oss-120b');
    expect(resolveFamily(all, 'nothing like this')).toBeNull();
  });

  it('a provider lights its models and dims the rest -- it never removes them', () => {
    const marked = mark(all, { provider: 'openai', model: '' });
    expect(marked.drawn).toBeNull(); // everything is still drawn
    expect(marked.lit.has('openai/gpt-5.6-luna')).toBe(true);
    expect(marked.lit.has('anthropic/claude-opus-5')).toBe(false);
    // and both of that provider's multi-mode families get a line
    expect([...marked.lines.keys()].sort()).toEqual([
      'openai/gpt-5.6-luna',
      'openai/gpt-oss-120b'
    ]);
  });

  it('a model narrows the chart to that family alone, with its line', () => {
    const marked = mark(all, { provider: '', model: 'Luna' });
    expect(marked.drawn).not.toBeNull();
    expect([...(marked.drawn ?? [])].sort()).toEqual(luna().map((m) => m.id).sort());
    expect(marked.lines.get('openai/gpt-5.6-luna')?.map((m) => m.effort)).toEqual([
      'non-reasoning',
      'low',
      'high',
      'max'
    ]);
  });

  it('a model that publishes one setting is drawn with no line to draw', () => {
    const marked = mark(all, { provider: '', model: 'anthropic/claude-opus-5' });
    expect(marked.drawn).toEqual(new Set(['anthropic/claude-opus-5']));
    expect(marked.lines.size).toBe(0);
  });

  it('an empty pair is not a filter: everything drawn, nothing lit', () => {
    const marked = mark(all, { provider: '', model: '  ' });
    expect(marked.drawn).toBeNull();
    expect(marked.lit.size).toBe(0);
    expect(marked.lines.size).toBe(0);
  });

  it('a model outside the chosen provider does not silently widen the search', () => {
    const marked = mark(all, { provider: 'anthropic', model: 'Luna' });
    // Luna is not an anthropic family, so the provider filter stands
    expect(marked.drawn).toBeNull();
    expect(marked.lit).toEqual(new Set(['anthropic/claude-opus-5']));
  });
});
