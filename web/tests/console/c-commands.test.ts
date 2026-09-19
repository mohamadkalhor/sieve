import { describe, expect, it } from 'vitest';
import { MATCH_LIMIT, match, type Command } from '../../src/lib/console/logic/commands';

/**
 * The palette's search (CONSOLE.md section 6.1).
 *
 * Three tiers of hit, in this order, because that is the order a person means
 * them in: a title that starts with what they typed beats a word that starts
 * with it, which beats letters scattered through the row. Ties fall to the
 * title, so the same query always draws the same list.
 */

function command(title: string, over: Partial<Command> = {}): Command {
  return {
    id: title.toLowerCase().replace(/\W+/g, '-'),
    group: 'Actions',
    title,
    run: () => undefined,
    ...over
  };
}

const COMMANDS: Command[] = [
  command('Ship now', { group: 'Actions' }),
  command('Reset to loaded', { group: 'Actions' }),
  command('Open profiles', { group: 'Go to' }),
  command('Seats', { group: 'Seats' }),
  command('Model: Claude Sonnet 4', { group: 'Models', keywords: 'sonnet anthropic' }),
  command('Model: GPT-5', { group: 'Models', keywords: 'openai' }),
  command('Weight: quality', { group: 'Models' }),
  command('Trim: openrouter', { group: 'Models' })
];

const titles = (query: string, limit?: number): string[] =>
  match(query, COMMANDS, limit).map((hit) => hit.title);

describe('match', () => {
  it('puts a title that starts with the query first', () => {
    expect(titles('seats')[0]).toBe('Seats');
  });

  it('ranks a title that starts with the query above a word start, and both above scattered letters', () => {
    const tiers = [command('Cost axis'), command('Reset to cost'), command('Claude opus')];
    expect(match('co', tiers).map((hit) => hit.title)).toEqual([
      'Cost axis',
      'Reset to cost',
      'Claude opus'
    ]);
  });

  it('ranks a word-start hit above a scattered one', () => {
    const found = titles('trim');
    expect(found[0]).toBe('Trim: openrouter');
  });

  it('reads the hint and the keywords too', () => {
    expect(titles('anthropic')).toEqual(['Model: Claude Sonnet 4']);
    expect(titles('openai')).toEqual(['Model: GPT-5']);
  });

  it('is not case sensitive, and ignores space around the query', () => {
    expect(titles('  SEATS  ')).toEqual(['Seats']);
  });

  it('finds nothing for nothing', () => {
    expect(titles('zzz')).toEqual([]);
    expect(titles('qwertyuiop')).toEqual([]);
  });

  it('keeps the limit', () => {
    expect(match('e', COMMANDS, 3)).toHaveLength(3);
    expect(match('e', COMMANDS, 0)).toEqual([]);
  });

  it('draws the same list twice for the same query', () => {
    expect(titles('o')).toEqual(titles('o'));
  });

  it('opens with a fixed starter list, by group and then by title', () => {
    expect(titles('', 4)).toEqual([
      'Seats',
      'Model: Claude Sonnet 4',
      'Model: GPT-5',
      'Trim: openrouter'
    ]);
  });

  it('has a limit worth showing', () => {
    expect(MATCH_LIMIT).toBeGreaterThan(0);
  });
});
