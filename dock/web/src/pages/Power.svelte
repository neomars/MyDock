<script lang="ts">
  import Lock from '@lucide/svelte/icons/lock'
  import Moon from '@lucide/svelte/icons/moon'
  import Power from '@lucide/svelte/icons/power'
  import RotateCcw from '@lucide/svelte/icons/rotate-ccw'
  import PageHeader from '../components/PageHeader.svelte'
  import { formatCountdown, ifaceKindLabel } from '../lib/format'
  import { sock } from '../lib/socket.svelte'
  import { wake, wakePc } from '../lib/wake.svelte'

  const wol = $derived(sock.dock?.wol)
  let now = $state(Date.now())
  $effect(() => {
    if (!wake.sentAt) return
    const timer = setInterval(() => (now = Date.now()), 500)
    return () => clearInterval(timer)
  })
  const sentAgo = $derived(wake.sentAt ? Math.max(0, now - wake.sentAt) : null)

  const CONTROLS = [
    { label: 'Verrouiller', icon: Lock },
    { label: 'Mettre en veille', icon: Moon },
    { label: 'Redémarrer', icon: RotateCcw },
    { label: 'Éteindre', icon: Power },
  ]
</script>

<PageHeader title="Alimentation" />

<div class="grid">
  <section class="card" data-testid="wol-card">
    <h2>Allumer le PC (Wake-on-LAN)</h2>
    {#if sock.connected}
      <p class="state"><span class="chip good">PC allumé et connecté</span></p>
    {:else}
      <p class="state"><span class="chip warn">PC hors ligne</span></p>
    {/if}
    <button
      class="btn primary big"
      data-testid="wake-power"
      disabled={wake.sending}
      onclick={() => void wakePc()}
    >
      <Power size={30} aria-hidden="true" />
      {wake.sending ? 'Envoi…' : 'Envoyer le paquet de réveil'}
    </button>
    {#if sentAgo !== null}
      <p class="sent" data-testid="wake-sent">Envoyé il y a {formatCountdown(sentAgo)}</p>
    {/if}
    <dl>
      <dt>Adresse MAC visée</dt>
      <dd class="mono" data-testid="wol-mac">{wol?.mac ?? 'inconnue'}</dd>
      <dt>Origine</dt>
      <dd>
        {#if wol?.source === 'manual'}saisie à la main{:else if wol?.source === 'auto'}apprise
          automatiquement ({ifaceKindLabel(wol.kind ?? '')}{wol.iface ? ` ${wol.iface}` : ''}){:else}—{/if}
      </dd>
    </dl>
    {#if wol?.kind === 'wifi'}
      <p class="warn">
        C'est l'adresse du Wi-Fi : réveiller un PC éteint par Wi-Fi ne marche en général pas.
        Branche le PC en Ethernet (et active le réveil par le réseau dans le BIOS).
      </p>
    {/if}
  </section>

  <section class="card">
    <h2>Contrôler le PC</h2>
    <div class="controls">
      {#each CONTROLS as control (control.label)}
        {@const Icon = control.icon}
        <button class="btn" disabled><Icon size={28} aria-hidden="true" /> {control.label}</button>
      {/each}
    </div>
    <p class="dim note">Ces commandes, avec confirmation à l'écran, arrivent à l'étape f.</p>
  </section>
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: 1.1fr 1fr;
    gap: 16px;
  }
  .state {
    margin-bottom: 14px;
  }
  .sent {
    margin-top: 10px;
    color: var(--good);
    font-weight: 600;
  }
  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 6px 16px;
    margin: 16px 0 0;
  }
  dt {
    color: var(--text-3);
    font-size: var(--fs-small);
  }
  dd {
    margin: 0;
  }
  .warn {
    margin-top: 12px;
    color: var(--warn);
    font-size: var(--fs-small);
  }
  .controls {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  .controls .btn {
    min-height: 76px;
    flex-direction: column;
    gap: 6px;
    font-size: var(--fs-small);
  }
  .note {
    margin-top: 14px;
    font-size: var(--fs-small);
  }
</style>
