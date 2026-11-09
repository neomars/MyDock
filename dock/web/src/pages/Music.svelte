<script lang="ts">
  import Disc3 from '@lucide/svelte/icons/disc-3'
  import Library from '@lucide/svelte/icons/library'
  import MonitorSpeaker from '@lucide/svelte/icons/monitor-speaker'
  import Search from '@lucide/svelte/icons/search'
  import type { Component } from 'svelte'
  import { onMount } from 'svelte'
  import SpotifyDetail from '../components/SpotifyDetail.svelte'
  import SpotifyDevices from '../components/SpotifyDevices.svelte'
  import SpotifyLibrary from '../components/SpotifyLibrary.svelte'
  import SpotifyPlayer from '../components/SpotifyPlayer.svelte'
  import SpotifySearch from '../components/SpotifySearch.svelte'
  import { spotify, type Tab } from '../lib/spotify.svelte'

  // La page Spotify : à gauche trois onglets (ce qui joue, rechercher, ta bibliothèque), comme le menu de l'application
  // officielle ; à droite leur contenu. Le mini-lecteur de la barre du haut reste visible sur tous les onglets.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const TABS: { id: Tab; label: string; icon: Component<any> }[] = [
    { id: 'player', label: 'En lecture', icon: Disc3 },
    { id: 'search', label: 'Rechercher', icon: Search },
    { id: 'library', label: 'Ta bibliothèque', icon: Library },
    { id: 'devices', label: 'Sortie sonore', icon: MonitorSpeaker },
  ]

  // On arrive toujours sur ce qui joue ; l'état de Spotify (installé, lancé, compte) se lit une fois à l'ouverture.
  onMount(() => {
    spotify.go('player')
    void spotify.refreshStatus()
  })

  // Où sort la musique (le nom de l'appareil sur le lecteur, son volume) : suivi tant que la page est ouverte, dès que
  // le compte est connu.
  $effect(() => {
    if (!spotify.status?.connected) return
    return spotify.watchDevices()
  })
</script>

<div class="spotify" data-testid="spotify">
  <div class="rail" role="tablist" aria-label="Spotify">
    {#each TABS as tab (tab.id)}
      <button
        class="tab"
        role="tab"
        aria-selected={spotify.tab === tab.id}
        aria-label={tab.label}
        title={tab.label}
        data-testid="sp-tab-{tab.id}"
        onclick={() => spotify.go(tab.id)}
      >
        <tab.icon size={44} aria-hidden="true" />
      </button>
    {/each}
  </div>

  <div class="content" data-tab={spotify.tab}>
    {#if spotify.tab === 'player'}
      <SpotifyPlayer />
    {:else if spotify.opened}
      <SpotifyDetail />
    {:else if spotify.tab === 'search'}
      <SpotifySearch />
    {:else if spotify.tab === 'library'}
      <SpotifyLibrary />
    {:else}
      <SpotifyDevices />
    {/if}
  </div>
</div>

<style>
  /* Le vert de Spotify pour cette page seulement : la barre de progression, le curseur de volume et les boutons
     lisent tous --accent, ils prennent donc la couleur sans rien changer d'autre. */
  .spotify {
    --accent: #1ed760;
    --accent-ink: #04140a;
    --accent-soft: #0f3320;
    display: grid;
    grid-template-columns: 96px minmax(0, 1fr);
    gap: 16px;
    align-items: start;
  }
  .rail {
    position: sticky;
    top: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .tab {
    display: grid;
    place-items: center;
    width: 96px;
    height: 96px;
    border-radius: 24px;
    background: var(--surface-2);
    color: var(--text-2);
  }
  .tab:active {
    background: var(--surface-3);
    transform: scale(0.96);
  }
  .tab[aria-selected='true'] {
    background: var(--accent-soft);
    color: var(--accent);
    box-shadow: inset 0 0 0 2px var(--accent);
  }
  .content {
    min-width: 0;
  }
</style>
