<script lang="ts">
  import ArrowDown from '@lucide/svelte/icons/arrow-down'
  import ArrowUp from '@lucide/svelte/icons/arrow-up'
  import Check from '@lucide/svelte/icons/check'
  import Plus from '@lucide/svelte/icons/plus'
  import Search from '@lucide/svelte/icons/search'
  import X from '@lucide/svelte/icons/x'
  import { appHue, appInitial } from '../lib/audio'
  import { editText } from '../lib/keyboard.svelte'
  import { MAX_PINNED, filterApps, moved, pinnedTiles, toggled } from '../lib/launcher'
  import { appIcons } from '../lib/pictures.svelte'
  import { picker } from '../lib/picker.svelte'
  import type { AppsData } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'
  import { whenVisible } from '../lib/visible'

  let query = $state('')
  /** Deux volets, pas deux colonnes : avec le texte doublé, les deux ne tiennent pas côte à côte dans 1024 px. */
  let tab = $state<'all' | 'chosen'>('all')
  $effect(() => {
    if (picker.open) tab = 'all'
  })

  // La fenêtre suit la liste des applications installées tant qu'elle est ouverte.
  $effect(() => {
    if (!picker.open) return
    return sock.subscribe(['apps'])
  })

  const data = $derived(sock.topics.apps as AppsData | undefined)
  const pinned = $derived(sock.ui?.pinned_apps ?? [])
  const tiles = $derived(pinnedTiles(pinned, data?.apps))
  const shown = $derived(filterApps(data?.apps ?? [], query))
  const full = $derived(pinned.length >= MAX_PINNED)

  async function save(next: string[]): Promise<void> {
    const ack = await sock.act('dock.ui.set', { pinned_apps: next })
    if (!ack.ok) notify(ack.e?.msg ?? 'Modification impossible.', 'bad', 6_000)
  }

  async function toggle(id: string): Promise<void> {
    const next = toggled(pinned, id)
    if (next === null) {
      notify(`Le lanceur contient déjà ${MAX_PINNED} applications : retire-en une d’abord.`, 'info', 5_000)
      return
    }
    await save(next)
  }

  async function search(): Promise<void> {
    const value = await editText({ title: 'Rechercher une application', hint: 'Une partie du nom', kind: 'text', value: query })
    if (value !== null) query = value.trim()
  }
</script>

