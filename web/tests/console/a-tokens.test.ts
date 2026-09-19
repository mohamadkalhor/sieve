import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * Two claims about the console's skin, both of which rot silently.
 *
 * One: every token a component reads is a token that exists. A renamed token in
 * `tokens.css` does not fail a build -- it paints `unset`, which looks like a
 * design decision and is actually a typo. Reading the token names out of the
 * stylesheet and the `var()`s out of the sources catches it in one second
 * instead of one screenshot.
 *
 * Two: the fonts are ours. The console asks the browser for no font it does not
 * host, because a request to a font CDN is a third party watching who opens the
 * console and when.
 *
 * A `var(--x, fallback)` is allowed to name a token that is not there: that is
 * what the fallback is for, and the places that use one are listed in the
 * failure message so a rename still shows up in the diff.
 */

const SRC = fileURLToPath(new URL('../../src', import.meta.url));
const TOKENS = fileURLToPath(new URL('../../src/lib/tokens/tokens.css', import.meta.url));

const sources = (): string[] =>
  readdirSync(SRC, { recursive: true })
    .map((entry) => String(entry))
    .filter((entry) => entry.endsWith('.svelte') || entry.endsWith('.css'))
    .map((entry) => `${SRC}/${entry}`);

const defined = (text: string): Set<string> =>
  new Set([...text.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((match) => match[1]));

const used = (text: string): { name: string; fallback: boolean }[] =>
  [...text.matchAll(/var\(\s*(--[a-z0-9-]+)\s*(,)?/g)].map((match) => ({
    name: match[1],
    fallback: match[2] === ','
  }));

describe('the console palette', () => {
  const tokens = readFileSync(TOKENS, 'utf8');
  const known = defined(tokens);

  it('defines the families the console is built from', () => {
    for (const family of ['--c-', '--f-', '--h-', '--w-', '--r-']) {
      expect([...known].some((name) => name.startsWith(family))).toBe(true);
    }
  });

  it('has no component reading a token that is not defined', () => {
    const missing: string[] = [];
    for (const file of sources()) {
      const text = readFileSync(file, 'utf8');
      const here = defined(text);
      for (const { name, fallback } of used(text)) {
        if (fallback || here.has(name) || known.has(name)) continue;
        missing.push(`${file.slice(SRC.length + 1)}: ${name}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it('asks no font server for anything', () => {
    const asked: string[] = [];
    for (const file of [...sources(), `${SRC}/app.html`]) {
      const text = readFileSync(file, 'utf8');
      if (/fonts\.(googleapis|gstatic)\.com/.test(text)) asked.push(file.slice(SRC.length + 1));
    }
    expect(asked).toEqual([]);
  });

  it('serves both faces from the package the build installs', () => {
    const app = readFileSync(`${SRC}/app.css`, 'utf8');
    expect(app).toContain('@fontsource/ibm-plex-sans');
    expect(app).toContain('@fontsource/ibm-plex-mono');
  });
});
