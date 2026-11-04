<script lang="ts">
  import Clock from '@lucide/svelte/icons/clock'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import type { Snippet } from 'svelte'
  import { formatCountdown } from '../lib/format'
  import { attemptsNote, remainingMs, shownUrls } from '../lib/pairing'
  import { pairingUi } from '../lib/pairing.svelte'
  import { sock } from '../lib/socket.svelte'

  /** `compact` : la version des Réglages (le code d'abord, l'adresse dessous) ; sinon celle de l'écran d'accueil du dock
   *  (l'adresse à ouvrir d'abord, puis le code en grand).
   *  `onClose` : ajoute un bouton « Fermer » à côté de « Nouveau code ». `extra` : d'autres boutons sur la même rangée. */
  let { compact = false, onClose, extra }: { compact?: boolean; onClose?: () => void; extra?: Snippet } = $props()

  // Un réveil par seconde, seulement tant que le code est affiché.
  let now = $state(Date.now())
  $effect(() => {
    const timer = setInterval(() => (now = Date.now()), 1_000)
    return () => clearInterval(timer)
  })

  const pairing = $derived(sock.dock?.pairing)
  const urls = $derived(shownUrls(sock.dock?.portal?.urls ?? [], 2))
  const left = $derived(remainingMs(pairing, now))
  const attempts = $derived(attemptsNote(pairing))
</script>

{#snippet how()}
  <p class="how" data-testid="pairing-urls">
    Sur le PC, ouvre
    {#each urls as url, i (url)}{i > 0 ? ' ou ' : ' '}<span class="mono url">{url}</span>{:else}{' '}<span class="dim"
        >l'adresse du Pi (voir Réglages → À propos)</span
      >{/each}, installe le paquet, puis tape ce code.
  </p>
{/snippet}

{#if pairing?.open}
  <div class="pairing" class:compact data-testid="pairing">
    {#if !compact}{@render how()}{/if}

    <div class="codebox" data-testid="pairing-box">
      <div class="label">{compact ? 'Code à saisir' : 'Code à saisir sur le PC'}</div>
      <div class="code mono" data-testid="pairing-code" aria-label={pairing.code.split('').join(' ')}>
        {pairing.code}
      </div>
      <div class="meta">
        {#if left !== null}
          <div class="timer" class:soon={left < 60_000} data-testid="pairing-timer">
            <Clock size={30} aria-hidden="true" />
            Valable encore {formatCountdown(left)}
          </div>
        {/if}
        {#if attempts}
          <div class="attempts" data-testid="pairing-attempts">{attempts}</div>
        {/if}
      </div>
      <div class="buttons">
        <button class="btn" data-testid="pairing-renew" disabled={pairingUi.busy} onclick={() => void pairingUi.open()}>
          <RefreshCw size={32} aria-hidden="true" />
          Nouveau code
        </button>
        {#if onClose}
          <button class="btn" data-testid="pairing-close" disabled={pairingUi.busy} onclick={onClose}>Fermer</button>
        {/if}
        {@render extra?.()}
      </div>
    </div>

    {#if compact}{@render how()}{/if}
  </div>
{/if}

<style>
  /* Texte doublé : le code d'abord, le temps restant sur la ligne du titre, les boutons dessous. */
  .pairing {
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
    text-align: left;
  }
  .how {
    font-size: var(--fs-small);
    color: var(--text-2);
    line-height: 1.3;
  }
  .pairing:not(.compact) .how {
    text-align: center;
  }
  .url {
    color: var(--accent);
    font-weight: 600;
  }
  .codebox {
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-areas:
      'label meta'
      'code code'
      'buttons buttons';
    align-items: center;
    justify-items: center;
    gap: 4px 12px;
    padding: 12px 16px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    text-align: center;
  }
  .label {
    grid-area: label;
    justify-self: start;
    color: var(--text-3);
    font-size: var(--fs-cap);
  }
  .meta {
    grid-area: meta;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
  }
  .code {
    grid-area: code;
    font-size: calc(32px * var(--ts));
    font-weight: 700;
    letter-spacing: 0.03em;
    line-height: 1.1;
    color: var(--text);
    user-select: all;
    white-space: nowrap;
  }
  .pairing:not(.compact) .code {
    font-size: calc(42px * var(--ts));
  }
  .timer {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--text-2);
    font-size: var(--fs-cap);
    white-space: nowrap;
  }
  .timer.soon {
    color: var(--warn);
  }
  .attempts {
    color: var(--warn);
    font-size: var(--fs-cap);
    font-weight: 600;
  }
  .buttons {
    grid-area: buttons;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 12px;
    margin-top: 4px;
  }
</style>
