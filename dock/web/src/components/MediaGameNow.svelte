<script lang="ts">
  import Gamepad2 from '@lucide/svelte/icons/gamepad-2'
  import { onMount } from 'svelte'
  import { gameStateText } from '../lib/retro'
  import { retro } from '../lib/retro.svelte'
  import type { PlayerSnapshot } from '../lib/protocol'

  // Un jeu occupe l'écran : MAME est devant, cette page n'est vue qu'un instant (le temps qu'il s'ouvre) ou si MAME est derrière.
  // Aucun bouton : toucher l'écran quitte le jeu, comme le bouton physique ou Select + Start tenus deux secondes.
  let { player }: { player: PlayerSnapshot } = $props()

  // Le toucher qui vient de lancer le jeu n'est pas une demande de le quitter : on laisse passer un instant.
  let armed = $state(false)
  onMount(() => {
    const timer = setTimeout(() => (armed = true), 1_800)
    return () => clearTimeout(timer)
  })
</script>

<button class="game card" data-testid="game-now" data-state={player.state} disabled={!armed} onclick={() => retro.quit()}>
  <Gamepad2 size={96} strokeWidth={1.4} aria-hidden="true" />
  <strong class="title" data-testid="game-now-title">{player.title}</strong>
  {#if player.subtitle}<span class="dim sub">{player.subtitle}</span>{/if}
  <span class="state" class:wait={player.state === 'starting'} data-testid="game-now-state">{gameStateText(player)}</span>
  <span class="dim hint" data-testid="game-now-hint">Touche l’écran pour quitter le jeu.</span>
</button>

<style>
  .game {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    width: 100%;
    min-height: 420px;
    text-align: center;
    border-color: var(--accent);
    color: var(--text);
  }
  .game:disabled {
    opacity: 1;
  }
  .title {
    font-size: var(--fs-large);
    line-height: 1.15;
  }
  .sub {
    font-size: var(--fs-small);
  }
  .state {
    font-size: var(--fs-small);
    font-weight: 600;
    color: var(--accent);
  }
  .state.wait {
    animation: pulse 1.4s ease-in-out infinite;
  }
  .hint {
    font-size: var(--fs-cap);
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
