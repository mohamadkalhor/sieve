<script lang="ts">
  /**
   * One inline stroke icon, 16-box (CONSOLE.md section 3.3).
   *
   * Never an emoji or a text glyph: `✕` and `☰` are typography, so they change
   * shape with the reader's font and cannot be given a stroke weight. The two
   * paths the console already had -- the search glass and the pin -- are copied
   * from `Console.reference.html`, the lock from the profile page; the rest are
   * drawn in the same box and the same 1.5 stroke.
   */
  import type { IconName } from './icon-names';
  import type { SVGAttributes } from 'svelte/elements';

  interface Props extends SVGAttributes<SVGSVGElement> {
    name: IconName;
    /** fills the shape as well as stroking it -- on, pinned, locked */
    filled?: boolean;
    /** the box in px; the drawing scales with it */
    size?: number;
    class?: string;
  }

  let { name, filled = false, size = 16, class: klass = '' }: Props = $props();
</script>

<svg
  class={klass}
  viewBox="0 0 16 16"
  width={size}
  height={size}
  fill="none"
  stroke="currentColor"
  stroke-width="1.5"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  {#if name === 'search'}
    <circle cx="7" cy="7" r="4.5" />
    <path d="M10.5 10.5L14 14" />
  {:else if name === 'pin'}
    <path
      d="M6 1.5h4l-.6 4 2.6 2.5v1.2H9v5.3l-1 .5-1-.5V9.2H4V8l2.6-2.5z"
      fill={filled ? 'currentColor' : 'none'}
      stroke-width="1.2"
    />
  {:else if name === 'x'}
    <path d="M4 4l8 8M12 4l-8 8" />
  {:else if name === 'lock'}
    <rect x="3" y="7" width="10" height="7" rx="1.5" fill={filled ? 'currentColor' : 'none'} stroke-width="1.3" />
    <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" stroke-width="1.3" />
  {:else if name === 'plus'}
    <path d="M8 3.5v9M3.5 8h9" />
  {:else if name === 'minus'}
    <path d="M3.5 8h9" />
  {:else if name === 'chevron'}
    <path d="M5 6.5L8 9.5l3-3" />
  {:else if name === 'dots'}
    <g fill="currentColor" stroke="none">
      <circle cx="3.5" cy="8" r="1.3" />
      <circle cx="8" cy="8" r="1.3" />
      <circle cx="12.5" cy="8" r="1.3" />
    </g>
  {:else if name === 'menu'}
    <path d="M3 4h10M3 8h10M3 12h10" />
  {:else if name === 'check'}
    <path d="M3.5 8.5l3 3 6-7" />
  {:else if name === 'grip'}
    <g fill="currentColor" stroke="none">
      <circle cx="6" cy="4.5" r="1.1" />
      <circle cx="6" cy="8" r="1.1" />
      <circle cx="6" cy="11.5" r="1.1" />
      <circle cx="10" cy="4.5" r="1.1" />
      <circle cx="10" cy="8" r="1.1" />
      <circle cx="10" cy="11.5" r="1.1" />
    </g>
  {:else if name === 'up'}
    <path d="M8 12.5V4M4.5 7.5L8 4l3.5 3.5" />
  {:else if name === 'down'}
    <path d="M8 3.5V12M4.5 8.5L8 12l3.5-3.5" />
  {/if}
</svg>
