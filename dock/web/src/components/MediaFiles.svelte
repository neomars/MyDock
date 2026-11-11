<script lang="ts">
  import CornerLeftUp from '@lucide/svelte/icons/corner-left-up'
  import Film from '@lucide/svelte/icons/film'
  import Folder from '@lucide/svelte/icons/folder'
  import HardDrive from '@lucide/svelte/icons/hard-drive'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import { onMount } from 'svelte'
  import { fileSize, playerBlocker, whereText } from '../lib/media'
  import { media } from '../lib/media.svelte'
  import { sock } from '../lib/socket.svelte'

  // Kiosque : les partages Samba de la Freebox. Un dossier s'ouvre, une vidéo se lance en plein écran sur VLC.
  let { onPlayed }: { onPlayed: () => void } = $props()

  onMount(() => {
    if (media.files === 'idle' || (media.share === null && media.files !== 'loading')) void media.loadShares()
  })

  const blocker = $derived(playerBlocker(sock.player))
  const host = $derived(sock.dock?.media?.smb_host ?? '')
  let starting = $state<string | null>(null)

  async function play(name: string): Promise<void> {
    if (starting) return
    starting = name
    try {
      if (await media.playFile(name)) onPlayed()
    } finally {
      starting = null
    }
  }
</script>

<div class="bar">
  <button class="btn up" disabled={media.share === null || media.files === 'loading'} data-testid="files-up" onclick={() => media.up()}>
    <CornerLeftUp size={30} aria-hidden="true" />{media.share === null ? 'Partages' : 'Dossier parent'}
  </button>
  <p class="where" data-testid="files-where">{whereText(media.share, media.path)}</p>
  <button class="btn icon" aria-label="Relire" data-testid="files-refresh" disabled={media.files === 'loading'} onclick={() => media.retryFiles()}>
    <RefreshCw size={30} aria-hidden="true" />
  </button>
</div>

{#if blocker}
  <p class="notice warn" data-testid="files-blocker">{blocker}</p>
{/if}

{#if media.files === 'loading'}
  <p class="state dim" data-testid="files-loading">{media.share === null ? `Connexion à ${host || 'au serveur de fichiers'}…` : 'Lecture du dossier…'}</p>
{:else if media.files === 'error'}
  <section class="state problem" data-testid="files-error">
    <h2>Le partage n'est pas lisible</h2>
    <p>{media.filesError}</p>
    <p class="dim">Le serveur de fichiers se règle dans Réglages → Divertissement{host ? ` (actuellement : ${host})` : ''}.</p>
    <button class="btn primary" data-testid="files-retry" onclick={() => media.retryFiles()}>
      <RefreshCw size={28} aria-hidden="true" />Réessayer
    </button>
  </section>
{:else if media.share === null}
  <ul class="list" data-testid="files-shares">
    {#each media.shares as share (share)}
      <li>
        <button class="row" data-testid="share-{share}" onclick={() => media.open(share, '')}>
          <HardDrive size={36} aria-hidden="true" /><span class="name">{share}</span>
        </button>
      </li>
    {:else}
      <li class="none dim" data-testid="files-no-share">Aucun partage visible sur ce serveur.</li>
    {/each}
  </ul>
{:else}
  <ul class="list" data-testid="files-list">
    {#each media.entries as entry (entry.name)}
      <li>
        {#if entry.dir}
          <button class="row dir" data-testid="dir-{entry.name}" onclick={() => media.enter(entry.name)}>
            <Folder size={36} aria-hidden="true" /><span class="name">{entry.name}</span>
          </button>
        {:else}
          <button class="row" class:busy={starting === entry.name} disabled={starting !== null} data-testid="video-{entry.name}" onclick={() => play(entry.name)}>
            <Film size={36} aria-hidden="true" /><span class="name">{entry.name}</span>
            {#if starting === entry.name}<span class="chip accent">Lancement…</span>{:else}<span class="size dim mono">{fileSize(entry.size)}</span>{/if}
          </button>
        {/if}
      </li>
    {:else}
      <li class="none dim" data-testid="files-empty">Dossier vide : seuls les dossiers et les vidéos s'affichent.</li>
    {/each}
  </ul>
{/if}

<style>
  .bar {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }
  .where {
    flex: 1 1 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-2);
    font-size: var(--fs-small);
  }
  .notice {
    margin-bottom: 8px;
    font-size: var(--fs-small);
  }
  .notice.warn {
    color: var(--warn);
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
  .row.dir {
    color: var(--accent);
  }
  .row.busy {
    border-color: var(--accent);
  }
  .name {
    flex: 1 1 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-small);
    font-weight: 600;
    color: var(--text);
  }
  .size {
    flex: none;
    font-size: var(--fs-cap);
  }
  .none {
    padding: 20px 4px;
  }
</style>
