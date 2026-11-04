<script lang="ts">
  import CircleX from '@lucide/svelte/icons/circle-x'
  import Hourglass from '@lucide/svelte/icons/hourglass'
  import KeyRound from '@lucide/svelte/icons/key-round'
  import Link2 from '@lucide/svelte/icons/link-2'
  import MonitorOff from '@lucide/svelte/icons/monitor-off'
  import Power from '@lucide/svelte/icons/power'
  import ServerOff from '@lucide/svelte/icons/server-off'
  import Settings from '@lucide/svelte/icons/settings'
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import type { Component } from 'svelte'
  import { clock } from '../lib/clock.svelte'
  import { formatClock, formatCountdown, formatDate } from '../lib/format'
  import { portalReady } from '../lib/pairing'
  import { pairingUi } from '../lib/pairing.svelte'
  import type { LinkState } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { wake, wakePc } from '../lib/wake.svelte'
  import PairingPanel from './PairingPanel.svelte'

  let { onSettings }: { onSettings: () => void } = $props()

  interface Copy {
    title: string
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    icon: Component<any>
    tone: 'bad' | 'warn' | 'dim'
    /** Le PC est probablement éteint : proposer de l'allumer. */
    canWake: boolean
    /** Une correction passe par les Réglages. */
    needsSettings: boolean
    /** Associer (de nouveau) un PC règle le problème : le jeton manque ou a changé. */
    canPair: boolean
  }

  const COPY: Record<LinkState, Copy> = {
    unreachable: { title: 'PC hors ligne', icon: MonitorOff, tone: 'bad', canWake: true, needsSettings: false, canPair: false },
    connecting: { title: 'Connexion au PC…', icon: Hourglass, tone: 'warn', canWake: true, needsSettings: false, canPair: false },
    host_up_agent_down: { title: 'PC allumé, agent arrêté', icon: ServerOff, tone: 'warn', canWake: false, needsSettings: false, canPair: false },
    auth_failed: { title: 'Connexion refusée', icon: KeyRound, tone: 'bad', canWake: false, needsSettings: true, canPair: true },
    protocol_mismatch: { title: 'Versions incompatibles', icon: TriangleAlert, tone: 'bad', canWake: false, needsSettings: false, canPair: false },
    not_configured: { title: 'Aucun PC associé', icon: CircleX, tone: 'dim', canWake: false, needsSettings: true, canPair: true },
    connected: { title: 'Connecté', icon: Power, tone: 'dim', canWake: false, needsSettings: false, canPair: false },
  }

  // Compte à rebours de la prochaine tentative : un réveil toutes les 500 ms, seulement tant que cet écran est affiché.
  let now = $state(Date.now())
  $effect(() => {
    const timer = setInterval(() => (now = Date.now()), 500)
    return () => clearInterval(timer)
  })

  // Pendant une nouvelle tentative on garde le dernier diagnostic (« PC hors ligne ») au lieu de
  // basculer 4 s sur « Connexion… » à chaque essai, ce qui ferait clignoter l'écran.
  const retrying = $derived(sock.link?.state === 'connecting' && sock.problem !== null)
  const link = $derived(retrying ? sock.problem : sock.link)
  const copy = $derived(COPY[link?.state ?? 'connecting'])
  const Icon = $derived(copy.icon)
  const retryIn = $derived(link?.next_retry_at ? link.next_retry_at - now : null)
  const wol = $derived(sock.dock?.wol)
  const sentAgo = $derived(wake.sentAt ? now - wake.sentAt : null)
  const waking = $derived(sentAgo !== null && sentAgo < 120_000)

  // Un code est affiché : il remplace le diagnostic, le temps d'associer le PC.
  const pairingOpen = $derived(sock.dock?.pairing?.open === true)
  const portal = $derived(sock.dock?.portal)
  const canOfferPairing = $derived(copy.canPair && portalReady(portal) && !pairingOpen)
  const portalProblem = $derived(copy.canPair && portal && !portalReady(portal))
</script>

