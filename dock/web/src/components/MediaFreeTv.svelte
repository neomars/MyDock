<script lang="ts">
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import Keyboard from '@lucide/svelte/icons/keyboard'
  import Tv from '@lucide/svelte/icons/tv'
  import { audioLine, cornerText, freetvStateText } from '../lib/media'
  import { media } from '../lib/media.svelte'
  import type { PlayerSnapshot } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'

  // Free TV est ouvert dans une fenêtre de Chromium sur l'écran du Pi. Cette page est ce que montre le dock quand il est
  // devant (au démarrage, ou après un retour au dock) : Free TV reste ouvert derrière, prêt à revenir.
  // Un toucher dans Free TV navigue dans sa page : pour revenir ici, le bouton du Pi, Select + Start sur une manette, ou un
  // appui long dans un coin de l'écran.
  let { player, onMenu }: { player: PlayerSnapshot; onMenu: () => void } = $props()

  const starting = $derived(player.state === 'starting')
  const audio = $derived(audioLine(player.audio))
  const corner = $derived(cornerText(sock.dock?.media?.freetv_corner))
  // Sans clavier à l'écran sur le Pi (wvkbd), le bouton « Clavier » le dit au lieu de ne rien faire.
  const noKeyboard = $derived(player.helper?.keyboard === false)

  /** Revient à Free TV avec son clavier à l'écran : Free TV passe devant, puis le clavier s'y affiche (la première connexion). */
  async function withKeyboard(): Promise<void> {
    if (!(await media.front('video'))) return
    await media.keyboard(true)
  }
</script>

<section class="free card" data-testid="freetv-now" data-state={player.state} data-front={player.front}>
  <div class="top">
    <div class="now">
      <h2 data-testid="freetv-title"><Tv size={44} strokeWidth={1.6} aria-hidden="true" />Free TV ouvert</h2>
      <p class="meta">
        <span class="state" class:wait={starting} data-testid="freetv-state">{freetvStateText(player)}</span>
        {#if audio}<span class="audio" class:good={audio.level === 'good'} class:warn={audio.level === 'warn'} class:dim={audio.level === 'dim'} data-testid="freetv-audio" data-level={audio.level}>{audio.text}</span>{/if}
      </p>
    </div>
    <button class="btn" data-testid="media-other" onclick={onMenu}><ArrowLeft size={30} aria-hidden="true" />Menu</button>
  </div>

  <div class="actions">
    <button class="btn primary big" data-testid="freetv-back" disabled={starting} onclick={() => media.front('video')}>
      <span>Revenir à Free&nbsp;TV</span>
    </button>
    <button class="btn big" data-testid="freetv-keyboard" disabled={starting || noKeyboard} aria-pressed={player.keyboard === true} onclick={withKeyboard}>
      <Keyboard size={40} aria-hidden="true" /><span>Clavier</span>
    </button>
    <button class="btn danger big" data-testid="freetv-close" onclick={() => media.control('stop')}>
      <span>Fermer Free&nbsp;TV</span>
    </button>
  </div>

  {#if noKeyboard}
    <p class="warn small" data-testid="freetv-no-keyboard">Pas de clavier à l’écran : sudo apt install wvkbd (ou un clavier USB).</p>
  {:else}
    <p class="dim small" data-testid="freetv-keyboard-help">« Clavier » : pour te connecter la première fois.</p>
  {/if}
  <p class="dim small" data-testid="freetv-howto">
    Retour au dock : bouton du Pi, Select + Start, ou appui long <strong>{corner}</strong>. Un toucher navigue dans Free&nbsp;TV.
  </p>
</section>

<style>
  .free {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
  }
  .now h2 {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: var(--fs-large);
    font-weight: 700;
    line-height: 1.1;
    margin: 0;
    color: var(--text);
    letter-spacing: 0;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 18px;
    margin: 4px 0 0;
    font-size: var(--fs-small);
    color: var(--text-2);
  }
  .state.wait {
    color: var(--warn);
    animation: pulse 1.4s ease-in-out infinite;
  }
  .actions {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
  }
  .big {
    min-height: 100px;
    flex-direction: column;
    gap: 4px;
    padding: 8px 8px;
    text-align: center;
    white-space: normal;
    line-height: 1.1;
    min-width: 0;
  }
  .btn[aria-pressed='true'] {
    border-color: var(--accent);
    background: var(--accent-soft, var(--surface-3));
  }
  .small {
    font-size: var(--fs-cap);
    line-height: 1.25;
    margin: 0;
  }
  .audio {
    font-weight: 600;
    color: var(--text-3);
  }
  .audio.good {
    color: var(--good);
  }
  .audio.warn,
  .warn {
    color: var(--warn);
  }
  @keyframes pulse {
    50% {
      opacity: 0.45;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .state.wait {
      animation: none;
    }
  }
</style>
