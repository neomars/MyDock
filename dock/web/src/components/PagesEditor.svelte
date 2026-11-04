<script lang="ts">
  import ArrowDown from '@lucide/svelte/icons/arrow-down'
  import ArrowUp from '@lucide/svelte/icons/arrow-up'
  import Check from '@lucide/svelte/icons/check'
  import Eye from '@lucide/svelte/icons/eye'
  import EyeOff from '@lucide/svelte/icons/eye-off'
  import RotateCcw from '@lucide/svelte/icons/rotate-ccw'
  import { movePage, pageRows, toggleHidden } from '../lib/pages'
  import { pagesUi } from '../lib/pagesEditor.svelte'
  import type { PageId } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'

  const order = $derived(sock.ui?.order ?? [])
  const hidden = $derived(sock.ui?.hidden ?? [])
  const rows = $derived(pageRows(order, hidden))
  const shown = $derived(rows.filter((r) => r.visible).length)
  const pristine = $derived(order.length === 0 && hidden.length === 0)

  async function save(patch: { order?: PageId[]; hidden?: PageId[] }): Promise<void> {
    const ack = await sock.act('dock.ui.set', patch)
    if (!ack.ok) notify(ack.e?.msg ?? 'Modification impossible.', 'bad', 6_000)
  }
</script>

{#if pagesUi.open}
  <div class="overlay" role="dialog" aria-modal="true" aria-label="Pages du dock et leur ordre" data-testid="pages-editor">
    <header>
      <h1>Pages du dock</h1>
      <div class="actions">
        <button class="btn" data-testid="pages-reset" disabled={pristine} onclick={() => save({ order: [], hidden: [] })}>
          <RotateCcw size={32} aria-hidden="true" />Ordre d'origine
        </button>
        <button class="btn primary" data-testid="pages-done" onclick={() => pagesUi.close()}>
          <Check size={34} aria-hidden="true" />Terminé
        </button>
      </div>
    </header>

    <section class="card">
      <p class="muted" data-testid="pages-summary">
        {shown} page{shown > 1 ? 's' : ''} visible{shown > 1 ? 's' : ''} sur {rows.length}
      </p>
      <div class="list">
        {#each rows as row, index (row.page.id)}
          {@const Icon = row.page.icon}
          {@const locked = row.page.id === 'settings'}
          <div class="row" class:off={!row.visible} data-testid="page-row-{row.page.id}">
            <span class="rank dim">{index + 1}</span>
            <Icon size={36} aria-hidden="true" />
            <span class="who">
              <strong>{row.page.tab}</strong>
              {#if locked}<small>toujours visible</small>{:else if !row.visible}<small>cachée</small>{/if}
            </span>
            <button
              class="step"
              aria-label="Monter {row.page.tab}"
              data-testid="page-up-{row.page.id}"
              disabled={index === 0}
              onclick={() => save({ order: movePage(order, hidden, row.page.id, -1) })}
            >
              <ArrowUp size={34} aria-hidden="true" />
            </button>
            <button
              class="step"
              aria-label="Descendre {row.page.tab}"
              data-testid="page-down-{row.page.id}"
              disabled={index === rows.length - 1}
              onclick={() => save({ order: movePage(order, hidden, row.page.id, 1) })}
            >
              <ArrowDown size={34} aria-hidden="true" />
            </button>
            <button
              class="step eye"
              class:on={row.visible}
              aria-pressed={row.visible}
              aria-label="{row.visible ? 'Cacher' : 'Montrer'} {row.page.tab}"
              data-testid="page-toggle-{row.page.id}"
              disabled={locked}
              onclick={() => save({ hidden: toggleHidden(hidden, row.page.id) })}
            >
              {#if row.visible}<Eye size={34} aria-hidden="true" />{:else}<EyeOff size={34} aria-hidden="true" />{/if}
            </button>
          </div>
        {/each}
      </div>
    </section>
  </div>
{/if}

<style>
  /* Plein écran sous la barre du haut ; une seule colonne qui défile (texte doublé). */
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
  .actions {
    display: flex;
    gap: 10px;
  }
  .card {
    flex: 1 1 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 8px 16px;
  }
  .card > p {
    flex: none;
    font-size: var(--fs-cap);
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
  .row.off .who strong,
  .row.off :global(svg:first-of-type) {
    color: var(--text-3);
  }
  .rank {
    flex: none;
    width: 28px;
    font-size: var(--fs-cap);
    font-weight: 600;
    text-align: right;
  }
  .who {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    line-height: 1.1;
  }
  .who strong {
    font-size: var(--fs-small);
    font-weight: 600;
  }
  .who small {
    font-size: calc(13px * var(--ts));
    color: var(--text-3);
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
  .step.eye.on {
    background: var(--accent-soft);
    color: var(--accent);
    box-shadow: inset 0 0 0 2px var(--accent);
  }
</style>
