<script lang="ts">
  /**
   * The Field: one canvas, every measured model, cost on a log x-axis.
   *
   * 644 points have to stay smooth, so the scene is drawn once into an
   * offscreen canvas and only the hover marker is repainted per frame. Hit
   * testing goes through a coarse grid rather than a scan of every point.
   *
   * A search may light one provider's models and dim the rest, and join one
   * model's effort modes into a line. Dimmed is still **drawn**: the rest of
   * the field is the context that makes the lit ones mean something.
   */
  import { scaleLinear, scaleLog } from 'd3-scale';

  export interface Point {
    id: string;
    x: number | null; // cost per task, USD
    y: number | null; // axis value, 0..1
    reachable: boolean;
    primary: boolean;
    /** a search is running and this is not one of its answers */
    dim?: boolean;
    /** a search is running and this is one of its answers */
    lit?: boolean;
    /** a model is pinned and this is not one of its modes: not drawn at all */
    hidden?: boolean;
    /** the effort mode this row is, when it is one */
    effort?: string | null;
    /**
     * Where x came from: `true` the model's own observed tokens, `false` a
     * posted price standing in for them, `undefined` when the axis *is* the
     * price list and the distinction does not exist.
     */
    measured?: boolean;
  }

  /** One model's effort modes, already in effort order. */
  export interface Line {
    family: string;
    ids: string[];
  }

  interface Props {
    points: Point[];
    lines?: Line[];
    xLabel?: string;
    yLabel?: string;
    height?: number;
    onselect?: (id: string) => void;
  }
  let {
    points,
    lines = [],
    xLabel = 'cost per task (USD)',
    yLabel = 'axis',
    height = 420,
    onselect
  }: Props = $props();

  let host: HTMLElement | undefined = $state();
  let canvas: HTMLCanvasElement | undefined = $state();
  let width = $state(720);
  let hovered: Point | null = $state(null);
  let pointer = $state({ x: 0, y: 0 });

  const PAD = { top: 18, right: 18, bottom: 34, left: 44 };
  const CELL = 24;

  const plotted = $derived(points.filter((p) => p.x !== null && p.y !== null && (p.x as number) > 0));

  const shown = $derived(plotted.filter((p) => !p.hidden));

  /**
   * Which population the cost axis is scaled to.
   *
   * A **provider** search dims the rest of the field but keeps it on screen, so
   * the domain stays the whole population: the greys are the comparison, and
   * rescaling would move them under the reader.
   *
   * A **pinned model** removes everything else, and then the population on
   * screen *is* that family — six modes squeezed into thirty pixels at the
   * right edge answer nothing. So the axis becomes the family's own, and the
   * tick labels say so.
   *
   * The quality axis is not rescaled either way: it is a normalised 0..1 and
   * the full range is the meaning. Zooming it would turn three hundredths of a
   * point into half a chart.
   */
  const scales = $derived.by(() => {
    const domain = shown.length && shown.length < plotted.length ? shown : plotted;
    const xs = domain.map((p) => p.x as number);
    const ys = plotted.map((p) => p.y as number);
    // The fallbacks are for an empty axis only. Folding them into the min and
    // max the way this used to read pinned the domain to $0.0001 whatever was
    // on screen, so a filtered field stayed squashed against the right edge and
    // the rescale above did nothing.
    const low = xs.length ? Math.min(...xs) : 0.0001;
    const high = xs.length ? Math.max(...xs) : 0.001;
    const x = scaleLog()
      .domain([low * 0.8, high * 1.2])
      .range([PAD.left, width - PAD.right])
      .clamp(true);
    const y = scaleLinear()
      .domain([Math.min(0, ...ys), Math.max(1, ...ys)])
      .range([height - PAD.bottom, PAD.top])
      .clamp(true);
    // the cost range is published on the figure so a test can assert the
    // rescale above happened, without guessing at canvas pixels
    return { x, y, low: low * 0.8, high: high * 1.2 };
  });

  const placed = $derived(
    plotted.map((point) => ({
      point,
      px: scales.x(point.x as number),
      py: scales.y(point.y as number)
    }))
  );

  const at = $derived(new Map(placed.map((item) => [item.point.id, item])));

  /** Coarse grid: a hover looks at one cell, not 644 points. */
  const index = $derived.by(() => {
    const grid = new Map<string, typeof placed>();
    for (const item of placed) {
      if (item.point.hidden) continue;
      const key = `${Math.floor(item.px / CELL)}:${Math.floor(item.py / CELL)}`;
      const bucket = grid.get(key) ?? [];
      bucket.push(item);
      grid.set(key, bucket);
    }
    return grid;
  });

  /** `$0.004`, `$1.20`, `$45` -- never `4.0e-3`. */
  function money(value: number): string {
    if (value >= 1) return `$${value >= 10 ? value.toFixed(0) : value.toFixed(2)}`;
    if (value >= 0.001) return `$${value.toFixed(3)}`;
    return `$${value.toFixed(5)}`;
  }

  /** A measured point is a disc; an estimate is a hollow square. */
  function node(
    ctx: CanvasRenderingContext2D,
    px: number,
    py: number,
    radius: number,
    measured: boolean
  ) {
    if (measured) {
      ctx.beginPath();
      ctx.arc(px, py, radius, 0, Math.PI * 2);
      ctx.fill();
      return;
    }
    const side = radius * 1.7;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(px - side / 2, py - side / 2, side, side);
    ctx.lineWidth = 1;
  }

  /**
   * Labels for one line, pushed apart so a vertical line stays readable.
   *
   * Most of these lines *are* vertical -- a family usually charges one rate for
   * every mode -- which stacks six labels on one x. Nudging them apart is the
   * difference between six modes and one smudge.
   */
  function stack(nodes: { py: number }[], gap: number): number[] {
    const order = nodes.map((n, i) => ({ i, py: n.py })).sort((a, b) => a.py - b.py);
    const out = new Array<number>(nodes.length);
    let last = -Infinity;
    for (const { i, py } of order) {
      const pushed = Math.max(py, last + gap);
      out[i] = pushed;
      last = pushed;
    }
    return out;
  }

  function draw() {
    if (!canvas) return;
    const dpr = Math.min(2, globalThis.devicePixelRatio || 1);
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const style = getComputedStyle(document.documentElement);
    const colour = (name: string) => style.getPropertyValue(name).trim() || '#888';

    // axes
    ctx.strokeStyle = colour('--rule');
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(PAD.left, PAD.top);
    ctx.lineTo(PAD.left, height - PAD.bottom);
    ctx.lineTo(width - PAD.right, height - PAD.bottom);
    ctx.stroke();

    ctx.fillStyle = colour('--muted');
    ctx.font = '10px "IBM Plex Mono", monospace';
    for (const tick of scales.y.ticks(5)) {
      const py = scales.y(tick);
      ctx.fillText(tick.toFixed(1), 8, py + 3);
      ctx.globalAlpha = 0.35;
      ctx.beginPath();
      ctx.moveTo(PAD.left, py);
      ctx.lineTo(width - PAD.right, py);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    // A log axis over four orders of magnitude returns far more ticks than fit,
    // so the decades carry it. Over a narrow one -- which is what a pinned
    // family is -- there may be only one decade in the whole domain, and a cost
    // axis with a single label is not a cost axis. So fall back to the ticks
    // themselves, and let the collision guard below thin them.
    const ticks = scales.x.ticks(4);
    const decades = ticks.filter((t) => Math.abs(Math.log10(t) - Math.round(Math.log10(t))) < 1e-9);
    let lastLabelEnd = -Infinity;
    for (const tick of decades.length >= 3 ? decades : ticks) {
      const px = scales.x(tick);
      const text = money(tick);
      const halfWidth = ctx.measureText(text).width / 2;
      if (px - halfWidth < lastLabelEnd + 8) continue;
      ctx.fillText(text, px - halfWidth, height - PAD.bottom + 14);
      lastLabelEnd = px + halfWidth;
    }

    // faint first, lit second, decisions last, so the eye lands on the decision
    const order = [...placed].sort(
      (a, b) =>
        Number(a.point.lit ?? false) - Number(b.point.lit ?? false) ||
        Number(a.point.primary) - Number(b.point.primary) ||
        Number(a.point.reachable) - Number(b.point.reachable)
    );
    for (const { point, px, py } of order) {
      if (point.hidden) continue;
      if (point.dim) {
        // still there, and out of the way: a search greys the field, it does
        // not delete it
        ctx.fillStyle = colour('--muted');
        ctx.globalAlpha = 0.22;
        ctx.beginPath();
        ctx.arc(px, py, 2.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
        continue;
      }
      if (point.lit) {
        ctx.fillStyle = colour('--good');
        ctx.strokeStyle = colour('--good');
        node(ctx, px, py, point.primary ? 4.5 : 3.5, point.measured !== false);
      } else {
        ctx.beginPath();
        ctx.arc(px, py, point.primary ? 4.5 : 3, 0, Math.PI * 2);
        if (point.primary) {
          ctx.fillStyle = colour('--accent');
        } else if (point.reachable) {
          ctx.fillStyle = colour('--reach');
        } else {
          ctx.fillStyle = colour('--muted');
          ctx.globalAlpha = 0.35;
        }
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      if (point.primary) {
        ctx.strokeStyle = colour(point.lit ? '--good' : '--accent');
        ctx.globalAlpha = 0.5;
        ctx.beginPath();
        ctx.arc(px, py, 8, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }

    drawLines(ctx, colour);
  }

  function drawLines(ctx: CanvasRenderingContext2D, colour: (name: string) => string) {
    for (const line of lines) {
      const nodes = line.ids
        .map((id) => at.get(id))
        .filter((item): item is NonNullable<typeof item> => Boolean(item));
      if (nodes.length < 2) continue;

      ctx.strokeStyle = colour('--good');
      ctx.globalAlpha = 0.85;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      nodes.forEach(({ px, py }, i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.globalAlpha = 1;

      // Label the MODE, not the model: the name is identical on every node and
      // would be six copies of one word.
      const ys = stack(nodes, 11);
      ctx.fillStyle = colour('--ink');
      ctx.font = '9px "IBM Plex Mono", monospace';
      nodes.forEach(({ px, point }, i) => {
        const text = point.effort ?? 'one setting';
        const w = ctx.measureText(text).width;
        const left = px + 7 + w > width - PAD.right ? px - 7 - w : px + 7;
        ctx.globalAlpha = 0.9;
        ctx.fillText(text, left, ys[i] + 3);
        ctx.globalAlpha = 1;
      });
      ctx.font = '10px "IBM Plex Mono", monospace';
    }
  }

  function nearest(mx: number, my: number) {
    const cx = Math.floor(mx / CELL);
    const cy = Math.floor(my / CELL);
    let best: { point: Point; px: number; py: number } | null = null;
    let bestDistance = 18 * 18;
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        for (const item of index.get(`${cx + dx}:${cy + dy}`) ?? []) {
          const distance = (item.px - mx) ** 2 + (item.py - my) ** 2;
          if (distance < bestDistance) {
            bestDistance = distance;
            best = item;
          }
        }
      }
    }
    return best;
  }

  function onmove(event: MouseEvent) {
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const mx = event.clientX - box.left;
    const my = event.clientY - box.top;
    const found = nearest(mx, my);
    hovered = found?.point ?? null;
    pointer = { x: mx, y: my };
  }

  $effect(() => {
    if (!host) return;
    const observer = new ResizeObserver(([entry]) => {
      width = Math.max(320, Math.round(entry.contentRect.width));
    });
    observer.observe(host);
    return () => observer.disconnect();
  });

  $effect(() => {
    void [placed, lines, width, height];
    draw();
  });
</script>

<figure
  class="field"
  bind:this={host}
  style:height={`${height}px`}
  aria-label={`${plotted.length} models plotted, ${yLabel} against ${xLabel}`}
  data-cost-domain={`${scales.low.toPrecision(4)}..${scales.high.toPrecision(4)}`}
>
  <canvas
    bind:this={canvas}
    style:width="100%"
    style:height={`${height}px`}
    onmousemove={onmove}
    onmouseleave={() => (hovered = null)}
    onclick={() => hovered && onselect?.(hovered.id)}
  ></canvas>

  {#if hovered}
    <div
      class="tip mono"
      style:left={`${Math.min(pointer.x + 12, width - 240)}px`}
      style:top={`${pointer.y + 12}px`}
    >
      <div class="id">{hovered.id}</div>
      {#if hovered.effort}<div class="pair">effort {hovered.effort}</div>{/if}
      <div class="pair">{yLabel} {(hovered.y ?? 0).toFixed(3)}</div>
      <div class="pair">
        {money(hovered.x ?? 0)}
        {#if hovered.measured !== undefined}
          <span class="from">{hovered.measured ? '· measured' : '· posted price'}</span>
        {/if}
      </div>
    </div>
  {/if}

  <!--
    The legend names the colours of the unfiltered field. Once a line is drawn
    none of them are on screen, and it sits exactly where the top mode labels
    land -- so it goes.
  -->
  <div class="legend mono" hidden={lines.length > 0}>
    <span><i class="sw accent"></i>current #1</span>
    <span><i class="sw reach"></i>reachable</span>
    <span><i class="sw faint"></i>measured</span>
  </div>
</figure>

<style>
  .field {
    position: relative;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    overflow: hidden;
  }
  canvas {
    display: block;
    cursor: crosshair;
  }
  .tip {
    position: absolute;
    pointer-events: none;
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    padding: 0.35rem 0.5rem;
    font-size: 0.72rem;
    max-width: 230px;
  }
  .tip .id {
    color: var(--ink);
    overflow-wrap: anywhere;
  }
  .tip .pair {
    color: var(--muted);
  }
  .tip .from {
    opacity: 0.75;
  }
  .legend {
    position: absolute;
    right: 0.6rem;
    top: 0.5rem;
    display: flex;
    gap: 0.75rem;
    font-size: 0.68rem;
    color: var(--muted);
  }
  /* `display: flex` above beats the `hidden` attribute's own `display: none`,
     which is a quiet way to ship a control that cannot be hidden. */
  .legend[hidden] {
    display: none;
  }
  .sw {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    margin-right: 0.25rem;
  }
  .sw.accent {
    background: var(--accent);
  }
  .sw.reach {
    background: var(--reach);
  }
  .sw.faint {
    background: var(--muted);
    opacity: 0.4;
  }
  @media (max-width: 700px) {
    .legend {
      display: none;
    }
  }
</style>
