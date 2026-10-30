<script lang="ts">
  import FileText from '@lucide/svelte/icons/file-text'
  import { onMount } from 'svelte'
  import { clock } from '../lib/clock.svelte'
  import { formatAgo } from '../lib/format'
  import { launch } from '../lib/launch.svelte'
  import { kindIcon, kindLabel } from '../lib/launcher'
  import type { OfficeData, RecentDocument } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'

  onMount(() => sock.subscribe(['office']))

  const data = $derived(sock.topics.office as OfficeData | undefined)
  const now = $derived(clock.now.getTime() / 1_000)

  const MODULES = [
    { kind: 'writer', title: 'Nouveau texte', program: 'Writer' },
    { kind: 'calc', title: 'Nouveau tableur', program: 'Calc' },
    { kind: 'impress', title: 'Nouvelle présentation', program: 'Impress' },
  ] as const

  function create(kind: string, title: string): void {
    void launch.run(`new:${kind}`, 'office.new', { kind }, title)
  }

  function reopen(doc: RecentDocument): void {
    void launch.run(`doc:${doc.id}`, 'office.open', { id: doc.id }, doc.name)
  }
</script>

{#if data?.unavailable}
  <section class="empty" data-testid="office-unavailable">
    <FileText size={64} aria-hidden="true" />
    <h1>LibreOffice indisponible</h1>
    <p class="muted">{data.unavailable}</p>
  </section>
{:else if !data}
  <p class="muted" data-testid="office-loading">Lecture des documents récents…</p>
{:else}
  <div class="layout">
    <div class="modules" data-testid="office-new">
      {#each MODULES as module (module.kind)}
        {@const Icon = kindIcon(module.kind)}
        {@const busy = launch.isBusy(`new:${module.kind}`)}
        <button class="module {module.kind}" class:busy data-testid="new-{module.kind}" onclick={() => create(module.kind, `LibreOffice ${module.program}`)}>
          <Icon size={40} aria-hidden="true" />
          <span class="text">
            <strong>{module.title}</strong>
            <small>{busy ? 'Lancement…' : `LibreOffice ${module.program}`}</small>
          </span>
        </button>
      {/each}
    </div>

    <section class="card recent" data-testid="office-recent">
      <h2>Documents récents{data.recent.length ? ` (${data.recent.length})` : ''}</h2>
      <div class="list">
        {#each data.recent as doc (doc.id)}
          {@const Icon = kindIcon(doc.kind)}
          {@const busy = launch.isBusy(`doc:${doc.id}`)}
          <button class="doc {doc.kind}" class:busy aria-label="Ouvrir {doc.name}" data-testid="recent-{doc.id}" onclick={() => reopen(doc)}>
            <Icon size={30} aria-hidden="true" />
            <span class="text">
              <strong data-testid="recent-name-{doc.id}">{doc.name}</strong>
              <small>{kindLabel(doc.kind)} · {doc.where}</small>
            </span>
            <span class="when dim">{busy ? 'Ouverture…' : doc.modified !== null ? formatAgo(doc.modified, now) : ''}</span>
          </button>
        {/each}
        {#if data.recent.length === 0}
          <p class="muted none" data-testid="office-none">Aucun document récent : ouvre un document dans LibreOffice, il apparaîtra ici.</p>
        {/if}
      </div>
    </section>
  </div>
{/if}

<style>
  .layout {
    display: grid;
    grid-template-columns: 330px minmax(0, 1fr);
    gap: 12px;
    height: 100%;
    min-height: 0;
  }
  .modules {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .module {
    flex: 1 1 0;
    display: flex;
    align-items: center;
    gap: 16px;
    min-height: 96px;
    padding: 0 18px;
    border-radius: var(--radius);
    background: var(--surface);
    border: 1px solid var(--line);
    text-align: left;
  }
  .module:active,
  .doc:active {
    background: var(--surface-3);
    transform: scale(0.98);
  }
  .module.busy,
  .doc.busy {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .module.writer :global(svg),
  .doc.writer :global(svg) {
    color: var(--accent);
  }
  .module.calc :global(svg),
  .doc.calc :global(svg) {
    color: var(--good);
  }
  .module.impress :global(svg),
  .doc.impress :global(svg) {
    color: var(--warn);
  }
  .doc.draw :global(svg),
  .doc.other :global(svg) {
    color: var(--text-2);
  }
  .text {
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.25;
  }
  .text strong {
    font-size: var(--fs);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .text small {
    font-size: var(--fs-cap);
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .recent {
    display: flex;
    flex-direction: column;
    min-height: 0;
    padding: 12px 14px 6px;
  }
  .recent > h2 {
    margin-bottom: 6px;
  }
  .list {
    flex: 1 1 0;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .doc {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    min-height: 64px;
    padding: 0 8px;
    border: 1px solid transparent;
    border-top-color: var(--line);
    border-radius: var(--radius-s);
    text-align: left;
  }
  .doc:first-child {
    border-top-color: transparent;
  }
  .doc .text {
    flex: 1 1 0;
  }
  .when {
    flex: none;
    font-size: var(--fs-cap);
    white-space: nowrap;
  }
  .none {
    padding: 10px 2px;
  }
  .empty {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    text-align: center;
    color: var(--text-3);
  }
  .empty h1 {
    font-size: var(--fs-title);
    color: var(--text);
  }
  .empty p {
    max-width: 600px;
  }
</style>
