<script lang="ts">
  import CircleAlert from '@lucide/svelte/icons/circle-alert'
  import ChevronLeft from '@lucide/svelte/icons/chevron-left'
  import Disc3 from '@lucide/svelte/icons/disc-3'
  import Heart from '@lucide/svelte/icons/heart'
  import History from '@lucide/svelte/icons/history'
  import ListMusic from '@lucide/svelte/icons/list-music'
  import MicVocal from '@lucide/svelte/icons/mic-vocal'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import type { Component } from 'svelte'
  import { music } from '../lib/music.svelte'
  import type { LibrarySection, SpotifyItem } from '../lib/protocol'
  import { SECTIONS, accountView, contextFor, isNowPlaying, sectionLabel } from '../lib/spotify'
  import { spotify } from '../lib/spotify.svelte'
  import SpotifyConnect from './SpotifyConnect.svelte'
  import SpotifyNotice from './SpotifyNotice.svelte'
  import SpotifyRow from './SpotifyRow.svelte'

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ICONS: Record<LibrarySection, Component<any>> = {
    playlists: ListMusic,
    liked: Heart,
    albums: Disc3,
    artists: MicVocal,
    recent: History,
  }

  const ready = $derived(accountView(spotify.status) === 'ready' && !spotify.statusError)
  const items = $derived(spotify.library.state === 'ready' ? spotify.library.data.items : [])

  function tap(item: SpotifyItem): void {
    if (item.kind === 'track') {
      void spotify.play(item.uri, contextFor(item), item.name)
    } else {
      void spotify.open(item.kind, item.id, item.name)
    }
  }
</script>

{#if !ready}
  <SpotifyConnect />
{:else if spotify.section === null}
  <h1 class="head" data-testid="sp-library-title">Ta bibliothèque</h1>
  <div class="sections" data-testid="sp-sections">
    {#each SECTIONS as section (section.id)}
      {@const Icon = ICONS[section.id]}
      <button class="section" data-testid="sp-section-{section.id}" onclick={() => void spotify.openSection(section.id)}>
        <Icon size={44} aria-hidden="true" />
        <span>{section.label}</span>
      </button>
    {/each}
  </div>
{:else}
  <div class="bar">
    <button class="back" aria-label="Retour à la bibliothèque" data-testid="sp-back" onclick={() => spotify.back()}>
      <ChevronLeft size={44} aria-hidden="true" />
    </button>
    <h1 class="head" data-testid="sp-library-title">{sectionLabel(spotify.section)}</h1>
  </div>

  {#if spotify.library.state === 'loading' || spotify.library.state === 'idle'}
    <SpotifyNotice icon={RefreshCw} title="Chargement…" testid="sp-library-loading" />
  {:else if spotify.library.state === 'error'}
    <SpotifyNotice icon={CircleAlert} title="La bibliothèque n’a pas pu être lue" testid="sp-library-error" tone="bad">
      <p data-testid="sp-library-error-message">{spotify.library.message}</p>
      <button class="again" data-testid="sp-library-retry" onclick={() => spotify.retryLibrary()}>
        <RefreshCw size={32} aria-hidden="true" /> Réessayer
      </button>
    </SpotifyNotice>
  {:else if items.length === 0}
    <SpotifyNotice icon={ICONS[spotify.section]} title="C’est vide pour l’instant" testid="sp-library-empty">
      <p>Rien dans « {sectionLabel(spotify.section)} » sur ce compte.</p>
    </SpotifyNotice>
  {:else}
    <div class="results" data-testid="sp-library-items">
      {#each items as item (item.kind + item.id)}
        <SpotifyRow
          {item}
          active={item.kind === 'track' && isNowPlaying(item, music.active)}
          busy={spotify.isStarting(item.uri)}
          testid="sp-{item.kind}-{item.id}"
          onTap={() => tap(item)}
        />
      {/each}
    </div>
  {/if}
{/if}

<style>
  .head {
    font-size: var(--fs-title);
    font-weight: 700;
    letter-spacing: -0.01em;
    margin-bottom: 12px;
  }
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
    margin-bottom: 4px;
    background: var(--bg);
  }
  .bar .head {
    margin: 0;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
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
  .sections {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .section {
    display: flex;
    align-items: center;
    gap: 16px;
    min-height: 112px;
    padding: 0 22px;
    border-radius: var(--radius);
    background: var(--surface-2);
    border: 1px solid var(--line);
    font-size: var(--fs-small);
    font-weight: 600;
    text-align: left;
  }
  .section:active {
    background: var(--surface-3);
    transform: scale(0.98);
  }
  /* Cinq rubriques sur deux colonnes : la dernière prend toute la largeur au lieu de laisser un trou. */
  .section:last-child:nth-child(odd) {
    grid-column: 1 / -1;
  }
  .section span {
    min-width: 0;
    line-height: 1.15;
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
