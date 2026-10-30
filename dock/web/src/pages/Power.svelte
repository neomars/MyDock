<script lang="ts">
  import Lock from '@lucide/svelte/icons/lock'
  import Moon from '@lucide/svelte/icons/moon'
  import Power from '@lucide/svelte/icons/power'
  import RotateCcw from '@lucide/svelte/icons/rotate-ccw'
  import { onMount } from 'svelte'
  import PageHeader from '../components/PageHeader.svelte'
  import { formatCountdown, ifaceKindLabel } from '../lib/format'
  import { blockerLine, controlView, POWER_CONTROLS, type PowerControlId } from '../lib/power'
  import { power } from '../lib/power.svelte'
  import type { PowerData } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { wake, wakePc } from '../lib/wake.svelte'

  onMount(() => sock.subscribe(['power']))

  const data = $derived(sock.topics.power as PowerData | undefined)
  const name = $derived(data?.hostname ?? sock.host?.hostname ?? null)
  const blockers = $derived(data?.blockers ?? [])
  const ICONS = { lock: Lock, suspend: Moon, reboot: RotateCcw, poweroff: Power } as const
  /** Ce qui coupe le PC est teinté de rouge : on ne le touche pas par inadvertance. */
  const DANGEROUS: readonly PowerControlId[] = ['reboot', 'poweroff']

  const wol = $derived(sock.dock?.wol)
  let now = $state(Date.now())
  $effect(() => {
    if (!wake.sentAt) return
    const timer = setInterval(() => (now = Date.now()), 500)
    return () => clearInterval(timer)
  })
  const sentAgo = $derived(wake.sentAt ? Math.max(0, now - wake.sentAt) : null)
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

  <section class="card" data-testid="power-card">
    <h2>{name ? `Contrôler « ${name} »` : 'Contrôler le PC'}</h2>
    {#if !sock.connected}
      <p class="notice dim" data-testid="power-offline">
        Les commandes reviendront dès que le PC sera joignable.
      </p>
    {:else if data?.unavailable}
      <p class="notice problem" data-testid="power-unavailable">{data.unavailable}</p>
    {/if}
    <div class="controls">
      {#each POWER_CONTROLS as control (control.id)}
        {@const Icon = ICONS[control.id]}
        {@const view = controlView(control.id, sock.connected, data)}
        <button
          class="ctl"
          class:danger={DANGEROUS.includes(control.id)}
          disabled={!view.ok || power.busy !== null}
          data-testid="power-{control.id}"
          onclick={() => void power.run(control.id)}
        >
          <Icon size={32} aria-hidden="true" />
          <span class="label">{power.busy === control.id ? 'En cours…' : control.label}</span>
          {#if view.why}
            <small class="why" data-testid="power-why-{control.id}">{view.why}</small>
          {/if}
        </button>
      {/each}
    </div>
    {#if blockers.length}
      <ul class="blockers" data-testid="power-blockers">
        {#each blockers as blocker (blocker.who + blocker.why)}
          <li>{blockerLine(blocker)}</li>
        {/each}
      </ul>
    {/if}
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
  .notice {
    margin-bottom: 12px;
    font-size: var(--fs-small);
  }
  .notice.problem {
    color: var(--warn);
  }
  .controls {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  .ctl {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    min-height: 104px;
    padding: 10px 8px;
    border-radius: 14px;
    background: var(--surface-3);
    font-weight: 600;
    text-align: center;
  }
  .ctl:active:not(:disabled) {
    background: var(--line);
    transform: scale(0.97);
  }
  .ctl.danger {
    background: var(--bad-soft);
    color: var(--bad);
    box-shadow: inset 0 0 0 1px var(--bad);
  }
  .ctl.danger:active:not(:disabled) {
    background: #4a1f29;
  }
  /* Grisé mais lisible : la raison du grisage compte autant que le bouton. */
  .ctl:disabled {
    background: var(--surface-2);
    color: var(--text-3);
    box-shadow: inset 0 0 0 1px var(--line);
  }
  .label {
    font-size: var(--fs);
  }
  .why {
    color: var(--warn);
    font-size: var(--fs-cap);
    font-weight: 500;
    line-height: 1.2;
  }
  .blockers {
    margin: 14px 0 0;
    padding: 0;
    list-style: none;
    display: grid;
    gap: 6px;
    color: var(--warn);
    font-size: var(--fs-small);
  }
</style>
