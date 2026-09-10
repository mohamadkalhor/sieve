/**
 * What a source is called, for a reader rather than for the store.
 *
 * `elo` is Artificial Analysis's own unit for its media arenas, so a board
 * headed only "Ranked by elo" names a unit and no author, and reads as though
 * some fourth party produced the numbers. Every media score in this system is
 * AA's, and the one case where that would stop being true — a second
 * scoreboard folded in — is exactly the case a reader has to be able to see.
 *
 * This lives beside `types.ts` rather than in it because that file is
 * generated from the Python contracts by `sieve export-types`, and a test
 * compares it against a fresh render. Anything hand-written there is deleted
 * by the next export.
 */

export const SOURCE_LABEL: Record<string, string> = {
  aa_llm: 'Artificial Analysis',
  aa_media: 'Artificial Analysis',
  aa_media_prices: 'Artificial Analysis',
  arena: 'LMArena',
  deepinfra: 'DeepInfra',
  fal: 'fal',
  manual: 'a manual drop-in',
  openrouter: 'OpenRouter'
};

/**
 * One phrase naming whoever produced these numbers.
 *
 * Deduplicated, because `aa_llm` and `aa_media` are one organisation and
 * "Artificial Analysis and Artificial Analysis" is not a sentence. An unknown
 * source keeps its own id rather than being dropped: a name nobody recognises
 * is still information, and silence is not.
 */
export function sourceWords(sources: string[] | undefined): string {
  const names = [...new Set((sources ?? []).map((s) => SOURCE_LABEL[s] ?? s))];
  if (names.length === 0) return '';
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
}