{#snippet diagnostic()}
  <div class="clockblock">
    <div class="time">{formatClock(clock.now)}</div>
    <div class="date muted">{formatDate(clock.now)}</div>
  </div>

  <div class="status card {copy.tone}">
    <div class="line">
      <Icon size={40} aria-hidden="true" />
      <h1 data-testid="offline-title">{copy.title}</h1>
    </div>
    <p class="detail muted" data-testid="offline-detail">{link?.detail}</p>
    {#if retrying}
      <p class="dim" data-testid="offline-retry">Nouvelle tentative en cours…</p>
    {:else if retryIn !== null && link?.state !== 'not_configured' && link?.state !== 'connecting'}
      <p class="dim" data-testid="offline-retry">
        {retryIn > 0 ? `Nouvelle tentative dans ${formatCountdown(retryIn)}` : 'Nouvelle tentative…'}
      </p>
    {/if}
    {#if waking && sentAgo !== null}
      <p class="waking" data-testid="offline-waking">
        Réveil demandé il y a {formatCountdown(sentAgo)} : un PC met 20 à 60 s à démarrer.
      </p>
    {/if}
  </div>

  <div class="actions">
    {#if copy.canWake}
      <button
        class="btn primary big"
        data-testid="wake"
        disabled={wake.sending}
        onclick={() => void wakePc()}
      >
        <Power size={30} aria-hidden="true" />
        {wake.sending ? 'Envoi…' : 'Allumer le PC'}
      </button>
    {/if}
    {#if canOfferPairing}
      <button class="btn primary big" data-testid="pair-open" disabled={pairingUi.busy} onclick={() => void pairingUi.open()}>
        <Link2 size={30} aria-hidden="true" />
        {link?.state === 'auth_failed' ? 'Associer à nouveau' : 'Associer un PC'}
      </button>
    {/if}
    {#if copy.needsSettings}
      <button class="btn big" data-testid="open-settings" onclick={onSettings}>
        <Settings size={28} aria-hidden="true" /> Réglages
      </button>
    {/if}
  </div>

  {#if portalProblem}
    <p class="hint warn" data-testid="portal-problem">
      La page d'installation du PC n'est pas disponible{portal?.error ? ` : ${portal.error.replace(/\.$/, '')}` : ''}.
      Saisis l'adresse et le jeton du PC dans les Réglages.
    </p>
  {/if}

  {#if copy.canWake && wol}
    {#if !wol.mac}
      <p class="hint dim" data-testid="wol-hint">
        Adresse MAC du PC inconnue : le dock la mémorise dès qu'il a vu le PC allumé.
      </p>
    {:else if wol.kind === 'wifi'}
      <p class="hint warn" data-testid="wol-hint">
        Adresse du Wi-Fi mémorisée : réveiller un PC éteint par Wi-Fi ne marche en général pas.
        Branche-le en Ethernet.
      </p>
    {/if}
  {/if}
{/snippet}

<section class="offline" data-testid="offline">
  {#if pairingOpen}
    <div class="welcome" data-testid="pairing-screen">
      <div class="line">
        <Link2 size={40} aria-hidden="true" />
        <h1 data-testid="pairing-title">Associer un PC</h1>
      </div>
      <p class="muted" data-testid="pairing-lead">
        Le dock se configure tout seul : rien à recopier, aucun jeton à saisir.
      </p>
      <PairingPanel />
      <div class="actions">
        {#if link?.state !== 'not_configured'}
          <button class="btn" data-testid="pairing-close" disabled={pairingUi.busy} onclick={() => void pairingUi.close()}>
            Annuler
          </button>
        {/if}
        {#if copy.needsSettings}
          <button class="btn" data-testid="open-settings" onclick={onSettings}>
            <Settings size={26} aria-hidden="true" /> Saisie à la main (Réglages)
          </button>
        {/if}
      </div>
    </div>
  {:else}
    {@render diagnostic()}
  {/if}
</section>

<style>
  .offline {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 16px;
    text-align: center;
  }
  .time {
    font-size: 104px;
    font-weight: 300;
    line-height: 1;
    letter-spacing: -0.02em;
  }
  .date {
    margin-top: 6px;
    font-size: var(--fs-large);
  }
  .status {
    width: min(720px, 100%);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 16px 24px;
  }
  .line {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  h1 {
    font-size: var(--fs-title);
    font-weight: 700;
  }
  .status.bad .line {
    color: var(--bad);
  }
  .status.warn .line {
    color: var(--warn);
  }
  .status.dim .line {
    color: var(--text-2);
  }
  .detail {
    max-width: 640px;
  }
  .waking {
    color: var(--good);
    font-weight: 600;
  }
  .actions {
    display: flex;
    gap: 16px;
  }
  .welcome {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }
  .welcome .line {
    color: var(--accent);
  }
  .hint {
    max-width: 700px;
    font-size: var(--fs-small);
  }
  .hint.warn {
    color: var(--warn);
  }
</style>
