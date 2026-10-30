<script lang="ts">
  import type { Component } from 'svelte'
  import { onMount } from 'svelte'
  import ConfirmDialog from './components/ConfirmDialog.svelte'
  import DebugOverlay from './components/DebugOverlay.svelte'
  import Keyboard from './components/Keyboard.svelte'
  import OfflineScreen from './components/OfflineScreen.svelte'
  import TabBar from './components/TabBar.svelte'
  import Toast from './components/Toast.svelte'
  import TopBar from './components/TopBar.svelte'
  import { pageById, visiblePages } from './lib/pages'
  import type { PageId } from './lib/protocol'
  import { sock } from './lib/socket.svelte'
  import { wakeDone } from './lib/wake.svelte'
  import Soon from './pages/Soon.svelte'

  const debug = new URLSearchParams(location.search).has('debug')

  // Pages complètes à cette étape ; les autres affichent ce qu'elles offriront (Soon).
  const loaders: Partial<Record<PageId, () => Promise<{ default: Component }>>> = {
    music: () => import('./pages/Music.svelte'),
    system: () => import('./pages/System.svelte'),
    power: () => import('./pages/Power.svelte'),
    settings: () => import('./pages/Settings.svelte'),
  }

  const pages = $derived(visiblePages(sock.ui?.order ?? [], sock.ui?.hidden ?? []))
  let selected = $state<PageId | null>(null)
  const currentId = $derived<PageId>(selected ?? sock.ui?.last_page ?? 'system')
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
        {#if load}
          {#await load() then module}
            {@const Page = module.default}
            <Page />
          {/await}
        {:else}
          <Soon page={current} />
        {/if}
      {/key}
    {/if}
  </main>

  <TabBar {pages} current={currentId} onSelect={select} />
</div>

<Keyboard />
<ConfirmDialog />
<Toast />
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
