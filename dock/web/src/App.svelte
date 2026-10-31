<script lang="ts">
  import type { Component } from 'svelte'
  import { onMount } from 'svelte'
  import AppPicker from './components/AppPicker.svelte'
  import ConfirmDialog from './components/ConfirmDialog.svelte'
  import DebugOverlay from './components/DebugOverlay.svelte'
  import Keyboard from './components/Keyboard.svelte'
  import MarketsEditor from './components/MarketsEditor.svelte'
  import OfflineScreen from './components/OfflineScreen.svelte'
  import PagesEditor from './components/PagesEditor.svelte'
  import ScreenSleep from './components/ScreenSleep.svelte'
  import TabBar from './components/TabBar.svelte'
  import Toast from './components/Toast.svelte'
  import TopBar from './components/TopBar.svelte'
  import { pageById, shownPage, visiblePages } from './lib/pages'
  import type { PageId } from './lib/protocol'
  import { sock } from './lib/socket.svelte'
  import { wakeDone } from './lib/wake.svelte'

  const debug = new URLSearchParams(location.search).has('debug')

  // Une page par onglet : le compilateur refuse qu'il en manque une.
  const loaders: Record<PageId, () => Promise<{ default: Component }>> = {
    music: () => import('./pages/Music.svelte'),
    sound: () => import('./pages/Sound.svelte'),
    apps: () => import('./pages/Apps.svelte'),
    steam: () => import('./pages/Steam.svelte'),
    office: () => import('./pages/Office.svelte'),
    markets: () => import('./pages/Markets.svelte'),
    system: () => import('./pages/System.svelte'),
    power: () => import('./pages/Power.svelte'),
    settings: () => import('./pages/Settings.svelte'),
  }

  const pages = $derived(visiblePages(sock.ui?.order ?? [], sock.ui?.hidden ?? []))
  let selected = $state<PageId | null>(null)
  const currentId = $derived<PageId>(shownPage(selected ?? sock.ui?.last_page ?? 'system', pages))
  const current = $derived(pageById(currentId))

  function select(id: PageId): void {
    selected = id
    void sock.act('dock.ui.set', { last_page: id })
  }

  // La barre du haut montre toujours le nom du PC (« host ») et le mini-lecteur (« music ») : on s'abonne
  // à ces deux thèmes en permanence ; les pages suivent les autres (system, procs…) tant qu'elles sont ouvertes.
  onMount(() => {
    sock.connect()
    return sock.subscribe(['host', 'music'])
  })

  // Quand le PC répond enfin, le « réveil en cours » est terminé.
  $effect(() => {
    if (sock.connected) wakeDone()
  })

  // Liaison navigateur -> dock perdue (le dock redémarre ?) : on l'affiche après un court délai.
  let dockLost = $state(false)
  $effect(() => {
    if (sock.status === 'open') {
      dockLost = false
      return
    }
    const timer = setTimeout(() => (dockLost = true), 1_500)
    return () => clearTimeout(timer)
  })
</script>

<div class="app">
  <TopBar onOpenMusic={() => select('music')} />

  <main class="page" data-testid="page-{currentId}">
    {#if dockLost}
      <section class="lost" data-testid="dock-lost">
        <h1>Dock injoignable</h1>
        <p class="muted">Le serveur du dock ne répond plus. Reconnexion automatique…</p>
      </section>
    {:else if !sock.link}
      <!-- démarrage : les premiers instantanés arrivent en quelques millisecondes -->
    {:else if current.needsPc && !sock.connected}
      <OfflineScreen onSettings={() => select('settings')} />
    {:else}
      {@const load = loaders[currentId]}
      {#key currentId}
        {#await load() then module}
          {@const Page = module.default}
          <Page />
        {/await}
      {/key}
    {/if}
  </main>

  <TabBar {pages} current={currentId} onSelect={select} />
</div>

<AppPicker />
<MarketsEditor />
<PagesEditor />
<Keyboard />
<ConfirmDialog />
<Toast />
<ScreenSleep />
{#if debug}<DebugOverlay />{/if}

<style>
  .app {
    display: grid;
    grid-template-rows: var(--topbar) minmax(0, 1fr) var(--tabbar);
    height: 100%;
  }
  .page {
    overflow-y: auto;
    overflow-x: hidden;
    padding: 12px 20px 14px;
    overscroll-behavior: contain;
  }
  .lost {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    text-align: center;
  }
  .lost h1 {
    font-size: var(--fs-title);
    color: var(--warn);
  }
</style>
