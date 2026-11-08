<script lang="ts">
  import AudioLines from '@lucide/svelte/icons/audio-lines'
  import ChevronRight from '@lucide/svelte/icons/chevron-right'
  import Disc3 from '@lucide/svelte/icons/disc-3'
  import ListMusic from '@lucide/svelte/icons/list-music'
  import Music from '@lucide/svelte/icons/music'
  import UserRound from '@lucide/svelte/icons/user-round'
  import type { SpotifyItem } from '../lib/protocol'
  import { durationText, itemSubtitle } from '../lib/spotify'
  import { spotifyArt } from '../lib/spotify.svelte'
  import { whenVisible } from '../lib/visible'

  // Une ligne de liste : pochette (ou numéro), nom, ligne d'aide, et à droite la durée ou une flèche.
  let {
    item,
    number = null,
    active = false,
    busy = false,
    testid,
    onTap,
  }: {
    item: SpotifyItem
    /** Dans un album : le numéro du morceau remplace la pochette (qui serait la même partout). */
    number?: number | null
    /** C'est le morceau que joue le lecteur. */
    active?: boolean
    /** Sa lecture vient d'être demandée. */
    busy?: boolean
    testid: string
    onTap: () => void
  } = $props()

  const url = $derived(item.art ? spotifyArt.urls[item.art] : undefined)
  const duration = $derived(item.kind === 'track' ? durationText(item.duration_ms) : '')
  const unplayable = $derived(item.kind === 'track' && !item.playable)
  const Fallback = $derived(
    item.kind === 'artist' ? UserRound : item.kind === 'album' ? Disc3 : item.kind === 'playlist' ? ListMusic : Music,
  )
</script>

<button
  class="row"
  class:active
  class:busy
  class:unplayable
  disabled={unplayable}
  data-testid={testid}
  data-kind={item.kind}
  data-active={active ? 'true' : null}
  aria-label="{item.kind === 'track' ? 'Jouer' : 'Ouvrir'} {item.name}"
  onclick={onTap}
  use:whenVisible={() => {
    if (number === null) spotifyArt.want(item.art)
  }}
>
  {#if number !== null}
    <span class="num">
      {#if active}<AudioLines size={36} aria-hidden="true" />{:else}{number}{/if}
    </span>
  {:else}
    <span class="thumb" class:round={item.kind === 'artist'}>
      {#if url}
        <img src={url} alt="" decoding="async" />
      {:else}
        <Fallback size={38} aria-hidden="true" />
      {/if}
    </span>
  {/if}
  <span class="text">
    <strong class="name">{item.name}{#if item.kind === 'track' && item.explicit}<i class="explicit" title="Contenu explicite">E</i>{/if}</strong>
    <small class="sub">{itemSubtitle(item)}</small>
  </span>
  <span class="end">
    {#if busy}
      <small class="starting">Lecture…</small>
    {:else if active && number === null}
      <AudioLines size={36} aria-hidden="true" />
    {:else if duration}
      <small>{duration}</small>
    {:else if item.kind !== 'track'}
      <ChevronRight size={36} aria-hidden="true" />
    {/if}
  </span>
</button>

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 16px;
    width: 100%;
    min-height: 96px;
    padding: 8px 14px 8px 8px;
    border-radius: var(--radius);
    text-align: left;
  }
  .row:active:not(:disabled) {
    background: var(--surface-3);
  }
  .row.active {
    background: var(--accent-soft);
  }
  .row.unplayable {
    opacity: 0.4;
  }
  .thumb {
    flex: none;
    display: grid;
    place-items: center;
    width: 80px;
    height: 80px;
    border-radius: 8px;
    background: var(--surface-3);
    color: var(--text-3);
    overflow: hidden;
  }
  .thumb.round {
    border-radius: 50%;
  }
  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .num {
    flex: none;
    display: grid;
    place-items: center;
    width: 56px;
    color: var(--text-3);
    font-size: var(--fs-small);
  }
  .active .num {
    color: var(--accent);
  }
  .text {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    line-height: 1.2;
  }
  .name {
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .active .name {
    color: var(--accent);
  }
  .sub {
    font-size: var(--fs-cap);
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .explicit {
    display: inline-grid;
    place-items: center;
    width: 1.1em;
    height: 1.1em;
    margin-left: 10px;
    border-radius: 4px;
    background: var(--text-3);
    color: var(--bg);
    font-size: 0.6em;
    font-style: normal;
    font-weight: 800;
    vertical-align: middle;
  }
  .end {
    flex: none;
    display: flex;
    align-items: center;
    color: var(--text-3);
  }
  .end small {
    font-size: var(--fs-cap);
  }
  .active .end {
    color: var(--accent);
  }
  .starting {
    color: var(--accent);
    font-weight: 600;
  }
</style>
