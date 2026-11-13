<script lang="ts">
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import Folder from '@lucide/svelte/icons/folder'
  import Gamepad2 from '@lucide/svelte/icons/gamepad-2'
  import MonitorPlay from '@lucide/svelte/icons/monitor-play'
  import Tv from '@lucide/svelte/icons/tv'
  import { untrack } from 'svelte'
  import MediaFiles from '../components/MediaFiles.svelte'
  import MediaGameNow from '../components/MediaGameNow.svelte'
  import MediaGames from '../components/MediaGames.svelte'
  import MediaPads from '../components/MediaPads.svelte'
  import MediaRemote from '../components/MediaRemote.svelte'
  import MediaTv from '../components/MediaTv.svelte'
  import MediaUpload from '../components/MediaUpload.svelte'
  import PageHeader from '../components/PageHeader.svelte'
  import { playerActive, splitFailure, type MediaView } from '../lib/media'
  import { media } from '../lib/media.svelte'
  import { gameActive, type RetroView } from '../lib/retro'
  import { sock } from '../lib/socket.svelte'

  // Divertissement : Freetv (les chaînes de la Freebox), Kiosque (ses partages Samba) — lus par VLC sur cet écran — et
  // Retrogame (les jeux d'arcade de MAME). Pendant une vidéo ou un jeu, l'application occupe tout l'écran ; un toucher
  // ramène ici : sur la télécommande pour une vidéo ; pour un jeu, il le quitte et l'on retrouve la liste des jeux.
  type View = MediaView | RetroView | 'remote' | 'game'

  const player = $derived(sock.player)
  const active = $derived(playerActive(player))
  const gaming = $derived(gameActive(player))
  const watching = $derived(active && !gaming) // une vidéo
  let view = $state<View>(untrack(() => (gameActive(sock.player) ? 'game' : playerActive(sock.player) ? 'remote' : 'menu')))

  // La télécommande s'ouvre d'elle-même quand une lecture démarre ou que le dock revient devant la vidéo ; elle se ferme
  // quand la lecture s'arrête (le message d'une lecture en échec reste dans le menu). Un jeu a son propre écran, et quand
  // il s'arrête (toucher, bouton, Select + Start, ou MAME qui se ferme) on revient à la liste des jeux.
  let wasWatching = untrack(() => watching)
  let wasGaming = untrack(() => gaming)
  let wasFront = untrack(() => player?.front)
  $effect(() => {
    const video = watching
    const game = gaming
    const front = player?.front
    untrack(() => {
      if (video && (!wasWatching || (wasFront === 'video' && front === 'dock'))) view = 'remote'
      if (!video && view === 'remote') view = 'menu'
      if (game && !wasGaming) view = 'game'
      if (!game && view === 'game') view = 'games'
      wasWatching = video
      wasGaming = game
      wasFront = front
    })
  })

  const TITLES: Record<View, string> = {
    menu: 'Divertissement',
    tv: 'Freetv',
    files: 'Kiosque',
    remote: 'Lecture en cours',
    games: 'Retrogame',
    pads: 'Manettes',
    upload: 'Envoyer des jeux',
    game: 'En jeu',
  }
  // Le bouton de retour de l'en-tête : les sous-écrans de Retrogame reviennent à la liste des jeux, les autres au menu.
  const BACK: Partial<Record<View, { to: View; label: string }>> = {
    tv: { to: 'menu', label: 'Divertissement' },
    files: { to: 'menu', label: 'Divertissement' },
    games: { to: 'menu', label: 'Divertissement' },
    pads: { to: 'games', label: 'Retrogame' },
    upload: { to: 'games', label: 'Retrogame' },
  }
  const back = $derived(BACK[view])
  const failed = $derived(player?.state === 'error' ? player.error : null)
  const failure = $derived(failed ? splitFailure(failed) : null)
  const failedGame = $derived(player?.state === 'error' && player.engine === 'mame')
</script>

