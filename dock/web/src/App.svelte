<script lang="ts">
  import type { Component } from 'svelte'
  import { onMount } from 'svelte'
  import { fade, fly } from 'svelte/transition'
  import AppPicker from './components/AppPicker.svelte'
  import ConfirmDialog from './components/ConfirmDialog.svelte'
  import DebugOverlay from './components/DebugOverlay.svelte'
  import HomeScreen from './components/HomeScreen.svelte'
  import Keyboard from './components/Keyboard.svelte'
  import LazyPage from './components/LazyPage.svelte'
  import MarketsEditor from './components/MarketsEditor.svelte'
  import OfflineScreen from './components/OfflineScreen.svelte'
  import PagesEditor from './components/PagesEditor.svelte'
  import SaverSources from './components/SaverSources.svelte'
  import ScreenSleep from './components/ScreenSleep.svelte'
  import Toast from './components/Toast.svelte'
  import TopBar from './components/TopBar.svelte'
  import { takeReopen } from './lib/build'
  import { playerActive } from './lib/media'
  import { PAGES, pageById } from './lib/pages'
  import { justPaired } from './lib/pairing'
  import type { PageId } from './lib/protocol'
  import { sock } from './lib/socket.svelte'
  import { swipeRight } from './lib/swipe'
  import { notify } from './lib/toast.svelte'
  import { wakeDone } from './lib/wake.svelte'

  const debug = new URLSearchParams(location.search).has('debug')

  // Une page par bouton de l'accueil : le compilateur refuse qu'il en manque une.
  const loaders: Record<PageId, () => Promise<{ default: Component }>> = {
    music: () => import('./pages/Music.svelte'),
    sound: () => import('./pages/Sound.svelte'),
    apps: () => import('./pages/Apps.svelte'),
    steam: () => import('./pages/Steam.svelte'),
    office: () => import('./pages/Office.svelte'),
    markets: () => import('./pages/Markets.svelte'),
    system: () => import('./pages/System.svelte'),
    media: () => import('./pages/Media.svelte'),
    power: () => import('./pages/Power.svelte'),
    settings: () => import('./pages/Settings.svelte'),
  }

  // Le menu principal (l'accueil, des gros boutons) ou une page. On revient à l'accueil par le bouton de la barre du
  // haut ou par un balayage vers la droite ; on y démarre toujours (un dock qui redémarre n'ouvre pas une page au
  // hasard), sauf juste après le rechargement qui répare l'écran d'une mise à jour : la page touchée se rouvre.
  function firstView(): 'home' | PageId {
    const id = takeReopen()
    return PAGES.find((page) => page.id === id)?.id ?? 'home'
  }
  let view = $state<'home' | PageId>(firstView())
  const current = $derived(view === 'home' ? null : pageById(view))

  function open(id: PageId): void {
    view = id
  }

  function goHome(): void {
    view = 'home'
  }

  // Les glissements des pages (de 0 à 190 ms) : aucun si la personne a demandé moins de mouvement.
  const motion = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1

  // La barre du haut montre toujours le nom du PC (« host ») et le mini-lecteur (« music ») : on s'abonne
  // à ces deux thèmes en permanence ; les pages suivent les autres (system, procs…) tant qu'elles sont ouvertes.
  onMount(() => {
    sock.connect()
    return sock.subscribe(['host', 'music'])
  })

  // Pendant une vidéo (VLC en plein écran), un toucher ramène le dock devant : on ouvre alors la télécommande, quelle que
  // soit la page affichée, au lieu de montrer ce qui était là avant la vidéo.
  let wasFront = sock.player?.front
  $effect(() => {
    const front = sock.player?.front
    const active = playerActive(sock.player)
    if (wasFront === 'video' && front === 'dock' && active) open('media')
    wasFront = front
  })

  // Quand le PC répond enfin, le « réveil en cours » est terminé.
  $effect(() => {
    if (sock.connected) wakeDone()
  })

  // Un PC vient de s'associer (code tapé sur le PC) : on le dit une fois, quelle que soit la page affichée.
  let pairedSeen = 0
  $effect(() => {
    const last = sock.dock?.pairing?.last
    const text = justPaired(last, Date.now(), pairedSeen)
    if (last?.result === 'paired') pairedSeen = last.at
    if (text) notify(text, 'good', 6_000)
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
  <TopBar onOpenMusic={() => open('music')} showHome={view !== 'home'} onHome={goHome} onPower={() => open('power')} />

  <div class="stage">
    {#if dockLost}
      <section class="layer lost" data-testid="dock-lost">
        <h1>Dock injoignable</h1>
        <p class="muted">Le serveur du dock ne répond plus. Reconnexion automatique…</p>
      </section>
    {:else if !sock.link}
      <!-- démarrage : les premiers instantanés arrivent en quelques millisecondes -->
    {:else if view === 'home' || !current}
      <div class="layer" in:fly={{ x: -60, duration: 170 * motion }} out:fade={{ duration: 120 * motion }}>
        <HomeScreen onOpen={open} />
      </div>
    {:else}
      <main
        class="layer page"
        data-testid="page-{view}"
        use:swipeRight={goHome}
        in:fly={{ x: 90, duration: 190 * motion }}
        out:fly={{ x: 90, duration: 160 * motion }}
      >
        {#if current.needsPc && !sock.connected}
          <OfflineScreen onSettings={() => open('settings')} />
        {:else}
          {#key view}
            <LazyPage load={loaders[view]} page={view} />
          {/key}
        {/if}
      </main>
    {/if}
  </div>
</div>

<AppPicker />
<MarketsEditor />
<PagesEditor />
<SaverSources />
<Keyboard />
<ConfirmDialog />
<Toast />
<ScreenSleep />
{#if debug}<DebugOverlay />{/if}

<style>
  .app {
    display: grid;
    /* « minmax(0, 1fr) » et non « auto » : la colonne ne s'élargit jamais pour son contenu (la barre du haut, une page). */
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: var(--topbar) minmax(0, 1fr);
    height: 100%;
  }
  /* L'accueil et la page se partagent la même place : pendant un glissement, les deux sont là. */
  .stage {
    position: relative;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
    overflow: hidden;
  }
  .layer {
    grid-area: 1 / 1;
    min-width: 0;
    min-height: 0;
  }
  .page {
    overflow-y: auto;
    overflow-x: hidden;
    padding: 14px 20px 16px;
    overscroll-behavior: contain;
    /* Le défilement vertical reste au navigateur ; le balayage vers la droite est à nous (voir lib/swipe.ts). */
    touch-action: pan-y;
  }
  .lost {
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
