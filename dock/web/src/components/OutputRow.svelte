<script lang="ts">
  import Check from '@lucide/svelte/icons/check'
  import type { Component } from 'svelte'

  // Une ligne de la liste des sorties : l'icône du genre d'appareil, son nom, une ligne d'aide, et à droite sa coche
  // quand c'est la sortie en cours.
  let {
    icon: Icon,
    title,
    subtitle = '',
    active = false,
    busy = false,
    disabled = false,
    testid,
    onTap,
  }: {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    icon: Component<any>
    title: string
    subtitle?: string
    /** C'est la sortie en cours. */
    active?: boolean
    /** Le changement vers cette sortie est demandé. */
    busy?: boolean
    disabled?: boolean
    testid: string
    onTap: () => void
  } = $props()
</script>

<button
  class="row"
  class:active
  class:busy
  {disabled}
  data-testid={testid}
  data-active={active ? 'true' : null}
  aria-pressed={active}
  onclick={onTap}
>
  <span class="icon"><Icon size={44} aria-hidden="true" /></span>
  <span class="text">
    <strong class="name">{title}</strong>
    {#if subtitle}<small class="sub">{subtitle}</small>{/if}
  </span>
  <span class="end">
    {#if busy}
      <small class="starting">Connexion…</small>
    {:else if active}
      <Check size={40} aria-hidden="true" />
    {/if}
  </span>
</button>

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 16px;
    width: 100%;
    min-height: 96px;
    padding: 8px 18px 8px 12px;
    border-radius: var(--radius);
    text-align: left;
  }
  .row:active:not(:disabled) {
    background: var(--surface-3);
  }
  .row.active {
    background: var(--accent-soft);
  }
  .row:disabled {
    opacity: 0.4;
  }
  .icon {
    flex: none;
    display: grid;
    place-items: center;
    width: 72px;
    height: 72px;
    border-radius: 50%;
    background: var(--surface-3);
    color: var(--text-2);
  }
  .active .icon {
    background: var(--accent);
    color: var(--accent-ink);
  }
  .text {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    line-height: 1.2;
  }
  .name {
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .active .name {
    color: var(--accent);
  }
  .sub {
    font-size: var(--fs-cap);
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .end {
    flex: none;
    display: flex;
    align-items: center;
    color: var(--accent);
  }
  .starting {
    font-size: var(--fs-cap);
    font-weight: 600;
  }
</style>
