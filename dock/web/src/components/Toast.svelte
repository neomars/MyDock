<script lang="ts">
  import CircleAlert from '@lucide/svelte/icons/circle-alert'
  import CircleCheck from '@lucide/svelte/icons/circle-check'
  import Info from '@lucide/svelte/icons/info'
  import { toast } from '../lib/toast.svelte'

  const Icon = $derived(toast.kind === 'good' ? CircleCheck : toast.kind === 'bad' ? CircleAlert : Info)
</script>

{#if toast.visible}
  <div class="toast {toast.kind}" role="status" data-testid="toast">
    <Icon size={26} aria-hidden="true" />
    <span>{toast.text}</span>
  </div>
{/if}

<style>
  .toast {
    position: fixed;
    top: calc(var(--topbar) + 12px);
    left: 50%;
    transform: translateX(-50%);
    z-index: 50;
    display: flex;
    align-items: center;
    gap: 12px;
    max-width: 880px;
    padding: 14px 24px;
    border-radius: 999px;
    background: var(--surface-3);
    border: 1px solid var(--line);
    font-weight: 600;
    pointer-events: none;
  }
  .good {
    background: var(--good-soft);
    border-color: var(--good);
    color: var(--good);
  }
  .bad {
    background: var(--bad-soft);
    border-color: var(--bad);
    color: var(--bad);
  }
  span {
    color: var(--text);
  }
</style>
