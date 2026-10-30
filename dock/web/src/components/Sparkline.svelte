<script lang="ts">
  import { curve, type Tone } from '../lib/markets'

  let {
    values,
    tone = 'flat',
    width = 112,
    height = 44,
    label = 'Évolution récente',
  }: { values: number[]; tone?: Tone; width?: number; height?: number; label?: string } = $props()

  const geo = $derived(curve(values, width, height))
</script>

<!-- Un SVG de quelques dizaines de points : aucun filtre, aucune ombre (60 i/s sur le Pi). -->
{#if geo}
  <svg class="spark {tone}" viewBox="0 0 {width} {height}" {width} {height} role="img" aria-label={label} data-testid="spark">
    <polygon class="area" points={geo.area} />
    <polyline class="line" points={geo.line} />
    <circle class="dot" cx={geo.last[0]} cy={geo.last[1]} r="3.2" />
  </svg>
{:else}
  <span class="none" style:width="{width}px" style:height="{height}px" aria-hidden="true"></span>
{/if}

<style>
  .spark {
    display: block;
    flex: none;
    --c: var(--text-3);
  }
  .spark.up {
    --c: var(--good);
  }
  .spark.down {
    --c: var(--bad);
  }
  .area {
    fill: var(--c);
    fill-opacity: 0.14;
  }
  .line {
    fill: none;
    stroke: var(--c);
    stroke-width: 2.2;
    stroke-linejoin: round;
    stroke-linecap: round;
  }
  .dot {
    fill: var(--c);
  }
  .none {
    display: block;
    flex: none;
  }
</style>
