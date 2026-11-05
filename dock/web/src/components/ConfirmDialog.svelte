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
      <TriangleAlert size={56} color="var(--warn)" aria-hidden="true" />
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
  /* Texte doublé : la fenêtre prend presque toute la largeur ; si le libellé est très long elle défile, les deux boutons restent atteignables. */
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 40;
    display: grid;
    place-items: center;
    padding: 12px;
    background: rgb(0 0 0 / 0.62);
  }
  .dialog {
    width: min(920px, 100%);
    max-height: 100%;
    overflow-y: auto;
    overscroll-behavior: contain;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 20px 28px;
    text-align: center;
    background: var(--surface-2);
    border: 1px solid var(--line);
    border-radius: 20px;
  }
  h2 {
    font-size: var(--fs);
    font-weight: 700;
    line-height: 1.25;
  }
  p {
    font-size: var(--fs-cap);
  }
  .actions {
    display: flex;
    gap: 16px;
    margin-top: 8px;
    width: 100%;
  }
  .actions .btn {
    flex: 1;
  }
</style>