{#snippet icon(name: string, key: string | null | undefined)}
  <span class="icon" style:--hue={appHue(name)} aria-hidden="true">
    {#if key && appIcons.urls[key]}<img src={appIcons.urls[key]} alt="" />{:else}{appInitial(name)}{/if}
  </span>
{/snippet}

{#if picker.open}
  <div class="overlay" role="dialog" aria-modal="true" aria-label="Choisir les applications du lanceur" data-testid="picker">
    <header>
      <div class="tabs" role="group" aria-label="Affichage">
        <button class="tab" class:on={tab === 'all'} aria-pressed={tab === 'all'} data-testid="picker-tab-all" onclick={() => (tab = 'all')}>
          Applications
        </button>
        <button class="tab" class:on={tab === 'chosen'} aria-pressed={tab === 'chosen'} data-testid="picker-tab-chosen" onclick={() => (tab = 'chosen')}>
          Lanceur ({pinned.length}/{MAX_PINNED})
        </button>
      </div>
      <button class="btn primary" data-testid="picker-done" onclick={() => picker.hide()}>
        <Check size={34} aria-hidden="true" />Terminé
      </button>
    </header>

    {#if tab === 'all'}
      <section class="card" data-testid="picker-all">
        <div class="search">
          <button class="btn field" data-testid="picker-search" onclick={search}>
            <Search size={32} aria-hidden="true" />
            <span class:dim={!query}>{query || 'Rechercher une application…'}</span>
          </button>
          {#if query}
            <button class="btn" aria-label="Effacer la recherche" data-testid="picker-clear" onclick={() => (query = '')}>
              <X size={32} aria-hidden="true" />
            </button>
          {/if}
        </div>
        <div class="list">
          {#if !data}
            <p class="muted">Lecture de la liste…</p>
          {:else if shown.length === 0}
            <p class="muted" data-testid="picker-none">Aucune application ne correspond.</p>
          {/if}
          {#each shown as app (app.id)}
            {@const on = pinned.includes(app.id)}
            <div class="row" use:whenVisible={() => appIcons.want(app.icon)}>
              {@render icon(app.name, app.icon)}
              <span class="who">
                <strong>{app.name}</strong>
                {#if app.generic}<small>{app.generic}</small>{/if}
              </span>
              <button
                class="step pick"
                class:on
                aria-pressed={on}
                aria-label="{on ? 'Retirer' : 'Ajouter'} {app.name}"
                data-testid="pick-{app.id}"
                disabled={!on && full}
                onclick={() => toggle(app.id)}
              >
                {#if on}<Check size={34} aria-hidden="true" />{:else}<Plus size={34} aria-hidden="true" />{/if}
              </button>
            </div>
          {/each}
        </div>
      </section>
    {:else}
      <section class="card" data-testid="picker-chosen">
        <div class="list">
          {#if pinned.length === 0}
            <p class="muted" data-testid="chosen-none">Rien pour l’instant : ajoute une application depuis l’onglet « Applications ».</p>
          {/if}
          {#each tiles as tile, index (tile.id)}
            <div class="row" class:gone={!tile.app} data-testid="chosen-{tile.id}" use:whenVisible={() => appIcons.want(tile.app?.icon)}>
              {@render icon(tile.name, tile.app?.icon)}
              <span class="who"><strong>{tile.name}</strong>{#if !tile.app}<small>introuvable sur ce PC</small>{/if}</span>
              <button class="step" aria-label="Monter {tile.name}" data-testid="up-{tile.id}" disabled={index === 0} onclick={() => save(moved(pinned, index, -1))}>
                <ArrowUp size={34} aria-hidden="true" />
              </button>
              <button class="step" aria-label="Descendre {tile.name}" data-testid="down-{tile.id}" disabled={index === tiles.length - 1} onclick={() => save(moved(pinned, index, 1))}>
                <ArrowDown size={34} aria-hidden="true" />
              </button>
              <button class="step remove" aria-label="Retirer {tile.name}" data-testid="remove-{tile.id}" onclick={() => save(pinned.filter((p) => p !== tile.id))}>
                <X size={34} aria-hidden="true" />
              </button>
            </div>
          {/each}
        </div>
      </section>
    {/if}
  </div>
{/if}

<style>
  /* Plein écran sous la barre du haut, un volet à la fois (texte doublé). */
  .overlay {
    position: fixed;
    inset: var(--topbar) 0 0 0;
    z-index: 30;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 16px 10px;
    background: var(--bg);
  }
  header {
    flex: none;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .tabs {
    flex: 1;
    min-width: 0;
    display: flex;
    gap: 8px;
  }
  .tab {
    flex: 1 1 0;
    min-width: 0;
    min-height: var(--tap);
    padding: 0 16px;
    border-radius: 12px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    font-weight: 600;
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tab.on {
    background: var(--accent-soft);
    border-color: var(--accent);
    color: var(--accent);
  }
  .card {
    flex: 1 1 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-height: 0;
    padding: 8px 12px;
  }
  .search {
    flex: none;
    display: flex;
    gap: 8px;
  }
  .field {
    flex: 1 1 0;
    min-width: 0;
    justify-content: flex-start;
    font-weight: 500;
  }
  .field span {
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .list {
    flex: 1 1 0;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 80px;
    border-top: 1px solid var(--line);
  }
  .row:first-child {
    border-top: 0;
  }
  .row.gone {
    opacity: 0.6;
  }
  .icon {
    flex: none;
    display: grid;
    place-items: center;
    width: 56px;
    height: 56px;
    overflow: hidden;
    border-radius: 12px;
    background: hsl(var(--hue) 50% 28%);
    color: hsl(var(--hue) 85% 86%);
    font-size: var(--fs-small);
    font-weight: 700;
  }
  .icon img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: var(--surface-2);
  }
  .who {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    line-height: 1.15;
  }
  .who strong {
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .who small {
    font-size: calc(13px * var(--ts));
    color: var(--text-3);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .step {
    flex: none;
    display: grid;
    place-items: center;
    width: var(--tap);
    height: 64px;
    border-radius: 12px;
    background: var(--surface-3);
  }
  .step:active:not(:disabled) {
    background: var(--line);
  }
  .step:disabled {
    opacity: 0.3;
  }
  .step.pick.on {
    background: var(--accent-soft);
    color: var(--accent);
    box-shadow: inset 0 0 0 2px var(--accent);
  }
  .step.remove:active {
    background: var(--bad-soft);
    color: var(--bad);
  }
</style>
