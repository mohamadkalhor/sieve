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

/* ------------------------------------------------------------------------ */
/* the ladder                                                                */
/* ------------------------------------------------------------------------ */

import type { LadderRung } from '../../src/lib/api/client';
import { ladderLines, ladderSentence } from '../../src/lib/console/inspector/view';

const rung = (over: Partial<LadderRung> & Pick<LadderRung, 'effort'>): LadderRung => ({
  id: `zai/glm-5-3-${over.effort}`,
  published: true,
  reachable: true,
  score: null,
  intelligence: null,
  here: false,
  ...over
});

/** GLM 5.3 on a seat at medium: low and max published, medium not. */
const GLM: LadderRung[] = [
  rung({ effort: 'low', score: 0.79, intelligence: 34.3, here: true }),
  rung({ effort: 'medium', id: null, published: false, reachable: false }),
  rung({ effort: 'max', score: 0.86, intelligence: 44.8 })
];

describe('ladderLines', () => {
  it('draws the highest effort first, and marks the row the score used', () => {
    const lines = ladderLines(GLM, 'medium');
    expect(lines.map((line) => line.word)).toEqual(['max', 'medium', 'low']);
    expect(lines.find((line) => line.here)?.effort).toBe('low');
    expect(lines[2]).toMatchObject({ score: '0.79', intelligence: '34.3', width: '79%' });
  });

  it('greys an unpublished rung and says the seat runs there', () => {
    const medium = ladderLines(GLM, 'medium')[1];
    expect(medium).toMatchObject({
      published: false,
      width: null,
      score: '',
      seat: true,
      note: 'not published · seat runs here'
    });
    expect(ladderLines(GLM, 'high')[1].note).toBe('not published');
  });

  it('says so when a published rung has no score on this seat', () => {
    const lines = ladderLines([rung({ effort: 'high', intelligence: 40 })], 'high');
    expect(lines[0]).toMatchObject({ width: null, note: 'no score on this seat', intelligence: '40.0' });
  });

  it('says none for a non-reasoning rung', () => {
    expect(ladderLines([rung({ effort: 'non-reasoning' })], null)[0].word).toBe('none');
  });
});

describe('ladderSentence', () => {
  it('explains a stand-in below, and what the top effort would have scored', () => {
    expect(ladderSentence(GLM, 'medium', 'GLM 5.3', 'nearest_below')).toBe(
      'The seat runs at medium. GLM 5.3 publishes no medium row, so it is scored at low, the nearest below, and never credited with max. At max it would score 0.86.'
    );
  });

  it('explains a stand-in above', () => {
    const ladder = [
      rung({ effort: 'low', id: null, published: false }),
      rung({ effort: 'max', score: 0.9, here: true })
    ];
    expect(ladderSentence(ladder, 'low', 'Muse', 'nearest_above')).toBe(
      'The seat runs at low. Muse publishes nothing at or below low, so it is scored at max, the nearest above.'
    );
  });

  it('is short when the seat effort is published', () => {
    const ladder = [rung({ effort: 'medium', score: 0.8, here: true }), rung({ effort: 'max', score: 0.9 })];
    expect(ladderSentence(ladder, 'medium', 'Astra', 'exact')).toBe(
      'The seat runs at medium, and Astra is scored there.'
    );
  });

  it('names any and an id-named effort, and says nothing without a marked row', () => {
    const ladder = [rung({ effort: 'max', score: 0.9, here: true })];
    expect(ladderSentence(ladder, null, 'Sol', 'any')).toBe(
      'Runs at is any, so Sol is scored at max, the row its router id matched.'
    );
    expect(ladderSentence(ladder, 'low', 'Flash', 'id')).toContain('Its router id names max itself');
    expect(ladderSentence([rung({ effort: 'max' })], 'low', 'X', 'exact')).toBe('');
  });
});

/* ------------------------------------------------------------------------ */
/* the Field ring                                                            */
/* ------------------------------------------------------------------------ */

import type { ModelRow } from '../../src/lib/api/client';
import { atEffort, seatLines } from '../../src/lib/field';

const mode = (id: string, family: string | null, effort: string | null): ModelRow =>
  ({ id, family, effort, name: id, creator: 'x' }) as unknown as ModelRow;

const FIELD = [
  mode('astra', 'astra', 'max'),
  mode('astra-medium', 'astra', 'medium'),
  mode('astra-low', 'astra', 'low'),
  mode('glm', 'glm', 'max'),
  mode('glm-low', 'glm', 'low'),
  mode('muse', null, null)
];

describe('the Field from a seat', () => {
  it("draws the lines of the seat's own families", () => {
    expect([...seatLines(FIELD, ['astra', 'muse']).keys()]).toEqual(['astra']);
    expect(seatLines(FIELD, []).size).toBe(0);
  });

  it("rings the mode at the seat's effort, and nothing where none is published", () => {
    const lines = seatLines(FIELD, ['astra', 'glm']);
    expect([...atEffort(lines, 'medium')]).toEqual(['astra-medium']);
    expect([...atEffort(lines, 'low')].sort()).toEqual(['astra-low', 'glm-low']);
    expect(atEffort(lines, null).size).toBe(0);
  });
});
