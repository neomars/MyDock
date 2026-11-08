<script lang="ts">
  import ChevronLeft from '@lucide/svelte/icons/chevron-left'
  import CircleAlert from '@lucide/svelte/icons/circle-alert'
  import Disc3 from '@lucide/svelte/icons/disc-3'
  import ListMusic from '@lucide/svelte/icons/list-music'
  import Play from '@lucide/svelte/icons/play'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import UserRound from '@lucide/svelte/icons/user-round'
  import { music } from '../lib/music.svelte'
  import { contextFor, isNowPlaying, itemSubtitle } from '../lib/spotify'
  import { spotify, spotifyArt } from '../lib/spotify.svelte'
  import { whenVisible } from '../lib/visible'
  import SpotifyNotice from './SpotifyNotice.svelte'
  import SpotifyRow from './SpotifyRow.svelte'

  // La page d'un album, d'une playlist ou d'un artiste : en-tête avec « Lecture », puis ses morceaux (et ses albums).
  const entry = $derived(spotify.opened)
  const view = $derived(entry?.view.state === 'ready' ? entry.view.data : null)
  const item = $derived(view?.item ?? null)
  const art = $derived(item?.art ?? null)
  const cover = $derived(art ? spotifyArt.urls[art] : undefined)
  const Fallback = $derived(entry?.kind === 'artist' ? UserRound : entry?.kind === 'album' ? Disc3 : ListMusic)
  const page = $derived(item ? { kind: item.kind, uri: item.uri } : null)
</script>

{#if entry}
  <div class="bar">
    <button class="back" aria-label="Retour" data-testid="sp-back" onclick={() => spotify.back()}>
      <ChevronLeft size={44} aria-hidden="true" />
    </button>
    <h1 class="name" data-testid="sp-detail-title">{item?.name ?? entry.name}</h1>
  </div>

  {#if entry.view.state === 'loading' || entry.view.state === 'idle'}
    <SpotifyNotice icon={RefreshCw} title="Chargement…" testid="sp-detail-loading" />
  {:else if entry.view.state === 'error'}
    <SpotifyNotice icon={CircleAlert} title="Cette page n’a pas pu être ouverte" testid="sp-detail-error" tone="bad">
      <p data-testid="sp-detail-error-message">{entry.view.message}</p>
      <button class="again" data-testid="sp-detail-retry" onclick={() => spotify.retryOpened()}>
        <RefreshCw size={32} aria-hidden="true" /> Réessayer
      </button>
    </SpotifyNotice>
  {:else if view && item}
    <header class="hero" data-testid="sp-detail">
      <span class="cover" class:round={entry.kind === 'artist'} use:whenVisible={() => spotifyArt.want(art)}>
        {#if cover}<img src={cover} alt="" decoding="async" />{:else}<Fallback size={56} aria-hidden="true" />{/if}
      </span>
      <div class="info">
        <p class="sub">{itemSubtitle(item) || (entry.kind === 'artist' ? 'Artiste' : '')}</p>
        <button
          class="play"
          data-testid="sp-detail-play"
          disabled={spotify.isStarting(item.uri)}
          onclick={() => void spotify.play(item.uri, undefined, item.name)}
        >
          <Play size={40} aria-hidden="true" />
          {spotify.isStarting(item.uri) ? 'Lecture…' : 'Lecture'}
        </button>
      </div>
    </header>

    {#if view.tracks.length > 0}
      <section data-testid="sp-detail-tracks">
        {#if entry.kind === 'artist'}<h2>Titres populaires</h2>{/if}
        {#each view.tracks as track, index (track.id + index)}
          <SpotifyRow
            item={track}
            number={entry.kind === 'album' ? (track.number ?? index + 1) : null}
            active={isNowPlaying(track, music.active)}
            busy={spotify.isStarting(track.uri)}
            testid="sp-track-{track.id}"
            onTap={() => void spotify.play(track.uri, contextFor(track, page), track.name)}
          />
        {/each}
      </section>
    {/if}

    {#if view.albums && view.albums.length > 0}
      <section data-testid="sp-detail-albums">
        <h2>Albums</h2>
        {#each view.albums as album (album.id)}
          <SpotifyRow item={album} testid="sp-album-{album.id}" onTap={() => void spotify.open('album', album.id, album.name)} />
        {/each}
      </section>
    {/if}

    {#if view.tracks.length === 0 && !(view.albums && view.albums.length > 0)}
      <SpotifyNotice icon={Fallback} title="Rien à afficher" testid="sp-detail-empty">
        <p>Spotify ne donne aucun morceau pour cette page.</p>
      </SpotifyNotice>
    {/if}
  {/if}
{/if}

<style>
  .bar {
    /* La page a 14 px de marge en haut : la barre colle au bord réel de l'écran et la recouvre, pour que la liste ne
       passe pas au-dessus d'elle. */
    position: sticky;
    top: -14px;
    margin-top: -14px;
    padding-top: 14px;
    z-index: 2;
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--bg);
  }
  .back {
    flex: none;
    display: grid;
    place-items: center;
    width: 84px;
    height: 84px;
    border-radius: 50%;
  }
  .back:active {
    background: var(--surface-3);
  }
  .name {
    min-width: 0;
    font-size: var(--fs-title);
    font-weight: 700;
    letter-spacing: -0.01em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .hero {
    display: flex;
    align-items: center;
    gap: 22px;
    margin: 4px 0 10px;
  }
  .cover {
    flex: none;
    display: grid;
    place-items: center;
    width: 148px;
    height: 148px;
    border-radius: 14px;
    background: var(--surface-3);
    color: var(--text-3);
    overflow: hidden;
  }
  .cover.round {
    border-radius: 50%;
  }
  .cover img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .info {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
  .sub {
    max-width: 100%;
    font-size: var(--fs-small);
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .play {
    display: inline-flex;
    align-items: center;
    gap: 12px;
    min-height: 84px;
    padding: 0 40px 0 30px;
    border-radius: 999px;
    background: var(--accent);
    color: var(--accent-ink);
    font-size: var(--fs);
    font-weight: 700;
  }
  .play:active:not(:disabled) {
    filter: brightness(1.15);
    transform: scale(0.97);
  }
  .play:disabled {
    opacity: 0.6;
  }
  h2 {
    margin: 10px 8px 4px;
    font-size: var(--fs-small);
    font-weight: 700;
    color: var(--text-2);
  }
  .again {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    min-height: var(--tap);
    padding: 0 26px;
    border-radius: 14px;
    background: var(--surface-3);
    font-weight: 600;
  }
  .again:active {
    background: var(--line);
    transform: scale(0.97);
  }
</style>
