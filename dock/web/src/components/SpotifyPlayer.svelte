<script lang="ts">
  import MonitorSpeaker from '@lucide/svelte/icons/monitor-speaker'
  import MusicIcon from '@lucide/svelte/icons/music'
  import Pause from '@lucide/svelte/icons/pause'
  import Play from '@lucide/svelte/icons/play'
  import Repeat from '@lucide/svelte/icons/repeat'
  import Repeat1 from '@lucide/svelte/icons/repeat-1'
  import Shuffle from '@lucide/svelte/icons/shuffle'
  import SkipBack from '@lucide/svelte/icons/skip-back'
  import SkipForward from '@lucide/svelte/icons/skip-forward'
  import { onMount } from 'svelte'
  import { audio } from '../lib/audio.svelte'
  import { music } from '../lib/music.svelte'
  import { subtitle } from '../lib/music'
  import type { PlayerInfo } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { deviceTitle, loopLabel, nextLoop } from '../lib/spotify'
  import { spotify } from '../lib/spotify.svelte'
  import DeviceVolume from './DeviceVolume.svelte'
  import Progress from './Progress.svelte'
  import SpotifyIcon from './SpotifyIcon.svelte'
  import VolumeBar from './VolumeBar.svelte'

  const player = $derived(music.active)
  const players = $derived(music.data?.players ?? [])
  const cover = $derived(player?.art ? music.arts[player.art] : undefined)
  const playing = $derived(player?.status === 'playing')
  // Aléatoire et répétition : seulement si le lecteur sait les régler (Spotify, VLC… ; pas un onglet de navigateur).
  const canShuffle = $derived(typeof player?.shuffle === 'boolean')
  const canLoop = $derived(typeof player?.loop === 'string')

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

  const installed = $derived(spotify.status?.installed)

  // La musique sort sur une enceinte, un téléphone… (et non sur ce PC) : le lecteur le dit, et son volume est celui de
  // cet appareil. Sur ce PC, c'est le volume de la sortie du PC, comme avant.
  const elsewhere = $derived(player?.id === 'spotify' && spotify.output !== null && !spotify.output.this_pc ? spotify.output : null)
</script>

