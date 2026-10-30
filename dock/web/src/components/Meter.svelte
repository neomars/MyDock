<script lang="ts">
  import { toneFor } from '../lib/system'

  let { percent, label = '' }: { percent: number; label?: string } = $props()
  const clamped = $derived(Math.max(0, Math.min(100, percent)))
</script>

<div class="meter" role="progressbar" aria-label={label} aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(clamped)}>
  <div class="fill {toneFor(clamped)}" style:width="{clamped}%"></div>
</div>

<style>
  .meter {
    height: 10px;
    border-radius: 999px;
    background: var(--surface-3);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    border-radius: 999px;
  }
  .good {
    background: var(--good);
  }
  .warn {
    background: var(--warn);
  }
  .bad {
    background: var(--bad);
  }
</style>
