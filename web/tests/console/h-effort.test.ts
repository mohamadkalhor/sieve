/**
 * Efforts on screen (EFFORT.md sections 6 and 7): the pill after a row's name,
 * and the sentence beside Runs at. Both are pure, so every look and every
 * wording is pinned here rather than in a browser.
 */
import { describe, expect, it } from 'vitest';
import type { Listed } from '../../src/lib/api/client';
import { effortHint, effortPill, effortWord } from '../../src/lib/console/seat/rows';

function row(over: Partial<Listed> = {}): Listed {
  return { id: 'glm-5-3', name: 'GLM 5.3', local_ids: ['ocg/glm-5.3'], score: 0.79, ...over };
}

describe('effortWord', () => {
  it('says none for non-reasoning and the name for the rest', () => {
    expect(effortWord('non-reasoning')).toBe('none');
    expect(effortWord('xhigh')).toBe('xhigh');
  });
});

describe('effortPill', () => {
  it('draws the seat effort in the exact look', () => {
    const pill = effortPill(row({ effort: 'medium', effort_how: 'exact' }), 'medium');
    expect(pill).toEqual({
      look: 'exact',
      text: 'medium',
      title: 'Scored at medium, the effort this seat runs at'
    });
  });

  it('marks a stand-in below with a down arrow and names what is missing', () => {
    const pill = effortPill(row({ effort: 'low', effort_how: 'nearest_below' }), 'medium');
    expect(pill?.look).toBe('near');
    expect(pill?.text).toBe('low ↓');
    expect(pill?.title).toBe(
      'No medium row is published for GLM 5.3. Scored at low, the nearest effort below.'
    );
  });

  it('marks a stand-in above with an up arrow', () => {
    const pill = effortPill(row({ effort: 'max', effort_how: 'nearest_above' }), 'low');
    expect(pill?.look).toBe('near');
    expect(pill?.text).toBe('max ↑');
    expect(pill?.title).toContain('none below it');
  });

  it('says the router id chose, and names the id', () => {
    const pill = effortPill(
      row({ name: 'Gemini 3.8 Flash', local_ids: ['ag/gemini-3.8-flash-high'], effort: 'high', effort_how: 'id' }),
      'medium'
    );
    expect(pill?.look).toBe('id');
    expect(pill?.text).toBe('high · id');
    expect(pill?.title).toBe(
      'The router id ag/gemini-3.8-flash-high names its own effort, so the seat setting does not apply'
    );
  });

  it('says one setting whatever the effort field holds', () => {
    expect(effortPill(row({ effort: null, effort_how: 'one' }), 'medium')).toMatchObject({
      look: 'one',
      text: 'one setting'
    });
  });

  it('draws an any row as plain text, and nothing when there is no effort', () => {
    expect(effortPill(row({ effort: 'max', effort_how: 'any' }), null)).toMatchObject({
      look: 'any',
      text: 'max'
    });
    expect(effortPill(row({ effort: null, effort_how: 'any' }), null)).toBeNull();
    expect(effortPill(row(), null)).toBeNull();
  });

  it('says none for a non-reasoning row', () => {
    expect(effortPill(row({ effort: 'non-reasoning', effort_how: 'exact' }), 'non-reasoning')?.text).toBe(
      'none'
    );
  });

  it("speaks of the seat's effort when it does not know which one", () => {
    expect(effortPill(row({ effort: 'low', effort_how: 'nearest_below' }), null)?.title).toContain(
      "No row at the seat's effort is published for GLM 5.3."
    );
  });
});

describe('effortHint', () => {
  it('says nothing while the effort is unchanged', () => {
    expect(effortHint(null, null, 3)).toBe('');
    expect(effortHint(undefined, null, 3)).toBe('');
    expect(effortHint('medium', 'medium', 0)).toBe('');
  });

  it('explains what any meant and counts the rows it moved', () => {
    expect(effortHint(null, 'medium', 3)).toBe(
      "Was any: scored at each model's top effort. Moves 3 rows."
    );
    expect(effortHint(null, 'medium', 1)).toBe(
      "Was any: scored at each model's top effort. Moves 1 row."
    );
  });

  it('names the old effort, and waits for the list before counting', () => {
    expect(effortHint('high', 'medium', null)).toBe('Was high.');
    expect(effortHint('non-reasoning', 'low', 0)).toBe('Was none. Moves no rows.');
  });
});
