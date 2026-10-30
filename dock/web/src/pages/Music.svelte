<script lang="ts">
  import MusicIcon from '@lucide/svelte/icons/music'
  import Pause from '@lucide/svelte/icons/pause'
  import Play from '@lucide/svelte/icons/play'
  import SkipBack from '@lucide/svelte/icons/skip-back'
  import SkipForward from '@lucide/svelte/icons/skip-forward'
  import { onMount } from 'svelte'
  import Progress from '../components/Progress.svelte'
  import VolumeBar from '../components/VolumeBar.svelte'
  import { audio } from '../lib/audio.svelte'
  import { music } from '../lib/music.svelte'
  import { subtitle } from '../lib/music'
  import type { PlayerInfo } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'

  const player = $derived(music.active)
  const players = $derived(music.data?.players ?? [])
  const cover = $derived(player?.art ? music.arts[player.art] : undefined)
  const playing = $derived(player?.status === 'playing')

  // La position avance toute seule : on la recalcule 4 fois par seconde, seulement en lecture.
  let now = $state(performance.now())
  const position = $derived(player ? music.position(player, now) : null)
  $effect(() => {
    if (!playing) return
    now = performance.now()
    const timer = setInterval(() => (now = performance.now()), 250)
    return () => clearInterval(timer)
  })

  // Un instantané en cache peut dater : on se recale à l'ouverture, puis toutes les 10 s.
  onMount(() => {
    void music.resync()
    const timer = setInterval(() => void music.resync(), music.resyncMs)
    return () => clearInterval(timer)
  })

  $effect(() => {
    void music.ensureArt(player?.art ?? null)
  })

  // Le volume réglé ici est celui de la sortie du PC : la page suit aussi le thème « audio ».
  onMount(() => sock.subscribe(['audio']))
  const output = $derived(audio.output)

  /** Deux instances du même navigateur portent le même nom : on les numérote. */
  function chipLabel(p: PlayerInfo): string {
    const same = players.filter((q) => q.name === p.name)
    return same.length > 1 ? `${p.name} ${same.indexOf(p) + 1}` : p.name
  }
</script>

{#if music.unavailable}
  <section class="empty" data-testid="music-unavailable">
    <MusicIcon size={64} aria-hidden="true" />
    <h1>Musique indisponible</h1>
    <p class="muted">{music.unavailable}</p>
  </section>
{:else if !player}
  <section class="empty" data-testid="music-empty">
    <MusicIcon size={64} aria-hidden="true" />
    <h1>Aucun lecteur ouvert</h1>
    <p class="muted">Lance Spotify, VLC ou une vidéo dans ton navigateur : le dock le détecte tout seul.</p>
  </section>
{:else}
  <div class="player" data-testid="music-player">
    <div class="cover" data-testid="music-cover">
      {#if cover}
        <img src={cover} alt="Pochette de {player.title ?? 'ce morceau'}" data-testid="music-cover-img" />
      {:else}
        <MusicIcon size={96} aria-hidden="true" />
      {/if}
    </div>

    <div class="side">
      <div class="meta">
        <h1 class="title" data-testid="music-title">{player.title ?? 'Sans titre'}</h1>
        <p class="artist" data-testid="music-artist">{subtitle(player) || player.name}</p>
        <p class="source dim">{player.name}{playing ? ' · lecture' : player.status === 'paused' ? ' · pause' : ''}</p>
      </div>

      <Progress
        {position}
        length={player.length_ms}
        seekable={player.can.seek}
        onSeek={(ms) => void music.seek(ms)}
      />

      <div class="transport">
        <button class="round" aria-label="Morceau précédent" data-testid="music-prev" disabled={!player.can.previous} onclick={music.previous}>
          <SkipBack size={34} aria-hidden="true" />
        </button>
        <button class="round main" aria-label={playing ? 'Pause' : 'Lecture'} data-testid="music-play" onclick={music.playPause}>
          {#if playing}<Pause size={44} aria-hidden="true" />{:else}<Play size={44} aria-hidden="true" />{/if}
        </button>
        <button class="round" aria-label="Morceau suivant" data-testid="music-next" disabled={!player.can.next} onclick={music.next}>
          <SkipForward size={34} aria-hidden="true" />
        </button>
      </div>

      {#if output}
        <div class="volume" data-testid="music-volume">
          <VolumeBar kind="sink" id={output.id} name={output.label} volume={output.volume} muted={output.muted} testid="music-vol" />
        </div>
      {/if}

      {#if players.length > 1}
        <div class="chips" role="tablist" aria-label="Lecteurs" data-testid="music-players">
          {#each players as p (p.id)}
            <button
              class="btn chip-btn"
              role="tab"
              aria-selected={p.id === player.id}
              data-testid="music-select-{p.id}"
              onclick={() => void music.select(p.id)}
            >
              {#if p.status === 'playing'}<Play size={18} aria-hidden="true" />{/if}
              {chipLabel(p)}
            </button>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .player {
    display: grid;
    grid-template-columns: 300px minmax(0, 1fr);
    gap: 28px;
    height: 100%;
    align-items: center;
  }
  .cover {
    display: grid;
    place-items: center;
    width: 300px;
    height: 300px;
    border-radius: 22px;
    background: linear-gradient(135deg, var(--surface-3), var(--surface));
    color: var(--text-3);
    overflow: hidden;
    border: 1px solid var(--line);
  }
  .cover img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .side {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .meta {
    min-height: 108px;
  }
  .title {
    font-size: 34px;
    font-weight: 700;
    line-height: 1.12;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .artist {
    margin-top: 6px;
    font-size: var(--fs-large);
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .source {
    margin-top: 2px;
    font-size: var(--fs-cap);
  }
  .transport {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 34px;
    margin-top: 2px;
  }
  .round {
    display: grid;
    place-items: center;
    width: 76px;
    height: 76px;
    border-radius: 50%;
    background: var(--surface-3);
  }
  .round:active:not(:disabled) {
    background: var(--line);
    transform: scale(0.95);
  }
  .round:disabled {
    opacity: 0.3;
  }
  .round.main {
    width: 96px;
    height: 96px;
    background: var(--accent);
    color: var(--accent-ink);
  }
  .round.main:active {
    background: #8ccbff;
  }
  .volume {
    margin-top: 2px;
  }
  .chips {
    display: flex;
    gap: 10px;
    justify-content: center;
    margin-top: 2px;
  }
  .chip-btn {
    min-height: 52px;
    padding: 0 20px;
    font-size: var(--fs-small);
  }
  .chip-btn[aria-selected='true'] {
    background: var(--accent-soft);
    color: var(--accent);
    box-shadow: inset 0 0 0 2px var(--accent);
  }
  .empty {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    text-align: center;
    color: var(--text-3);
  }
  .empty h1 {
    font-size: var(--fs-title);
    color: var(--text);
  }
  .empty p {
    max-width: 560px;
  }
</style>