{#if music.unavailable}
  <section class="empty" data-testid="music-unavailable">
    <MusicIcon size={84} aria-hidden="true" />
    <h1>Musique indisponible</h1>
    <p class="muted">{music.unavailable}</p>
  </section>
{:else if !player}
  <section class="empty" data-testid="music-empty">
    <SpotifyIcon size={84} strokeWidth={1.5} aria-hidden="true" />
    <h1>Spotify n’est pas ouvert</h1>
    {#if spotify.statusError?.code === 'unknown_action'}
      <p class="muted" data-testid="sp-agent-old">{spotify.statusError.message}</p>
    {:else if installed === false}
      <p class="muted" data-testid="sp-not-installed">
        Spotify n’est pas installé sur ce PC (ni en paquet .deb, ni en Snap, ni en Flatpak). Installe-le, ou ouvre un autre lecteur : le dock le détecte tout seul.
      </p>
    {:else}
      <p class="muted">Lance-le d’ici, ou ouvre un autre lecteur : le dock le détecte tout seul.</p>
      <button class="launch" data-testid="sp-launch" disabled={spotify.launching} onclick={() => void spotify.launchApp()}>
        <SpotifyIcon size={44} aria-hidden="true" />
        {spotify.launching ? 'Lancement de Spotify…' : 'Lancer Spotify'}
      </button>
    {/if}
  </section>
{:else}
  <div class="player" data-testid="music-player">
    <div class="cover" data-testid="music-cover">
      {#if cover}
        <img src={cover} alt="Pochette de {player.title ?? 'ce morceau'}" data-testid="music-cover-img" />
      {:else}
        <MusicIcon size={72} aria-hidden="true" />
      {/if}
    </div>

    <div class="meta">
      <h1 class="title" data-testid="music-title">{player.title ?? 'Sans titre'}</h1>
      <p class="artist" data-testid="music-artist">{subtitle(player) || player.name}</p>
      {#if elsewhere}
        <button class="output" data-testid="music-output" onclick={() => spotify.go('devices')}>
          <MonitorSpeaker size={30} aria-hidden="true" />
          <span>{deviceTitle(elsewhere)}</span>
        </button>
      {/if}
      {#if player.id !== 'spotify'}
        <p class="source dim" data-testid="music-source">{player.name}{playing ? ' · lecture' : player.status === 'paused' ? ' · pause' : ''}</p>
      {/if}
    </div>

    <div class="prog">
      <Progress {position} length={player.length_ms} seekable={player.can.seek} onSeek={(ms) => void music.seek(ms)} />
    </div>

    <div class="transport">
      {#if canShuffle}
        <button
          class="round small"
          class:on={player.shuffle === true}
          aria-label={player.shuffle ? 'Lecture aléatoire : activée (touche pour la couper)' : 'Lecture aléatoire : désactivée'}
          aria-pressed={player.shuffle === true}
          data-testid="music-shuffle"
          onclick={() => void music.shuffle(!player.shuffle)}
        >
          <Shuffle size={36} aria-hidden="true" />
        </button>
      {/if}
      <button class="round" aria-label="Morceau précédent" data-testid="music-prev" disabled={!player.can.previous} onclick={music.previous}>
        <SkipBack size={44} aria-hidden="true" />
      </button>
      <button class="round main" aria-label={playing ? 'Pause' : 'Lecture'} data-testid="music-play" onclick={music.playPause}>
        {#if playing}<Pause size={54} aria-hidden="true" />{:else}<Play size={54} aria-hidden="true" />{/if}
      </button>
      <button class="round" aria-label="Morceau suivant" data-testid="music-next" disabled={!player.can.next} onclick={music.next}>
        <SkipForward size={44} aria-hidden="true" />
      </button>
      {#if canLoop}
        <button
          class="round small"
          class:on={player.loop !== 'none'}
          aria-label={loopLabel(player.loop)}
          aria-pressed={player.loop !== 'none'}
          data-loop={player.loop}
          data-testid="music-repeat"
          onclick={() => void music.repeat(nextLoop(player.loop))}
        >
          {#if player.loop === 'track'}<Repeat1 size={36} aria-hidden="true" />{:else}<Repeat size={36} aria-hidden="true" />{/if}
        </button>
      {/if}
    </div>

    {#if elsewhere && elsewhere.can_volume}
      <div class="volume" data-testid="music-volume" data-device={elsewhere.id}>
        <DeviceVolume device={elsewhere} />
      </div>
    {:else if output}
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
            {#if p.status === 'playing'}<Play size={28} aria-hidden="true" />{/if}
            {chipLabel(p)}
          </button>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  /* Pochette et titre en haut, la progression, puis les commandes, le volume et (s'il y en a plusieurs) les lecteurs :
     tout tient dans l'écran sans défiler quand le titre tient sur une ligne. */
  .player {
    display: grid;
    grid-template-columns: 160px minmax(0, 1fr);
    grid-template-areas:
      'cover meta'
      'prog prog'
      'transport transport'
      'volume volume'
      'chips chips';
    gap: 2px 22px;
    align-items: center;
  }
  .cover {
    grid-area: cover;
    display: grid;
    place-items: center;
    width: 160px;
    height: 160px;
    border-radius: 16px;
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
  .meta {
    grid-area: meta;
    min-width: 0;
  }
  .title {
    font-size: var(--fs-title);
    font-weight: 700;
    line-height: 1.1;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .artist {
    margin-top: 4px;
    font-size: var(--fs);
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .source {
    margin-top: 0;
    font-size: var(--fs-cap);
  }
  .output {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    max-width: 100%;
    min-height: 56px;
    margin-top: 6px;
    padding: 0 20px 0 16px;
    border-radius: 999px;
    background: var(--accent-soft);
    color: var(--accent);
    font-size: var(--fs-cap);
    font-weight: 600;
  }
  .output span {
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .output:active {
    filter: brightness(1.2);
  }
  .prog {
    grid-area: prog;
  }
  .transport {
    grid-area: transport;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
  }
  .round {
    display: grid;
    place-items: center;
    flex: none;
    width: 78px;
    height: 78px;
    border-radius: 50%;
    background: var(--surface-3);
  }
  .round.small {
    width: 72px;
    height: 72px;
    background: transparent;
    color: var(--text-2);
  }
  .round.small.on {
    color: var(--accent);
    background: var(--accent-soft);
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
    background: var(--accent);
    filter: brightness(1.15);
  }
  .volume {
    grid-area: volume;
    min-width: 0;
  }
  .chips {
    grid-area: chips;
    display: flex;
    gap: 10px;
    justify-content: center;
  }
  .chip-btn {
    min-height: var(--tap);
    padding: 0 24px;
    font-size: var(--fs-small);
  }
  .chip-btn[aria-selected='true'] {
    background: var(--accent-soft);
    color: var(--accent);
    box-shadow: inset 0 0 0 2px var(--accent);
  }
  .empty {
    min-height: 420px;
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
    max-width: 700px;
    font-size: var(--fs-small);
  }
  .launch {
    display: inline-flex;
    align-items: center;
    gap: 14px;
    min-height: 88px;
    margin-top: 6px;
    padding: 0 40px;
    border-radius: 999px;
    background: var(--accent);
    color: var(--accent-ink);
    font-size: var(--fs-large);
    font-weight: 700;
  }
  .launch:active:not(:disabled) {
    filter: brightness(1.15);
    transform: scale(0.97);
  }
  .launch:disabled {
    opacity: 0.6;
  }
</style>
