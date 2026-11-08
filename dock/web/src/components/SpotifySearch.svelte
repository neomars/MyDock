<script lang="ts">
  import CircleAlert from '@lucide/svelte/icons/circle-alert'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import Search from '@lucide/svelte/icons/search'
  import X from '@lucide/svelte/icons/x'
  import { editText } from '../lib/keyboard.svelte'
  import { music } from '../lib/music.svelte'
  import type { SpotifyItem } from '../lib/protocol'
  import { accountView, contextFor, isNowPlaying, searchGroups } from '../lib/spotify'
  import { spotify } from '../lib/spotify.svelte'
  import SpotifyConnect from './SpotifyConnect.svelte'
  import SpotifyNotice from './SpotifyNotice.svelte'
  import SpotifyRow from './SpotifyRow.svelte'

  const ready = $derived(accountView(spotify.status) === 'ready' && !spotify.statusError)
  const result = $derived(spotify.search.state === 'ready' ? spotify.search.data : null)
  const groups = $derived(searchGroups(result))

  async function ask(): Promise<void> {
    const text = await editText({
      title: 'Rechercher sur Spotify',
      hint: 'Un titre, un artiste, un album ou une playlist',
      kind: 'text',
      value: spotify.query,
    })
    if (text !== null) await spotify.runSearch(text)
  }

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
{:else}
  <div class="bar">
    <button class="field" data-testid="sp-search-field" aria-label="Rechercher sur Spotify" onclick={() => void ask()}>
      <Search size={40} aria-hidden="true" />
      <span class="query" class:placeholder={!spotify.query}>{spotify.query || 'Que veux-tu écouter ?'}</span>
    </button>
    {#if spotify.query}
      <button class="clear" aria-label="Effacer la recherche" data-testid="sp-search-clear" onclick={() => void spotify.runSearch('')}>
        <X size={40} aria-hidden="true" />
      </button>
    {/if}
  </div>

  {#if spotify.search.state === 'idle'}
    <SpotifyNotice icon={Search} title="Cherche de la musique" testid="sp-search-idle">
      <p>Touche le champ pour écrire le titre d’un morceau, d’un artiste, d’un album ou d’une playlist.</p>
    </SpotifyNotice>
  {:else if spotify.search.state === 'loading'}
    <SpotifyNotice icon={RefreshCw} title="Recherche…" testid="sp-search-loading" />
  {:else if spotify.search.state === 'error'}
    <SpotifyNotice icon={CircleAlert} title="La recherche a échoué" testid="sp-search-error" tone="bad">
      <p data-testid="sp-search-error-message">{spotify.search.message}</p>
      <button class="again" data-testid="sp-search-retry" onclick={() => void spotify.runSearch(spotify.query)}>
        <RefreshCw size={32} aria-hidden="true" /> Réessayer
      </button>
    </SpotifyNotice>
  {:else if groups.length === 0}
    <SpotifyNotice icon={Search} title="Aucun résultat" testid="sp-search-empty">
      <p>Spotify n’a rien trouvé pour « {spotify.query} ». Essaie une autre orthographe.</p>
    </SpotifyNotice>
  {:else}
    <div class="results" data-testid="sp-results">
      {#each groups as group (group.id)}
        <section data-testid="sp-group-{group.id}">
          <h2>{group.label}</h2>
          {#each group.items as item (item.uri)}
            <SpotifyRow
              {item}
              active={item.kind === 'track' && isNowPlaying(item, music.active)}
              busy={spotify.isStarting(item.uri)}
              testid="sp-{item.kind}-{item.id}"
              onTap={() => tap(item)}
            />
          {/each}
        </section>
      {/each}
    </div>
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
    gap: 10px;
    padding-bottom: 10px;
    background: var(--bg);
  }
  .field {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 16px;
    min-height: 84px;
    padding: 0 24px;
    border-radius: 999px;
    background: var(--surface-2);
    border: 2px solid var(--line);
    font-size: var(--fs);
  }
  .field:active {
    background: var(--surface-3);
  }
  .query {
    flex: 1 1 0;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .query.placeholder {
    color: var(--text-3);
  }
  .clear {
    flex: none;
    display: grid;
    place-items: center;
    width: 84px;
    height: 84px;
    border-radius: 50%;
    background: var(--surface-3);
  }
  .clear:active {
    background: var(--line);
  }
  h2 {
    margin: 8px 8px 4px;
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
