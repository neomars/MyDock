<script lang="ts">
  import { axisLabel, closes, curve, formatPrice, type Tone } from '../lib/markets'
  import type { MarketRange } from '../lib/protocol'

  let {
    points,
    range,
    currency,
    tone = 'flat',
  }: {
    points: [number, number][]
    range: MarketRange
    currency: string | null
    tone?: Tone
  } = $props()

  const WIDTH = 880
  const HEIGHT = 200
  const geo = $derived(curve(closes(points), WIDTH, HEIGHT, 6))
  const first = $derived(points[0]?.[0])
  const last = $derived(points[points.length - 1]?.[0])
  const middle = $derived(first !== undefined && last !== undefined ? (first + last) / 2 : undefined)
  const level = (fraction: number): number =>
    geo ? geo.min + (geo.max - geo.min) * fraction : 0
</script>

<div class="chart {tone}" data-testid="price-chart">
  {#if geo}
    <div class="plot">
      <svg viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="none" role="img" aria-label="Cours sur la période">
        <line class="grid" x1="0" y1="0.5" x2={WIDTH} y2="0.5" />
        <line class="grid" x1="0" y1={HEIGHT / 2} x2={WIDTH} y2={HEIGHT / 2} />
        <line class="grid" x1="0" y1={HEIGHT - 0.5} x2={WIDTH} y2={HEIGHT - 0.5} />
        <polygon class="area" points={geo.area} />
        <polyline class="line" points={geo.line} />
      </svg>
      <ul class="levels" aria-hidden="true">
        <li data-testid="chart-max">{formatPrice(level(1), currency)}</li>
        <li>{formatPrice(level(0.5), currency)}</li>
        <li data-testid="chart-min">{formatPrice(level(0), currency)}</li>
      </ul>
    </div>
    <div class="times" aria-hidden="true">
      <span>{first !== undefined ? axisLabel(first, range) : ''}</span>
      <span>{middle !== undefined ? axisLabel(middle, range) : ''}</span>
      <span>{last !== undefined ? axisLabel(last, range) : ''}</span>
    </div>
  {:else}
    <p class="muted none" data-testid="chart-none">Pas assez de points sur cette période.</p>
  {/if}
</div>

<style>
  .chart {
    --c: var(--text-2);
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-height: 0;
    height: 100%;
  }
  .chart.up {
    --c: var(--good);
  }
  .chart.down {
    --c: var(--bad);
  }
  .plot {
    position: relative;
    flex: 1 1 0;
    min-height: 0;
  }
  svg {
    display: block;
    width: 100%;
    height: 100%;
  }
  .grid {
    stroke: var(--line);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .area {
    fill: var(--c);
    fill-opacity: 0.12;
  }
  .line {
    fill: none;
    stroke: var(--c);
    stroke-width: 3;
    stroke-linejoin: round;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
  .levels {
    position: absolute;
    inset: 0 auto 0 8px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    margin: 0;
    padding: 2px 0;
    list-style: none;
    font-size: var(--fs-cap);
    color: var(--text-3);
    pointer-events: none;
  }
  .levels li {
    background: color-mix(in srgb, var(--bg) 70%, transparent);
    padding: 0 4px;
    border-radius: 4px;
  }
  .times {
    display: flex;
    justify-content: space-between;
    font-size: var(--fs-cap);
    color: var(--text-3);
  }
  .none {
    margin: auto;
  }
</style>
