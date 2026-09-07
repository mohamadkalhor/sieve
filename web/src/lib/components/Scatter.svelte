<script lang="ts">
  /**
   * The Field: one canvas, every measured model, cost on a log x-axis.
   *
   * 644 points have to stay smooth, so the scene is drawn once into an
   * offscreen canvas and only the hover marker is repainted per frame. Hit
   * testing goes through a coarse grid rather than a scan of every point.
   */
  import { scaleLinear, scaleLog } from 'd3-scale';

  export interface Point {
    id: string;
    x: number | null; // cost per task, USD
    y: number | null; // axis value, 0..1
    reachable: boolean;
    primary: boolean;
  }

  interface Props {
    points: Point[];
    xLabel?: string;
    yLabel?: string;
    height?: number;
    onselect?: (id: string) => void;
  }
  let { points, xLabel = 'cost per task (USD)', yLabel = 'axis', height = 420, onselect }: Props =
    $props();

  let host: HTMLElement | undefined = $state();
  let canvas: HTMLCanvasElement | undefined = $state();
  let width = $state(720);
  let hovered: Point | null = $state(null);
  let pointer = $state({ x: 0, y: 0 });

  const PAD = { top: 18, right: 18, bottom: 34, left: 44 };
  const CELL = 24;

  const plotted = $derived(points.filter((p) => p.x !== null && p.y !== null && (p.x as number) > 0));

  const scales = $derived.by(() => {
    const xs = plotted.map((p) => p.x as number);
    const ys = plotted.map((p) => p.y as number);
    const x = scaleLog()
      .domain([Math.min(...xs, 0.0001) * 0.8, Math.max(...xs, 0.001) * 1.2])
      .range([PAD.left, width - PAD.right])
      .clamp(true);
    const y = scaleLinear()
      .domain([Math.min(0, ...ys), Math.max(1, ...ys)])
      .range([height - PAD.bottom, PAD.top])
      .clamp(true);
    return { x, y };
  });

  const placed = $derived(
    plotted.map((point) => ({
      point,
      px: scales.x(point.x as number),
      py: scales.y(point.y as number)
    }))
  );

  /** Coarse grid: a hover looks at one cell, not 644 points. */
  const index = $derived.by(() => {
    const grid = new Map<string, typeof placed>();
    for (const item of placed) {
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
    // A log axis over four orders of magnitude returns far more ticks than
    // fit. Label the decades, and drop any that would collide with the last.
    let lastLabelEnd = -Infinity;
    for (const tick of scales.x.ticks(4)) {
      const decade = Math.log10(tick);
      if (Math.abs(decade - Math.round(decade)) > 1e-9) continue;
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
        Number(a.point.primary) - Number(b.point.primary) ||
        Number(a.point.reachable) - Number(b.point.reachable)
    );
    for (const { point, px, py } of order) {
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
      if (point.primary) {
        ctx.strokeStyle = colour('--accent');
        ctx.globalAlpha = 0.5;
        ctx.beginPath();
        ctx.arc(px, py, 8, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
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
    void [placed, width, height];
    draw();
  });
</script>

<figure
  class="field"
  bind:this={host}
  style:height={`${height}px`}
  aria-label={`${plotted.length} models plotted, ${yLabel} against ${xLabel}`}
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
      <div class="pair">{yLabel} {(hovered.y ?? 0).toFixed(3)}</div>
      <div class="pair">cost {money(hovered.x ?? 0)}</div>
    </div>
  {/if}

  <div class="legend mono">
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
  .legend {
    position: absolute;
    right: 0.6rem;
    top: 0.5rem;
    display: flex;
    gap: 0.75rem;
    font-size: 0.68rem;
    color: var(--muted);
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
