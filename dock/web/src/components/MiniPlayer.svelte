<script lang="ts">
  import MusicIcon from '@lucide/svelte/icons/music'
  import Pause from '@lucide/svelte/icons/pause'
  import Play from '@lucide/svelte/icons/play'
  import SkipBack from '@lucide/svelte/icons/skip-back'
  import SkipForward from '@lucide/svelte/icons/skip-forward'
  import { music } from '../lib/music.svelte'

  let { onOpen }: { onOpen: () => void } = $props()

  const player = $derived(music.active)
  const cover = $derived(player?.art ? music.arts[player.art] : undefined)
  const playing = $derived(player?.status === 'playing')

  $effect(() => {
    void music.ensureArt(player?.art ?? null)
  })
</script>

{#if player}
  <div class="mini" data-testid="mini-player">
    <button class="info" aria-label="Ouvrir le lecteur : {player.title ?? player.name}" data-testid="mini-open" onclick={onOpen}>
      <span class="cover">
        {#if cover}<img src={cover} alt="" />{:else}<MusicIcon size={32} aria-hidden="true" />{/if}
      </span>
      <span class="text">
        <strong data-testid="mini-title">{player.title ?? 'Sans titre'}</strong>
        <small>{player.artist ?? player.name}</small>
      </span>
    </button>
    <button class="ctl" aria-label="Morceau précédent" data-testid="mini-prev" disabled={!player.can.previous} onclick={music.previous}>
      <SkipBack size={34} aria-hidden="true" />
    </button>
    <button class="ctl main" aria-label={playing ? 'Pause' : 'Lecture'} data-testid="mini-play" onclick={music.playPause}>
      {#if playing}<Pause size={36} aria-hidden="true" />{:else}<Play size={36} aria-hidden="true" />{/if}
    </button>
    <button class="ctl" aria-label="Morceau suivant" data-testid="mini-next" disabled={!player.can.next} onclick={music.next}>
      <SkipForward size={34} aria-hidden="true" />
    </button>
  </div>
{/if}

<style>
  .mini {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    justify-self: flex-end;
    width: 100%;
    max-width: 640px;
  }
  .info {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1 1 0;
    min-width: 0;
    height: var(--tap);
    padding: 0 6px 0 0;
    border-radius: 12px;
  }
  .info:active {
    background: var(--surface-2);
  }
  .cover {
    display: grid;
    place-items: center;
    width: 64px;
    height: 64px;
    flex: none;
    border-radius: 10px;
    background: var(--surface-3);
    color: var(--text-3);
    overflow: hidden;
  }
  .cover img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .text {
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.2;
  }
  .text strong {
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .text small {
    display: none; /* l'artiste se lit sur la page Spotify : la barre du haut n'a de place que pour le titre */
  }
  .ctl {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    flex: none;
    border-radius: 14px;
    color: var(--text);
  }
  .ctl:active:not(:disabled) {
    background: var(--surface-3);
  }
  .ctl:disabled {
    opacity: 0.3;
  }
  .ctl.main {
    background: var(--accent);
    color: var(--accent-ink);
    border-radius: 50%;
  }
  .ctl.main:active {
    background: #8ccbff;
  }
</style>
