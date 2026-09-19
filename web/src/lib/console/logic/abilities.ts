/**
 * What a model can do, who said so, and what a seat's count actually counts
 * (CONSOLE.md section 5.2, REVIEW.md finding 10).
 *
 * Three states, and the third one is the point: `yes`, `no`, and *nobody said*.
 * A claim with no author behind it is not a claim, so `who` never invents one;
 * and an unmeasured ability is never rendered as "no" -- the server sends
 * `null` for "no source covers this", and a seat that reads that as "no" would
 * be turning an absence into a fact about the model.
 *
 * `describable` is the same line drawn for a whole pool: a capacity count is
 * only worth drawing when at least one model has an answer to count.
 */
import { NEEDS, type Need } from '$lib/api/client';

/** What a need is called in front of a person. */
export const NEED_LABEL: Record<Need, string> = {
  vision: 'Image input',
  reasoning: 'Thinking mode',
  tools: 'Tool calling',
  structured_output: 'Structured output'
};

/** What a need is called in a table of sources, where it is a routing tag. */
export const NEED_TAG: Record<Need, string> = {
  vision: 'vision',
  reasoning: 'thinking',
  tools: 'tools',
  structured_output: 'JSON'
};

/** The answer, for a cell that paints three different ways. */
export type Tone = 'yes' | 'no' | 'unknown';

/** `null` and absent are both "nobody said", and both are their own answer. */
export function tone(answer: boolean | null | undefined): Tone {
  if (answer === true) return 'yes';
  if (answer === false) return 'no';
  return 'unknown';
}

/** Who answered, when the server says nothing about it. */
export const SOURCE_NOT_REPORTED = 'source not reported';

/** Who answered nothing at all -- a different sentence from the one above. */
export const NO_SOURCE_SAYS = 'no source says';

/** One ability as the model card carries it. */
export interface Abilities {
  answer?: boolean | null;
  /** the sources that said yes, when the answer is yes */
  yes?: string[];
  /** the sources that said no, when the answer is no */
  no?: string[];
}

/** `inventory: openrouter` -> `openrouter`: the source, not the table it sits in. */
function source(name: string): string {
  return name.replace(/^inventory:\s*/, '').trim();
}

/**
 * The authors of one answer, as a comma-separated list.
 *
 * An answer with no named sources reads "source not reported", because
 * something did answer; a missing answer reads "no source says", because
 * nothing did. Those are the two sentences a person can act on differently.
 */
export function who(entry: Abilities | null | undefined): string {
  const answer = entry?.answer;
  if (answer !== true && answer !== false) return NO_SOURCE_SAYS;
  const said = (answer ? entry?.yes : entry?.no) ?? [];
  const names = said.map(source).filter(Boolean);
  return names.length ? names.join(', ') : SOURCE_NOT_REPORTED;
}

export interface AbilitiesHolder {
  abilities?: Partial<Record<Need, boolean | null>>;
}

/**
 * How many models in a pool are known to do each need.
 *
 * Only `true` counts. A `null` is not a no, it is a gap in the sources, and a
 * count that folded it in would understate every capacity the catalogue does
 * not cover.
 */
export function supportCount(pool: readonly AbilitiesHolder[]): Record<Need, number> {
  const counts: Record<Need, number> = {
    vision: 0,
    reasoning: 0,
    tools: 0,
    structured_output: 0
  };
  for (const row of pool) {
    for (const need of NEEDS) if (row.abilities?.[need] === true) counts[need] += 1;
  }
  return counts;
}

/**
 * Whether this pool has anything to say about abilities at all.
 *
 * False on the fallback path (a server that sends no `abilities`), where the
 * honest thing is to leave the counts out rather than to draw four zeroes.
 */
export function describable(pool: readonly AbilitiesHolder[]): boolean {
  return pool.some((row) =>
    NEEDS.some((need) => {
      const answer = row.abilities?.[need];
      return answer === true || answer === false;
    })
  );
}
