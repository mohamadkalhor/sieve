<script lang="ts">
  /**
   * The guide: what the loop is, what each screen is for, and where the two
   * readings live that the rail no longer lists.
   *
   * It exists because the rail is six items long on purpose. Sources and Pulse
   * are worth reading and not worth a permanent place beside the screens you
   * actually work on, so they are named here instead of crowding the column.
   */
</script>

<svelte:head><title>Guide · Sieve</title></svelte:head>

<h1>Guide</h1>
<p class="lede">
  Sieve ranks every model it can reach against what each seat cares about, and ships the winning
  list to your gateways. It does that in three steps, and you can run any of them by hand.
</p>

<section>
  <h2>The three steps</h2>
  <dl>
    <dt>Pull sources</dt>
    <dd>
      Fetch the benchmark sources — Artificial Analysis for LLMs and media, OpenRouter for prices,
      anything you put in <code>data/observations</code> by hand — into the store. Nothing is ranked
      here; this is only the measuring.
    </dd>
    <dt>Harvest connectors</dt>
    <dd>
      Ask every connector what it is serving right now, match each local id to a model in the
      catalogue, and re-read the id prefixes that carry your negotiated cost multipliers. A model
      your gateway quietly dropped leaves the inventory here.
    </dd>
    <dt>Ship profiles</dt>
    <dd>
      Rank every seat against that inventory, decide its list, and write the combo to the gateway
      for the seats with auto-apply on. Every seat records a decision, a hold included, so a run
      that changed nothing is as visible as one that changed everything.
    </dd>
    <dt>Full run</dt>
    <dd>Harvest, then pull, then ship — harvest first, so nothing is ranked against a model that is no longer served.</dd>
  </dl>
  <p>
    The box at the top of every page runs any of them and sets how often each should go on its own.
    The <a href="/runs">Runs</a> screen keeps the record, with each run's log.
  </p>
</section>

<section>
  <h2>The screens</h2>
  <ul class="screens">
    <li><a href="/field">Overview</a> — every model in one modality, quality against cost.</li>
    <li><a href="/profiles">Profiles</a> — one row per seat: its controls and the list they produce.</li>
    <li><a href="/axes">Axes</a> — what a score is made of, and which fields carry it.</li>
    <li><a href="/connectors">Connectors</a> — the gateways Sieve reads and writes, and the cost multipliers per prefix.</li>
    <li><a href="/runs">Runs</a> — what the loop has done.</li>
    <li><a href="/sources">Sources</a> — what each benchmark last published, and whether its key is set.</li>
    <li><a href="/pulse">Pulse</a> — what your own traffic says about the models you are shipping.</li>
  </ul>
</section>

<section>
  <h2>Signing in</h2>
  <p>
    Sieve asks <a href="https://gate.mkalhor.xyz" rel="external">gate</a> who you are. When you are
    signed in, every screen writes as you and no token is asked for. A script uses a bearer token
    instead, with the scope the call needs — reads are open, changing a seat needs
    <code>profiles:write</code>, shipping needs <code>apply</code>.
  </p>
</section>

<style>
  h1 {
    margin: 0 0 0.2rem;
    font-size: 1.6rem;
  }
  h2 {
    font-family: var(--ui);
    font-size: 0.95rem;
    margin: 0 0 0.4rem;
  }
  .lede {
    margin: 0 0 1.4rem;
    color: var(--muted);
    font-size: 0.85rem;
    max-width: 46rem;
  }
  section {
    margin-bottom: 1.6rem;
    max-width: 46rem;
  }
  dl {
    margin: 0;
  }
  dt {
    color: var(--ink);
    font-size: 0.82rem;
    margin-top: 0.6rem;
  }
  dd {
    margin: 0.15rem 0 0;
    color: var(--muted);
    font-size: 0.8rem;
    line-height: 1.55;
  }
  p {
    color: var(--muted);
    font-size: 0.8rem;
    line-height: 1.55;
  }
  .screens {
    list-style: none;
    margin: 0;
    padding: 0;
    color: var(--muted);
    font-size: 0.8rem;
    line-height: 1.7;
  }
  a {
    color: var(--accent);
  }
  code {
    font-family: var(--mono);
    font-size: 0.76rem;
  }
</style>
