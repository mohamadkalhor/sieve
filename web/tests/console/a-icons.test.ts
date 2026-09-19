import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * The icon set and its names are one thing in two files.
 *
 * `icon-names.ts` is the union a caller has to satisfy; `Icon.svelte` is the
 * drawing. Add a name to the union and forget the branch and the icon renders
 * as an empty box -- a valid component, a blank spot in the top bar. Add the
 * branch and forget the union and nothing can ask for it.
 *
 * Reading both files and comparing the two sets is the only way to see that
 * without opening the console at the right moment.
 */

const UI = fileURLToPath(new URL('../../src/lib/console/ui', import.meta.url));

const names = (): string[] => {
  const text = readFileSync(`${UI}/icon-names.ts`, 'utf8');
  const union = text.slice(text.indexOf('=') + 1, text.lastIndexOf(';'));
  return [...union.matchAll(/'([a-z0-9-]+)'/g)].map((match) => match[1]);
};

const drawn = (): string[] => {
  const text = readFileSync(`${UI}/Icon.svelte`, 'utf8');
  return [...text.matchAll(/name === '([a-z0-9-]+)'/g)].map((match) => match[1]);
};

describe('the icon set', () => {
  it('has a name for every icon', () => {
    const named = names();
    expect(named.length).toBeGreaterThan(0);
    expect([...drawn()].sort()).toEqual([...new Set(named)].sort());
  });

  it('draws in one box, so no icon is drawn at another size', () => {
    const text = readFileSync(`${UI}/Icon.svelte`, 'utf8');
    expect(text).toContain('viewBox="0 0 16 16"');
  });

  it('never names an icon with a text glyph', () => {
    const glyphs = names().filter((name) => !/^[a-z][a-z0-9-]*$/.test(name));
    expect(glyphs).toEqual([]);
  });
});
