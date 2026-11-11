<script lang="ts">
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import Search from '@lucide/svelte/icons/search'
  import SearchX from '@lucide/svelte/icons/search-x'
  import { onMount } from 'svelte'
  import { editText } from '../lib/keyboard.svelte'
  import { channelCount, filterChannels, playerBlocker } from '../lib/media'
  import { media } from '../lib/media.svelte'
  import { sock } from '../lib/socket.svelte'

  // Freetv : les chaînes de la Freebox. Un toucher lance la chaîne en plein écran sur VLC ; la liste vient du dock.
  let { onPlayed }: { onPlayed: () => void } = $props()

  onMount(() => {
    if (media.tv === 'idle' || media.tv === 'error') void media.loadChannels()
  })

  const shown = $derived(filterChannels(media.channels, media.query))
  const hd = $derived(sock.ui?.tv_hd ?? false)
  const blocker = $derived(playerBlocker(sock.player))
  let starting = $state<string | null>(null)

  async function search(): Promise<void> {
    const typed = await editText({ title: 'Chercher une chaîne', hint: 'un nom ou un numéro', kind: 'text', value: media.query })
    if (typed !== null) media.query = typed.trim()
  }

  async function play(id: string): Promise<void> {
    if (starting) return
    starting = id
    try {
      if (await media.playTv(id)) onPlayed()
    } finally {
      starting = null
    }
  }
</script>

<div class="bar">
  <button class="btn search" data-testid="tv-search" onclick={search}>
    <Search size={30} aria-hidden="true" />
    <span class="q" class:dim={!media.query}>{media.query || 'Chercher une chaîne…'}</span>
  </button>
  {#if media.query}
    <button class="btn icon" aria-label="Effacer la recherche" data-testid="tv-clear" onclick={() => (media.query = '')}>
      <SearchX size={30} aria-hidden="true" />
    </button>
  {/if}
  <button class="btn toggle" class:on={hd} aria-pressed={hd} data-testid="tv-hd" onclick={() => media.setHd(!hd)}>HD</button>
  <button class="btn icon" aria-label="Relire la liste des chaînes" data-testid="tv-refresh" disabled={media.tv === 'loading'} onclick={() => media.loadChannels(true)}>
    <RefreshCw size={30} aria-hidden="true" />
  </button>
</div>

{#if blocker}
  <p class="notice warn" data-testid="tv-blocker">{blocker}</p>
{/if}
{#if media.tvWarning}
  <p class="notice warn" data-testid="tv-warning">{media.tvWarning}</p>
{/if}

{#if media.tv === 'loading' && media.channels.length === 0}
  <p class="state dim" data-testid="tv-loading">Lecture de la liste des chaînes de la Freebox…</p>
{:else if media.tv === 'error'}
  <section class="state problem" data-testid="tv-error">
    <h2>Les chaînes ne sont pas disponibles</h2>
    <p>{media.tvError}</p>
    <p class="dim">Le Pi doit être sur le réseau de la Freebox. L'adresse de la liste se règle dans Réglages → Divertissement.</p>
    <button class="btn primary" data-testid="tv-retry" onclick={() => media.loadChannels(true)}>
      <RefreshCw size={28} aria-hidden="true" />Réessayer
    </button>
  </section>
{:else}
  <p class="count dim" data-testid="tv-count">
    {channelCount(shown.length)}{media.query ? ` pour « ${media.query} »` : ''}{hd ? ' · en HD quand elle existe' : ''}
  </p>
  <ul class="list" data-testid="tv-list">
    {#each shown as channel (channel.id)}
      <li>
        <button class="row" class:busy={starting === channel.id} disabled={starting !== null} data-testid="tv-ch-{channel.id}" onclick={() => play(channel.id)}>
          <span class="num mono">{channel.number ?? '·'}</span>
          <span class="name">{channel.name}</span>
          {#if channel.qualities.includes('hd')}<span class="chip">HD</span>{/if}
          {#if starting === channel.id}<span class="chip accent">Lancement…</span>{/if}
        </button>
      </li>
    {:else}
      <li class="none dim" data-testid="tv-none">Aucune chaîne ne correspond.</li>
    {/each}
  </ul>
{/if}

<style>
  .bar {
    display: flex;
    gap: 10px;
    margin-bottom: 10px;
  }
  .search {
    flex: 1 1 0;
    justify-content: flex-start;
    min-width: 0;
  }
  .q {
    overflow: hidden;
    text-overflow: ellipsis;
    font-weight: 500;
  }
  .btn.toggle {
    min-width: 96px;
  }
  .btn.toggle.on {
    background: var(--accent);
    color: var(--accent-ink);
  }
  .notice {
    margin-bottom: 8px;
    font-size: var(--fs-small);
  }
  .notice.warn {
    color: var(--warn);
  }
  .count {
    font-size: var(--fs-cap);
    margin-bottom: 8px;
  }
  .state {
    padding: 24px 4px;
    font-size: var(--fs-small);
  }
  .state.problem {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  .state.problem h2 {
    color: var(--warn);
    font-size: var(--fs);
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    min-height: 76px;
    padding: 0 16px;
    border-radius: 14px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    text-align: left;
  }
  .row:active:not(:disabled) {
    background: var(--surface-3);
  }
  .row.busy {
    border-color: var(--accent);
  }
  .num {
    flex: none;
    min-width: 3ch;
    text-align: right;
    color: var(--accent);
    font-size: var(--fs-small);
    font-weight: 700;
  }
  .name {
    flex: 1 1 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-small);
    font-weight: 600;
  }
  .none {
    padding: 20px 4px;
  }
</style>
