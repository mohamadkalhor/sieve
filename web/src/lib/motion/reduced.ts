/**
 * Does this person want motion?
 *
 * Every animation in the app asks first. A ranking that slides is easier to
 * follow than one that teleports, but only for people who can watch it move --
 * for anyone else the same movement is noise at best.
 */
import { readable } from 'svelte/store';

const QUERY = '(prefers-reduced-motion: reduce)';

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia(QUERY).matches;
}

/** Reactive, because a person can change the setting while the page is open. */
export const reducedMotion = readable(prefersReducedMotion(), (set) => {
  if (typeof window === 'undefined' || !window.matchMedia) return;
  const media = window.matchMedia(QUERY);
  const update = () => set(media.matches);
  update();
  media.addEventListener('change', update);
  return () => media.removeEventListener('change', update);
});

/** Milliseconds for an animation, or 0 when motion is not wanted. */
export function duration(ms: number, reduced: boolean): number {
  return reduced ? 0 : ms;
}
