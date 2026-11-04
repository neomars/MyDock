<script lang="ts">
  import Clock from '@lucide/svelte/icons/clock'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import { formatCountdown } from '../lib/format'
  import { attemptsNote, remainingMs, shownUrls } from '../lib/pairing'
  import { pairingUi } from '../lib/pairing.svelte'
  import { sock } from '../lib/socket.svelte'

  /** `compact` : version en colonne des Réglages ; sinon la version pleine largeur de l'écran d'accueil.
   *  `onClose` : ajoute un bouton « Fermer » à côté de « Nouveau code ». */
  let { compact = false, onClose }: { compact?: boolean; onClose?: () => void } = $props()

  // Un réveil par seconde, seulement tant que le code est affiché.
  let now = $state(Date.now())
  $effect(() => {
    const timer = setInterval(() => (now = Date.now()), 1_000)
    return () => clearInterval(timer)
  })

  const pairing = $derived(sock.dock?.pairing)
  const urls = $derived(shownUrls(sock.dock?.portal?.urls ?? [], compact ? 2 : 3))
  const left = $derived(remainingMs(pairing, now))
  const attempts = $derived(attemptsNote(pairing))
</script>

{#if pairing?.open}
  <div class="pairing" class:compact data-testid="pairing">
    {#if !compact}
      <ol class="steps">
        <li>
          <span class="n" aria-hidden="true">1</span>
          <div>
            <p>Sur le PC, ouvre cette adresse dans un navigateur :</p>
            <ul class="urls" data-testid="pairing-urls">
              {#each urls as url (url)}
                <li class="mono">{url}</li>
              {:else}
                <li class="dim">l'adresse du Pi (voir Réglages → À propos)</li>
              {/each}
            </ul>
          </div>
        </li>
        <li>
          <span class="n" aria-hidden="true">2</span>
          <p>Télécharge le paquet et installe-le : la page donne les commandes à copier.</p>
        </li>
        <li>
          <span class="n" aria-hidden="true">3</span>
          <p>Tape le code affiché ici quand la commande le demande.</p>
        </li>
      </ol>
    {/if}

    <div class="codebox" data-testid="pairing-box">
      <div class="label">{compact ? 'Code à saisir' : 'Code à saisir sur le PC'}</div>
      <div class="code mono" data-testid="pairing-code" aria-label={pairing.code.split('').join(' ')}>
        {pairing.code}
      </div>
      <div class="meta">
        {#if left !== null}
          <div class="timer" class:soon={left < 60_000} data-testid="pairing-timer">
            <Clock size={20} aria-hidden="true" />
            Valable encore {formatCountdown(left)}
          </div>
        {/if}
        {#if attempts}
          <div class="attempts" data-testid="pairing-attempts">{attempts}</div>
        {/if}
      </div>
      <div class="buttons">
        <button class="btn" data-testid="pairing-renew" disabled={pairingUi.busy} onclick={() => void pairingUi.open()}>
          <RefreshCw size={24} aria-hidden="true" />
          Nouveau code
        </button>
        {#if onClose}
          <button class="btn" data-testid="pairing-close" disabled={pairingUi.busy} onclick={onClose}>Fermer</button>
        {/if}
      </div>
    </div>

    {#if compact}
      <p class="how" data-testid="pairing-urls">
        Sur le PC, ouvre
        {#each urls as url, i (url)}{i > 0 ? ' ou ' : ' '}<span class="mono url">{url}</span>{/each}, installe le paquet,
        puis tape ce code.
      </p>
    {/if}
  </div>
{/if}

<style>
  .pairing {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 400px;
    gap: 24px;
    align-items: stretch;
    width: min(960px, 100%);
    text-align: left;
  }
  .steps {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 14px;
  }
  .steps > li {
    display: grid;
    grid-template-columns: 40px minmax(0, 1fr);
    gap: 14px;
    align-items: start;
  }
  .n {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 700;
    display: grid;
    place-items: center;
  }
  .steps p {
    font-size: var(--fs);
    padding-top: 5px;
  }
  .urls {
    list-style: none;
    margin: 6px 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: var(--fs-large);
    font-weight: 600;
    color: var(--accent);
  }
  .codebox {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    text-align: center;
  }
  .label {
    color: var(--text-3);
    font-size: var(--fs-small);
  }
  .code {
    font-size: 56px;
    font-weight: 700;
    letter-spacing: 0.04em;
    line-height: 1.1;
    color: var(--text);
    user-select: all;
  }
  .meta {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
  }
  .buttons {
    display: flex;
    gap: 12px;
  }
  .timer {
    display: flex;
    align-items: center;
    gap: 8px;
    white-space: nowrap;
    color: var(--text-2);
    font-size: var(--fs-small);
  }
  .timer.soon {
    color: var(--warn);
  }
  .attempts {
    color: var(--warn);
    font-size: var(--fs-small);
    font-weight: 600;
  }
  .how {
    font-size: var(--fs-small);
    color: var(--text-2);
  }
  .url {
    color: var(--accent);
    font-weight: 600;
  }

  /* Réglages : le code d'abord, le temps restant sur la ligne du titre, les boutons dessous. */
  .compact {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
    width: 100%;
  }
  .compact .codebox {
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-areas:
      'label meta'
      'code code'
      'buttons buttons';
    align-items: center;
    justify-items: center;
    padding: 12px 16px;
    gap: 4px 12px;
  }
  .compact .label {
    grid-area: label;
    justify-self: start;
  }
  .compact .meta {
    grid-area: meta;
    align-items: flex-end;
  }
  .compact .code {
    grid-area: code;
    font-size: 48px;
  }
  .compact .buttons {
    grid-area: buttons;
    margin-top: 4px;
  }
</style>
