<script lang="ts">
  import type { PageInfo } from '../lib/pages'
  import type { PageId } from '../lib/protocol'

  let {
    pages,
    current,
    onSelect,
  }: { pages: PageInfo[]; current: PageId; onSelect: (id: PageId) => void } = $props()
</script>

<div class="tabs" role="tablist" aria-label="Pages">
  {#each pages as page (page.id)}
    {@const Icon = page.icon}
    <button
      class="tab"
      role="tab"
      aria-selected={page.id === current}
      data-testid="tab-{page.id}"
      onclick={() => onSelect(page.id)}
    >
      <Icon size={28} strokeWidth={2} aria-hidden="true" />
      <span>{page.tab}</span>
    </button>
  {/each}
</div>

<style>
  .tabs {
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: 1fr;
    height: var(--tabbar);
    background: var(--surface);
    border-top: 1px solid var(--line);
  }
  .tab {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 5px;
    min-width: 0;
    padding: 0 2px;
    color: var(--text-2);
    font-size: var(--fs-cap);
    font-weight: 600;
    text-align: center;
  }
  .tab span {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tab:active {
    background: var(--surface-2);
  }
  .tab[aria-selected='true'] {
    color: var(--accent);
    background: var(--accent-soft);
    box-shadow: inset 0 3px 0 var(--accent);
  }
</style>
