<script lang="ts">
  import { clock } from '../lib/clock.svelte'
  import { formatClock } from '../lib/format'
  import { sock } from '../lib/socket.svelte'
  import MiniPlayer from './MiniPlayer.svelte'
  import StatusDot from './StatusDot.svelte'

  let { onOpenMusic }: { onOpenMusic: () => void } = $props()

  const state = $derived(sock.link?.state ?? null)
  const label = $derived.by(() => {
    switch (state) {
      case 'connected':
        return sock.host?.hostname ?? sock.dock?.pc.name ?? sock.link?.host ?? 'PC connecté'
      case 'connecting':
        return 'Connexion…'
      case 'not_configured':
        return 'Aucun PC configuré'
      case 'host_up_agent_down':
        return 'Agent arrêté'
      case 'auth_failed':
        return 'Connexion refusée'
      case 'protocol_mismatch':
        return 'Versions incompatibles'
      case 'unreachable':
        return 'PC hors ligne'
      default:
        return 'MyDock'
    }
  })
</script>

<header class="top">
  <div class="who">
    <StatusDot {state} />
    <span class="name" data-testid="pc-label">{label}</span>
    {#if sock.link?.agent?.demo}<span class="chip accent" data-testid="demo-chip">DÉMO</span>{/if}
  </div>
  <!-- Mini-lecteur : toujours accessible, quelle que soit la page (absent quand le PC l'est). -->
  <div class="mini">
    {#if sock.connected}<MiniPlayer onOpen={onOpenMusic} />{/if}
  </div>
  <time class="clock" data-testid="clock">{formatClock(clock.now)}</time>
</header>

<style>
  .top {
    display: grid;
    grid-template-columns: 1fr minmax(0, 2fr) 1fr;
    align-items: center;
    height: var(--topbar);
    padding: 0 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }
  .who {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }
  .name {
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .mini {
    min-width: 0;
    display: flex;
    justify-content: center;
  }
  .clock {
    justify-self: end;
    font-size: 28px;
    font-weight: 600;
  }
</style>
