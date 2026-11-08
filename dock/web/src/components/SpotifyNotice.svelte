<script lang="ts">
  import type { Component, Snippet } from 'svelte'

  // Une carte d'état au milieu de la page : chargement, vide, erreur, ou comment connecter le compte.
  let {
    icon: Icon,
    title,
    testid,
    tone = 'plain',
    children,
  }: {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    icon: Component<any>
    title: string
    testid: string
    tone?: 'plain' | 'bad'
    children?: Snippet
  } = $props()
</script>

<section class="notice" class:bad={tone === 'bad'} data-testid={testid}>
  <Icon size={56} aria-hidden="true" />
  <h2>{title}</h2>
  {#if children}<div class="body">{@render children()}</div>{/if}
</section>

<style>
  .notice {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 320px;
    padding: 12px 8px;
    text-align: center;
    color: var(--text-3);
  }
  .notice.bad {
    color: var(--warn);
  }
  h2 {
    font-size: var(--fs-large);
    font-weight: 700;
    color: var(--text);
  }
  .body {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    max-width: 720px;
    font-size: var(--fs-small);
    color: var(--text-2);
  }
</style>
