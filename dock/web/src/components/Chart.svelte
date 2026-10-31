<script lang="ts">
  import { chartRuns } from '../lib/system'

  interface Series {
    values: (number | null)[]
    color: string
    /** Remplir sous la courbe. */
    fill?: boolean
  }

  let {
    series,
    max = 100,
    label,
    testid = 'chart',
  }: { series: Series[]; max?: number; label: string; testid?: string } = $props()

  const drawn = $derived(series.map((s) => ({ ...s, runs: chartRuns(s.values, max) })))
</script>

<!-- Un seul SVG léger : quelques dizaines de points, aucun filtre ni ombre (60 i/s sur le Pi). -->
<svg class="chart" viewBox="0 0 60 100" preserveAspectRatio="none" role="img" aria-label={label} data-testid={testid}>
  <line class="grid" x1="0" y1="0.5" x2="60" y2="0.5" />
  <line class="grid" x1="0" y1="50" x2="60" y2="50" />
  <line class="grid" x1="0" y1="99.5" x2="60" y2="99.5" />
  {#each drawn as s, i (i)}
    {#each s.runs as run, j (j)}
      {#if s.fill}<polygon points={run.area} fill={s.color} fill-opacity="0.13" />{/if}
      <polyline class="line" points={run.points} stroke={s.color} data-testid="{testid}-line" />
    {/each}
  {/each}
</svg>

<style>
  .chart {
    display: block;
    width: 100%;
    height: 100%;
  }
  .grid {
    stroke: var(--line);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .line {
    fill: none;
    stroke-width: 2.5;
    stroke-linejoin: round;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
</style>
