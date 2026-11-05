<script lang="ts">
  import LayoutGrid from '@lucide/svelte/icons/layout-grid'
  import Pencil from '@lucide/svelte/icons/pencil'
  import { onMount } from 'svelte'
  import { appHue, appInitial } from '../lib/audio'
  import { launch } from '../lib/launch.svelte'
  import { pinnedTiles, type Tile } from '../lib/launcher'
  import { appIcons } from '../lib/pictures.svelte'
  import { picker } from '../lib/picker.svelte'
  import type { AppsData } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'

  // La page suit la liste des applications du PC (noms et icônes) tant qu'elle est ouverte.
  onMount(() => sock.subscribe(['apps']))

  const data = $derived(sock.topics.apps as AppsData | undefined)
  const tiles = $derived(pinnedTiles(sock.ui?.pinned_apps ?? [], data?.apps))

  $effect(() => {
    for (const tile of tiles) appIcons.want(tile.app?.icon)
  })

  function open(tile: Tile): void {
    void launch.run(tile.id, 'apps.launch', { id: tile.id }, tile.name)
  }
</script>

{#if tiles.length === 0}
  <section class="empty" data-testid="apps-empty">
    <LayoutGrid size={64} aria-hidden="true" />
    <h1>Aucune application choisie</h1>
    <p class="muted">Choisis les applications que tu veux lancer d’un toucher, et dans quel ordre.</p>
    <button class="btn primary big" data-testid="apps-choose" onclick={() => picker.show()}>Choisir les applications</button>
  </section>
{:else}
  <div class="bar">
    <span class="dim" data-testid="apps-count">{tiles.length} application{tiles.length > 1 ? 's' : ''}</span>
    <button class="btn" data-testid="apps-edit" onclick={() => picker.show()}>
      <Pencil size={22} aria-hidden="true" />Modifier
    </button>
  </div>
  <div class="grid" data-testid="apps-grid">
    {#each tiles as tile (tile.id)}
      {@const busy = launch.isBusy(tile.id)}
      {@const url = tile.app?.icon ? appIcons.urls[tile.app.icon] : undefined}
      <button
        class="tile"
        class:busy
        disabled={!tile.app}
        aria-label={tile.app ? `Lancer ${tile.name}` : `${tile.name} : introuvable sur ce PC`}
        data-testid="launch-{tile.id}"
        onclick={() => open(tile)}
      >
        <span class="art" style:--hue={appHue(tile.name)}>
          {#if url}<img src={url} alt="" />{:else}<b aria-hidden="true">{appInitial(tile.name)}</b>{/if}
        </span>
        <span class="name" class:short={busy || !tile.app}>{tile.name}</span>
        {#if busy}<span class="state" data-testid="launching-{tile.id}">Lancement…</span>{/if}
        {#if !tile.app}<span class="state gone">Introuvable</span>{/if}
      </button>
    {/each}
  </div>
{/if}

<style>
  .bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: calc(var(--tap) + 8px);
    margin-bottom: 10px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
  }
  .tile {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    min-height: 224px; /* la hauteur d'un nom sur deux lignes : une tuile ne change pas de taille en lançant */
    padding: 14px 8px 8px;
    border-radius: var(--radius);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .tile:active:not(:disabled) {
    background: var(--surface-3);
    transform: scale(0.97);
  }
  .tile.busy {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .tile:disabled {
    opacity: 0.5;
  }
  .art {
    display: grid;
    place-items: center;
    width: 104px;
    height: 104px;
    overflow: hidden;
    border-radius: 24px;
    background: hsl(var(--hue) 50% 28%);
    color: hsl(var(--hue) 85% 86%);
    font-size: calc(30px * var(--ts));
  }
  .art img {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }
  .name {
    width: 100%;
    font-size: var(--fs-cap);
    font-weight: 600;
    line-height: 1.2;
    text-align: center;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  /* Le nom perd sa seconde ligne au profit de l'état : la tuile ne change pas de hauteur. */
  .name.short {
    -webkit-line-clamp: 1;
    line-clamp: 1;
  }
  .state {
    margin-top: -8px;
    font-size: var(--fs-cap);
    font-weight: 700;
    line-height: 1.2;
    color: var(--accent);
  }
  .state.gone {
    color: var(--text-2);
  }
  .empty {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    text-align: center;
    color: var(--text-3);
  }
  .empty h1 {
    font-size: var(--fs-title);
    color: var(--text);
  }
  .empty p {
    max-width: 760px;
  }
</style>
