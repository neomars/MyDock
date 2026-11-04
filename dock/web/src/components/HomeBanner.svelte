<script lang="ts">
  import CircleX from '@lucide/svelte/icons/circle-x'
  import Hourglass from '@lucide/svelte/icons/hourglass'
  import KeyRound from '@lucide/svelte/icons/key-round'
  import Link2 from '@lucide/svelte/icons/link-2'
  import MonitorOff from '@lucide/svelte/icons/monitor-off'
  import Power from '@lucide/svelte/icons/power'
  import ServerOff from '@lucide/svelte/icons/server-off'
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import X from '@lucide/svelte/icons/x'
  import type { Component } from 'svelte'
  import { formatCountdown } from '../lib/format'
  import { isPaired, portalReady, remainingMs, shownUrls } from '../lib/pairing'
  import { pairingUi } from '../lib/pairing.svelte'
  import type { LinkState, PageId } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { wake, wakePc } from '../lib/wake.svelte'

  let { onOpen }: { onOpen: (id: PageId) => void } = $props()

  // Un bandeau au-dessus des boutons, seulement quand il y a quelque chose à savoir ou à faire : l'écran d'accueil
  // d'un PC qui va bien n'a que ses boutons.
  interface Status {
    text: string
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    icon: Component<any>
    tone: 'bad' | 'warn' | 'dim'
  }
  const STATUS: Partial<Record<LinkState, Status>> = {
    unreachable: { text: 'PC hors ligne', icon: MonitorOff, tone: 'bad' },
    connecting: { text: 'Connexion au PC…', icon: Hourglass, tone: 'warn' },
    host_up_agent_down: { text: 'PC allumé, agent arrêté', icon: ServerOff, tone: 'warn' },
    auth_failed: { text: 'Connexion refusée', icon: KeyRound, tone: 'bad' },
    protocol_mismatch: { text: 'Versions incompatibles', icon: TriangleAlert, tone: 'bad' },
    not_configured: { text: 'Aucun PC associé', icon: CircleX, tone: 'dim' },
  }

  let now = $state(Date.now())
  $effect(() => {
    const timer = setInterval(() => (now = Date.now()), 1_000)
    return () => clearInterval(timer)
  })

  // Pendant une nouvelle tentative on garde le dernier diagnostic au lieu de clignoter sur « Connexion… ».
  const link = $derived(sock.link?.state === 'connecting' && sock.problem !== null ? sock.problem : sock.link)
  const linkState = $derived(link?.state)
  const status = $derived(linkState ? STATUS[linkState] : undefined)

  const pairing = $derived(sock.dock?.pairing)
  const portal = $derived(sock.dock?.portal)
  const code = $derived(pairing?.open ? pairing : null)
  const left = $derived(remainingMs(pairing, now))
  const url = $derived(shownUrls(portal?.urls ?? [], 1)[0])

  const sentAgo = $derived(wake.sentAt ? now - wake.sentAt : null)
  const waking = $derived(sentAgo !== null && sentAgo < 120_000)
  const canWake = $derived(linkState === 'unreachable')
  const canRepair = $derived(linkState === 'auth_failed' && portalReady(portal))
  const Icon = $derived(status?.icon)
</script>

{#if code}
  <!-- Un code est affiché : c'est ce qu'on a à faire, il passe avant tout le reste. -->
  <section class="banner pairing" data-testid="home-banner" data-kind="pairing">
    <div class="how">
      <h2><Link2 size={40} aria-hidden="true" /> Associer un PC</h2>
      <p class="muted">
        Sur le PC, ouvre <span class="mono url" data-testid="banner-url">{url ?? "l'adresse du Pi"}</span>
      </p>
    </div>
    <div class="code">
      <span class="mono value" data-testid="banner-code">{code.code}</span>
      {#if left !== null}<small class="dim" class:soon={left < 60_000}>valable encore {formatCountdown(left)}</small>{/if}
    </div>
    {#if isPaired(sock.dock)}
      <button class="btn close" data-testid="banner-close" aria-label="Fermer l'association" disabled={pairingUi.busy} onclick={() => void pairingUi.close()}>
        <X size={36} aria-hidden="true" />
      </button>
    {/if}
  </section>
{:else if status && Icon}
  <section class="banner {status.tone}" data-testid="home-banner" data-kind={linkState}>
    <div class="how">
      <h2><Icon size={40} aria-hidden="true" /> <span data-testid="banner-title">{status.text}</span></h2>
      {#if waking && sentAgo !== null}
        <p class="waking" data-testid="banner-waking">Réveil demandé il y a {formatCountdown(sentAgo)}</p>
      {/if}
    </div>
    {#if canWake}
      <button class="btn primary" data-testid="banner-wake" disabled={wake.sending} onclick={() => void wakePc()}>
        <Power size={36} aria-hidden="true" /> {wake.sending ? 'Envoi…' : 'Allumer le PC'}
      </button>
    {:else if canRepair}
      <button class="btn primary" data-testid="banner-pair" disabled={pairingUi.busy} onclick={() => void pairingUi.open()}>
        <Link2 size={36} aria-hidden="true" /> Associer à nouveau
      </button>
    {:else if linkState === 'not_configured'}
      <button class="btn" data-testid="banner-settings" onclick={() => onOpen('settings')}>Réglages</button>
    {/if}
  </section>
{/if}

<style>
  .banner {
    flex: none;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    min-height: calc(var(--tap) + 24px);
    padding: 12px 24px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }
  .banner.bad h2 {
    color: var(--bad);
  }
  .banner.warn h2 {
    color: var(--warn);
  }
  .banner.dim h2 {
    color: var(--text-2);
  }
  .pairing h2 {
    color: var(--accent);
  }
  h2 {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: var(--fs-small);
    font-weight: 700;
  }
  .how p {
    font-size: var(--fs-cap);
  }
  .url {
    color: var(--accent);
    font-weight: 600;
  }
  .waking {
    color: var(--good);
    font-weight: 600;
  }
  .how {
    min-width: 0;
  }
  .code {
    flex: none;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
  }
  .value {
    font-size: calc(28px * var(--ts));
    font-weight: 700;
    line-height: 1.05;
    letter-spacing: 0.04em;
    white-space: nowrap;
  }
  small {
    font-size: calc(12px * var(--ts));
  }
  small.soon {
    color: var(--warn);
  }
  .close {
    flex: none;
    padding: 0;
    width: var(--tap);
  }
</style>
