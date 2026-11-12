<script lang="ts">
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import FastForward from '@lucide/svelte/icons/fast-forward'
  import MonitorPlay from '@lucide/svelte/icons/monitor-play'
  import Pause from '@lucide/svelte/icons/pause'
  import Play from '@lucide/svelte/icons/play'
  import Rewind from '@lucide/svelte/icons/rewind'
  import Square from '@lucide/svelte/icons/square'
  import { formatPlayTime, progressRatio, SEEK_SECONDS, stateText } from '../lib/media'
  import { media } from '../lib/media.svelte'
  import type { PlayerSnapshot } from '../lib/protocol'
  import Slider from './Slider.svelte'

  // La télécommande : la vidéo joue en plein écran sur VLC ; touche l'écran, elle revient ici. La vidéo continue derrière.
  let { player, onMenu }: { player: PlayerSnapshot; onMenu: () => void } = $props()

  const ratio = $derived(progressRatio(player))
  const paused = $derived(player.state === 'paused')
  // Pas d'avance rapide dans un direct, ni quand le fichier arrive par un tube (smbclient : VLC ne peut pas y revenir en arrière).
  const seekable = $derived(!player.live && player.state !== 'starting' && player.seekable !== false)
</script>

<section class="remote card" data-testid="remote" data-state={player.state} data-front={player.front}>
  <div class="top">
    <div class="now">
      <h2 data-testid="remote-title">{player.title}</h2>
      <p class="meta">
        {#if player.subtitle}<span class="dim" data-testid="remote-subtitle">{player.subtitle}</span>{' · '}{/if}<span class="state" class:wait={player.state === 'starting'} data-testid="remote-state">{stateText(player)}</span>{#if player.seekable === false && !player.live}<span class="dim" data-testid="remote-noseek">{' · '}sans avance rapide</span>{/if}
      </p>
    </div>
    <button class="btn" data-testid="media-other" onclick={onMenu}><ArrowLeft size={30} aria-hidden="true" />Menu</button>
  </div>

  {#if ratio !== null}
    <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(ratio * 100)} data-testid="remote-progress">
      <span class="fill" style:width="{ratio * 100}%"></span>
    </div>
    <div class="times mono">
      <span data-testid="remote-position">{formatPlayTime(player.position)}</span>
      <span>-{formatPlayTime(player.length !== null && player.position !== null ? player.length - player.position : null)}</span>
    </div>
  {/if}
  <div class="transport">
    <button class="btn" disabled={!seekable} aria-label="Reculer de cinq minutes" data-testid="remote-back5" onclick={() => media.seek(-300)}>
      <Rewind size={32} aria-hidden="true" />5 min
    </button>
    <button class="btn" disabled={!seekable} aria-label="Reculer de {SEEK_SECONDS} secondes" data-testid="remote-back" onclick={() => media.seek(-SEEK_SECONDS)}>
      <Rewind size={32} aria-hidden="true" />{SEEK_SECONDS} s
    </button>
    <button class="btn primary play" aria-label={paused ? 'Reprendre' : 'Pause'} data-testid="remote-toggle" disabled={player.state === 'starting'} onclick={() => media.control('pause')}>
      {#if paused}<Play size={44} aria-hidden="true" />{:else}<Pause size={44} aria-hidden="true" />{/if}
    </button>
    <button class="btn" disabled={!seekable} aria-label="Avancer de {SEEK_SECONDS} secondes" data-testid="remote-fwd" onclick={() => media.seek(SEEK_SECONDS)}>
      {SEEK_SECONDS} s<FastForward size={32} aria-hidden="true" />
    </button>
    <button class="btn" disabled={!seekable} aria-label="Avancer de cinq minutes" data-testid="remote-fwd5" onclick={() => media.seek(300)}>
      5 min<FastForward size={32} aria-hidden="true" />
    </button>
  </div>

  <div class="volume">
    <span class="dim">Volume</span>
    <Slider value={player.volume} label="Volume de la vidéo" valueText="{player.volume} %" testid="remote-volume" onCommit={(v) => media.volume(v)} />
    <span class="mono" data-testid="remote-volume-value">{player.volume} %</span>
  </div>

  <div class="ends">
    <button class="btn" data-testid="remote-video" onclick={() => media.front('video')}>
      <MonitorPlay size={32} aria-hidden="true" />Revenir à la vidéo
    </button>
    <button class="btn danger" data-testid="remote-stop" onclick={() => media.control('stop')}>
      <Square size={28} aria-hidden="true" />Arrêter
    </button>
  </div>
</section>

<style>
  .remote {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
  }
  .now {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .meta {
    font-size: var(--fs-small);
    color: var(--text-2);
  }
  .now h2 {
    font-size: var(--fs-large);
    font-weight: 700;
    line-height: 1.1;
    overflow-wrap: anywhere;
    margin: 0;
    color: var(--text);
    letter-spacing: 0;
  }
  .bar {
    height: 12px;
    border-radius: 6px;
    background: var(--surface-3);
    overflow: hidden;
  }
  .fill {
    display: block;
    height: 100%;
    background: var(--accent);
  }
  .times {
    display: flex;
    justify-content: space-between;
    color: var(--text-3);
    font-size: var(--fs-cap);
    margin-top: -6px;
  }
  .state.wait {
    color: var(--warn);
  }
  .transport {
    display: grid;
    grid-template-columns: 1fr 1fr 1.3fr 1fr 1fr;
    gap: 10px;
  }
  .transport .btn {
    padding: 0 6px;
    min-height: 76px;
    font-size: var(--fs-cap);
    gap: 4px;
  }
  .transport .play {
    min-height: 76px;
  }
  .volume {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) 5.5ch;
    align-items: center;
    gap: 14px;
  }
  .ends {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
</style>
