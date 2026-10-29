<script lang="ts">
  import type { Snippet } from 'svelte'

  let { title, children }: { title: string; children?: Snippet } = $props()
</script>

<header class="head">
  <h1>{title}</h1>
  {#if children}<div class="extra">{@render children()}</div>{/if}
</header>

<style>
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 14px;
  }
  h1 {
    font-size: var(--fs-title);
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  .extra {
    display: flex;
    align-items: center;
    gap: 10px;
  }
</style>
