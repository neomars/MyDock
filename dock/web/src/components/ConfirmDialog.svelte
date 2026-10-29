<script lang="ts">
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import { confirmState, settle } from '../lib/confirm.svelte'
  import { formatCountdown } from '../lib/format'

  let now = $state(Date.now())
  $effect(() => {
    if (!confirmState.open) return
    now = Date.now()
    const timer = setInterval(() => (now = Date.now()), 250)
    return () => clearInterval(timer)
  })

  const remaining = $derived(Math.max(0, confirmState.openedAt + confirmState.ttlMs - now))
</script>

{#if confirmState.open}
  <div class="scrim" role="presentation">
    <div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" data-testid="confirm">
      <TriangleAlert size={44} color="var(--warn)" aria-hidden="true" />
      <h2 id="confirm-title">{confirmState.label}</h2>
      <p class="muted">Cette action est sensible : confirme pour l'exécuter.</p>
      <p class="dim" data-testid="confirm-timer">Expire dans {formatCountdown(remaining)}</p>
      <div class="actions">
        <button class="btn big" data-testid="confirm-cancel" onclick={() => settle(false)}>Annuler</button>
        <button class="btn big danger" data-testid="confirm-ok" onclick={() => settle(true)}>Confirmer</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 40;
    display: grid;
    place-items: center;
    background: rgb(0 0 0 / 0.62);
  }
  .dialog {
    width: min(620px, calc(100vw - 48px));
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 28px 32px;
    text-align: center;
    background: var(--surface-2);
    border: 1px solid var(--line);
    border-radius: 20px;
  }
  h2 {
    font-size: var(--fs-title);
    font-weight: 700;
  }
  .actions {
    display: flex;
    gap: 16px;
    margin-top: 14px;
    width: 100%;
  }
  .actions .btn {
    flex: 1;
  }
</style>
