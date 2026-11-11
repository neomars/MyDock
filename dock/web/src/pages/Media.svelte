<script lang="ts">
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import Folder from '@lucide/svelte/icons/folder'
  import MonitorPlay from '@lucide/svelte/icons/monitor-play'
  import Tv from '@lucide/svelte/icons/tv'
  import { untrack } from 'svelte'
  import MediaFiles from '../components/MediaFiles.svelte'
  import MediaRemote from '../components/MediaRemote.svelte'
  import MediaTv from '../components/MediaTv.svelte'
  import PageHeader from '../components/PageHeader.svelte'
  import { playerActive, type MediaView } from '../lib/media'
  import { media } from '../lib/media.svelte'
  import { sock } from '../lib/socket.svelte'

  // Divertissement : Freetv (les chaînes de la Freebox) et Kiosque (ses partages Samba), lus par VLC sur cet écran.
  // Pendant une lecture la vidéo occupe tout l'écran ; un toucher ramène ici, sur la télécommande.
  type View = MediaView | 'remote'

  const player = $derived(sock.player)
  const active = $derived(playerActive(player))
  let view = $state<View>(untrack(() => (playerActive(sock.player) ? 'remote' : 'menu')))

  // La télécommande s'ouvre d'elle-même quand une lecture démarre ou que le dock revient devant la vidéo ; elle se ferme
  // quand la lecture s'arrête (le message d'une lecture en échec reste dans le menu).
  let wasActive = untrack(() => active)
  let wasFront = untrack(() => player?.front)
  $effect(() => {
    const now = active
    const front = player?.front
    untrack(() => {
      if (now && (!wasActive || (wasFront === 'video' && front === 'dock'))) view = 'remote'
      if (!now && view === 'remote') view = 'menu'
      wasActive = now
      wasFront = front
    })
  })

  const TITLES: Record<View, string> = { menu: 'Divertissement', tv: 'Freetv', files: 'Kiosque', remote: 'Lecture en cours' }
  const failed = $derived(player?.state === 'error' ? player.error : null)
</script>

{#if view !== 'remote' || !active}
  <PageHeader title={TITLES[view]}>
    {#if view !== 'menu'}
      <button class="btn" data-testid="media-back" onclick={() => (view = 'menu')}>
        <ArrowLeft size={30} aria-hidden="true" />Divertissement
      </button>
    {/if}
  </PageHeader>
{/if}

{#if view === 'remote' && player && active}
  <!-- La télécommande a son titre (celui de la vidéo) et son bouton « Menu » : pas d'en-tête, pour tout garder à l'écran. -->
  <MediaRemote {player} onMenu={() => (view = 'menu')} />
{:else if view === 'tv'}
  <MediaTv onPlayed={() => (view = 'remote')} />
{:else if view === 'files'}
  <MediaFiles onPlayed={() => (view = 'remote')} />
{:else}
  {#if failed}
    <section class="card failed" data-testid="media-error">
      <h2>La lecture a échoué</h2>
      <p>{failed}</p>
      <button class="btn" data-testid="media-error-ok" onclick={() => media.control('dismiss')}>Compris</button>
    </section>
  {/if}
  {#if active && player}
    <button class="now card" data-testid="media-now" onclick={() => (view = 'remote')}>
      <MonitorPlay size={44} aria-hidden="true" />
      <span class="what"><strong>{player.title}</strong><small class="dim">{player.state === 'paused' ? 'En pause' : 'En lecture'} · touche pour la télécommande</small></span>
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
  </div>
{/if}

<style>
  .choices {
    display: grid;
    grid-template-columns: 1fr 1fr;
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
</style>
