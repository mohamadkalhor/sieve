<script lang="ts">
  /**
   * The table's view switch (CONSOLE.md section 6.6).
   *
   * Two views, one state: the lineup the seat would ship, or every reachable
   * model it was drawn from. The count travels with the second label because
   * "all reachable" is a promise about a list nobody has seen yet, and the
   * number is the only way to know it before going there.
   */
  import Segmented from '../ui/Segmented.svelte';

  interface Props {
    view: 'lineup' | 'all';
    /** how many models the server says are reachable at all */
    pool: number;
    onview: (view: 'lineup' | 'all') => void;
  }

  let { view, pool, onview }: Props = $props();
</script>

<div class="switch">
  <Segmented
    label="What the table shows"
    value={view}
    onchange={(next) => onview(next === 'all' ? 'all' : 'lineup')}
    options={[
      { value: 'lineup', label: 'Lineup' },
      { value: 'all', label: `All reachable ${pool}` }
    ]}
  />
</div>

<style>
  /* The switch is the table's first row, so it starts where the table's own
     cells start -- the 20px it used to carry put it out of line with the
     columns under it. `min-height` rather than `height`: the control is 34px
     (48px on a phone), and a 32px box let it hang over the table's top rule. */
  .switch {
    display: flex;
    align-items: center;
    min-height: 32px;
    padding: 0 12px;
    border-top: 1px solid var(--c-rule);
  }
</style>
