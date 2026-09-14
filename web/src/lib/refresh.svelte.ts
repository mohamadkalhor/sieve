/**
 * "Make sure this list updates every time."
 *
 * A run rewrites the inventory, the rankings and the chains, and a screen that
 * fetched them before the run was over shows yesterday. Rather than teach
 * every screen about runs, the layout bumps a counter when a run finishes and
 * every list that reads it re-fetches -- no reload, no per-page plumbing.
 */
class Pulse {
  /** bumped whenever something happened that invalidates what is on screen */
  stamp = $state(0);

  bump(): void {
    this.stamp += 1;
  }

  /**
   * Read the counter, and say so out loud.
   *
   * An `$effect` re-runs when anything it read changes, so reading this is the
   * whole subscription -- but `runPulse.stamp;` on its own is a bare
   * expression the linter is right to refuse. A call reads it and is a
   * statement.
   */
  seen(): number {
    return this.stamp;
  }
}

export const runPulse = new Pulse();
