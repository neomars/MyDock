<script lang="ts">
  import type { LinkState } from '../lib/protocol'

  let { state }: { state: LinkState | null } = $props()

  const tone = $derived(
    state === 'connected'
      ? 'good'
      : state === 'connecting' || state === 'host_up_agent_down'
        ? 'warn'
        : state === 'not_configured' || state === null
          ? 'dim'
          : 'bad',
  )
</script>

<span class="dot {tone}" aria-hidden="true"></span>

<style>
  .dot {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    flex: none;
    background: var(--text-3);
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