{#if !((view === 'remote' && watching) || (view === 'game' && gaming))}
  <PageHeader title={TITLES[view]}>
    {#if back}
      <button class="btn" data-testid="media-back" onclick={() => (view = back.to)}>
        <ArrowLeft size={30} aria-hidden="true" />{back.label}
      </button>
    {/if}
  </PageHeader>
{/if}

{#if view === 'remote' && player && watching}
  <!-- La télécommande a son titre (celui de la vidéo) et son bouton « Menu » : pas d'en-tête, pour tout garder à l'écran. -->
  <MediaRemote {player} onMenu={() => (view = 'menu')} />
{:else if view === 'game' && player && gaming}
  <MediaGameNow {player} />
{:else if view === 'tv'}
  <MediaTv onPlayed={() => (view = 'remote')} />
{:else if view === 'files'}
  <MediaFiles onPlayed={() => (view = 'remote')} />
{:else if view === 'games' || view === 'pads' || view === 'upload'}
  {#if failedGame && failed}
    <section class="card failed" data-testid="media-error">
      <h2>Le jeu n’a pas pu se lancer</h2>
      <p data-testid="media-error-text">{failure?.main}</p>
      <div class="ok-row">
        <button class="btn" data-testid="media-error-ok" onclick={() => media.control('dismiss')}>Compris</button>
        <span class="dim hint" data-testid="media-error-log">Journal : /var/log/mydock/player.log</span>
      </div>
    </section>
  {/if}
  {#if view === 'pads'}
    <MediaPads />
  {:else if view === 'upload'}
    <MediaUpload onDone={() => (view = 'games')} />
  {:else}
    <MediaGames onPads={() => (view = 'pads')} onUpload={() => (view = 'upload')} />
  {/if}
{:else}
  {#if failed}
    <section class="card failed" data-testid="media-error">
      <h2>{failedGame ? 'Le jeu n’a pas pu se lancer' : 'La lecture a échoué'}</h2>
      <p data-testid="media-error-text">{failure?.main}</p>
      {#if failure?.detail}<p class="detail dim" data-testid="media-error-detail">{failure.detail}</p>{/if}
      <div class="ok-row">
        <button class="btn" data-testid="media-error-ok" onclick={() => media.control('dismiss')}>Compris</button>
        <span class="dim hint" data-testid="media-error-log">Journal : /var/log/mydock/player.log</span>
      </div>
    </section>
  {/if}
  {#if active && player}
    <button class="now card" data-testid="media-now" onclick={() => (view = gaming ? 'game' : 'remote')}>
      {#if gaming}<Gamepad2 size={44} aria-hidden="true" />{:else}<MonitorPlay size={44} aria-hidden="true" />{/if}
      <span class="what"><strong>{player.title}</strong><small class="dim">{gaming ? 'En jeu' : player.state === 'paused' ? 'En pause' : 'En lecture'} · {gaming ? 'touche pour voir l’état' : 'touche pour la télécommande'}</small></span>
    </button>
  {/if}
  <div class="choices">
    <button class="choice tv" data-testid="media-tv" onclick={() => (view = 'tv')}>
      <Tv size={72} strokeWidth={1.6} aria-hidden="true" />
      <span class="label">Freetv</span>
      <span class="hint">Les chaînes de la Freebox</span>
    </button>
    <button class="choice files" data-testid="media-files" onclick={() => (view = 'files')}>
      <Folder size={72} strokeWidth={1.6} aria-hidden="true" />
      <span class="label">Kiosque</span>
      <span class="hint">Les films du partage de la Freebox</span>
    </button>
    <button class="choice games" data-testid="media-games" onclick={() => (view = 'games')}>
      <Gamepad2 size={72} strokeWidth={1.6} aria-hidden="true" />
      <span class="label">Retrogame</span>
      <span class="hint">Les jeux d’arcade, à la manette</span>
    </button>
  </div>
{/if}

<style>
  .choices {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
  }
  .choice {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 300px;
    padding: 16px;
    border-radius: 24px;
    color: #fff;
    text-align: center;
  }
  .choice:active {
    transform: scale(0.97);
  }
  .choice.tv {
    background: linear-gradient(180deg, #a855f7, #6b21a8);
  }
  .choice.files {
    background: linear-gradient(180deg, #0ea5e9, #075985);
  }
  .choice.games {
    background: linear-gradient(180deg, #f97316, #9a3412);
  }
  .label {
    font-size: calc(36px * var(--ts) * 0.7);
    font-weight: 700;
  }
  .hint {
    font-size: var(--fs-cap);
    opacity: 0.92;
  }
  .now {
    display: flex;
    align-items: center;
    gap: 16px;
    width: 100%;
    margin-bottom: 14px;
    border-color: var(--accent);
    text-align: left;
  }
  .what {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .what strong {
    font-size: var(--fs);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .failed {
    margin-bottom: 14px;
    border-color: var(--warn);
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  .failed h2 {
    color: var(--warn);
    margin: 0;
  }
  .failed p {
    margin: 0;
  }
  .ok-row {
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }
  .hint {
    font-size: var(--fs-cap);
    overflow-wrap: anywhere;
  }
  .detail {
    font-size: var(--fs-cap);
    line-height: 1.25;
    overflow-wrap: anywhere;
  }
</style>
