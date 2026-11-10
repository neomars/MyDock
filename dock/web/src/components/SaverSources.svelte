<script lang="ts">
  import Check from '@lucide/svelte/icons/check'
  import Pause from '@lucide/svelte/icons/pause'
  import Play from '@lucide/svelte/icons/play'
  import Plus from '@lucide/svelte/icons/plus'
  import Trash2 from '@lucide/svelte/icons/trash-2'
  import { editText } from '../lib/keyboard.svelte'
  import { aerialSummary, appleRootNote, cleanSourceUrl, sourceStatus, type AerialSource } from '../lib/saver'
  import { saver } from '../lib/saver.svelte'
  import { saverSources } from '../lib/saverSources.svelte'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'

  // La fenêtre des vidéos de l'économiseur : d'où le dock les télécharge (les vues aériennes d'Apple et les adresses que
  // tu saisis ici au clavier tactile), où ça en est, et de quoi retirer une source avec ses films. Le téléchargement et la
  // conversion se font tout seuls, en tâche de fond, sur le Pi.

  const status = $derived(saver.aerial)
  const apple = $derived(sock.ui?.saver_apple ?? true)
  const addresses = $derived(sock.ui?.saver_sources ?? [])
  /** La source dont on demande de confirmer le retrait (ses films seront effacés). */
  let confirming = $state<string | null>(null)

  // Tant que la fenêtre est ouverte, on suit l'avancement.
  $effect(() => {
    if (!saverSources.open) return
    void saver.refresh()
    const timer = setInterval(() => void saver.refresh(), 4_000)
    return () => {
      clearInterval(timer)
      confirming = null
    }
  })

  async function setUi(patch: Record<string, unknown>): Promise<boolean> {
    const ack = await sock.act('dock.ui.set', patch)
    if (!ack.ok) notify(ack.e?.msg ?? 'La modification a échoué.', 'bad', 8_000)
    void saver.refresh()
    return ack.ok
  }

  async function addAddress(): Promise<void> {
    const typed = await editText({
      title: 'Adresse de la vidéo',
      hint: 'https://… : une vidéo (MP4, WebM, MOV…) ou un fichier texte qui en liste (.txt)',
      kind: 'url',
      value: 'https://',
    })
    if (typed === null) return
    const url = cleanSourceUrl(typed)
    if (!url) {
      notify('Il faut une adresse complète, qui commence par https://', 'bad', 6_000)
      return
    }
    if (addresses.includes(url)) {
      notify('Cette adresse est déjà dans la liste.', 'info', 4_000)
      return
    }
    if (await setUi({ saver_sources: [...addresses, url] })) notify('Adresse ajoutée : le téléchargement démarre.', 'good', 4_000)
  }

  async function forget(source: string): Promise<void> {
    confirming = null
    const ack = await sock.act('dock.saver.forget', { source }, { timeoutMs: 25_000 })
    if (!ack.ok) notify(ack.e?.msg ?? 'Impossible de retirer cette source.', 'bad', 6_000)
    void saver.refresh()
  }

  const rows = $derived<AerialSource[]>(status?.sources ?? [])
  const nothing = $derived(!apple && addresses.length === 0)
  const rootNote = $derived(appleRootNote(status?.apple_root))
</script>

{#if saverSources.open}
  <div class="overlay" role="dialog" aria-modal="true" aria-label="Vidéos de l'économiseur d'écran" data-testid="saver-sources">
    <header>
      <h1>Vidéos de l'économiseur</h1>
      <button class="btn primary" data-testid="saver-sources-done" onclick={() => saverSources.close()}>
        <Check size={34} aria-hidden="true" />Terminé
      </button>
    </header>

    <section class="card">
      <p class="summary" data-testid="saver-sources-summary">{status ? aerialSummary(status, { detail: false }) : 'Lecture de l’état…'}</p>

      <div class="list">
        {#each rows as row, index (row.id)}
          <div class="row" data-testid="saver-src-{index}">
            <span class="who">
              <strong>{row.label}</strong>
              <small class:bad={row.error} data-testid="saver-src-{index}-status">{sourceStatus(row)}</small>
            </span>
            {#if confirming === row.id}
              <button class="btn" data-testid="saver-src-cancel-{index}" onclick={() => (confirming = null)}>Garder</button>
              <button class="btn danger" data-testid="saver-src-confirm-{index}" onclick={() => forget(row.id)}>Effacer</button>
            {:else}
              <button
                class="step"
                aria-label="Retirer {row.label} et effacer ses films"
                data-testid="saver-src-remove-{index}"
                onclick={() => (confirming = row.id)}
              >
                <Trash2 size={34} aria-hidden="true" />
              </button>
            {/if}
          </div>
        {/each}
        {#if rows.length === 0 && status}
          <p class="dim empty" data-testid="saver-src-none">Aucune source : l'économiseur ne montre que ses scènes animées.</p>
        {/if}
      </div>

      <div class="foot">
        <button class="btn" data-testid="saver-add-url" onclick={addAddress}>
          <Plus size={32} aria-hidden="true" />Ajouter une adresse…
        </button>
        {#if !apple}
          <button class="btn" data-testid="saver-add-apple" onclick={() => setUi({ saver_apple: true })}>
            <Plus size={32} aria-hidden="true" />Vues aériennes d'Apple
          </button>
        {/if}
        {#if status && !nothing}
          <button class="btn" data-testid="saver-aerial-toggle" onclick={() => setUi({ saver_aerial: !status.enabled })}>
            {#if status.enabled}<Pause size={30} aria-hidden="true" />Suspendre{:else}<Play size={30} aria-hidden="true" />Reprendre{/if}
          </button>
        {/if}
      </div>
      <p class="dim small hint">
        Une adresse https d'une vidéo, ou d'un fichier texte (.txt) qui en liste une par ligne. Le Pi télécharge, convertit si
        besoin puis range les films : l'économiseur les montre dès qu'ils sont prêts.
      </p>
      {#if rootNote}
        <p class="dim small hint" data-testid="saver-apple-root">{rootNote}</p>
      {/if}
    </section>
  </div>
{/if}

<style>
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
  h1 {
    font-size: var(--fs);
    font-weight: 700;
    white-space: nowrap;
  }
  .card {
    flex: 1 1 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px 16px;
    overflow-y: auto; /* texte doublé : toute la carte défile, rien n'est écrasé */
    overscroll-behavior: contain;
  }
  .summary {
    flex: none;
    font-size: var(--fs-cap);
    color: var(--text-2);
  }
  .list {
    flex: none;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 84px;
    border-top: 1px solid var(--line);
  }
  .row:first-child {
    border-top: 0;
  }
  .who {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    line-height: 1.1;
  }
  .who strong,
  .who small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .who strong {
    font-size: var(--fs-small);
    font-weight: 600;
  }
  .who small {
    font-size: calc(13px * var(--ts));
    color: var(--text-3);
  }
  .who small.bad {
    color: var(--warn);
    white-space: normal; /* le message d'échec se lit en entier : il dit ce qui manque */
    overflow: visible;
    overflow-wrap: anywhere;
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
  .step:active {
    background: var(--line);
  }
  .empty {
    padding: 12px 0;
  }
  .foot {
    flex: none;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .hint {
    flex: none;
  }
</style>
