<script lang="ts">
  import House from '@lucide/svelte/icons/house'
  import Power from '@lucide/svelte/icons/power'
  import { clock } from '../lib/clock.svelte'
  import { formatClock } from '../lib/format'
  import { sock } from '../lib/socket.svelte'
  import MiniPlayer from './MiniPlayer.svelte'
  import StatusDot from './StatusDot.svelte'

  let {
    onOpenMusic,
    showHome = false,
    onHome,
    onPower,
  }: { onOpenMusic: () => void; showHome?: boolean; onHome?: () => void; onPower?: () => void } = $props()

  const state = $derived(sock.link?.state ?? null)
  const label = $derived.by(() => {
    switch (state) {
      case 'connected':
        return sock.host?.hostname ?? sock.dock?.pc.name ?? sock.link?.host ?? 'PC connecté'
      case 'connecting':
        return 'Connexion…'
      case 'not_configured':
        return 'Aucun PC associé'
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
  {#if showHome}
    <!-- Dans une page : retour au menu principal (le balayage vers la droite fait la même chose). -->
    <button class="home" data-testid="home-back" aria-label="Retour à l'accueil" onclick={onHome}>
      <House size={44} aria-hidden="true" />
    </button>
  {/if}
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
  <!-- L'alimentation (verrouiller, redémarrer, éteindre, réveiller le PC) : un petit bouton, plus un gros bouton de l'accueil. -->
  {#if onPower}
    <button class="power" data-testid="topbar-power" aria-label="Alimentation" onclick={onPower}>
      <Power size={30} aria-hidden="true" />
    </button>
  {/if}
</header>

<style>
  .top {
    display: flex;
    align-items: center;
    gap: 16px;
    min-width: 0;
    height: var(--topbar);
    padding: 0 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }
  .home {
    display: grid;
    place-items: center;
    flex: none;
    width: var(--tap);
    height: var(--tap);
    margin-left: -8px;
    border-radius: 16px;
    background: var(--surface-3);
    color: var(--text);
  }
  .home:active {
    background: var(--line);
    transform: scale(0.96);
  }
  .who {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 0 1 auto;
    min-width: 0;
    max-width: 38%;
  }
  .name {
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .who .chip {
    font-size: calc(12px * var(--ts));
    padding: 2px 10px;
  }
  .mini {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    justify-content: flex-end;
    overflow: hidden;
  }
  .clock {
    flex: none;
    font-size: calc(22px * var(--ts));
    font-weight: 600;
  }
  /* Petit : on ne veut pas l'effleurer en visant l'heure, mais il reste franchement touchable (56 px). */
  .power {
    display: grid;
    place-items: center;
    flex: none;
    width: 56px;
    height: 56px;
    margin-right: -6px;
    border-radius: 14px;
    background: var(--surface-3);
    color: var(--text-2);
  }
  .power:active {
    background: var(--line);
    transform: scale(0.96);
  }
</style>
