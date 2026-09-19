/**
 * The icons the console draws (CONSOLE.md section 3.3).
 *
 * A union rather than a string, so a typo is a compile error instead of an
 * empty box. Kept beside `Icon.svelte` rather than inside it: a component
 * cannot export a type that its callers import without a second script block,
 * and this is the file every caller can reach.
 */
export type IconName =
  | 'search'
  | 'pin'
  | 'x'
  | 'lock'
  | 'plus'
  | 'minus'
  | 'chevron'
  | 'dots'
  | 'menu'
  | 'check'
  | 'grip'
  | 'up'
  | 'down';
