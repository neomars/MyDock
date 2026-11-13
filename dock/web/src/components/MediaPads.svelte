<script lang="ts">
  import Battery from '@lucide/svelte/icons/battery-medium'
  import Bluetooth from '@lucide/svelte/icons/bluetooth'
  import BluetoothSearching from '@lucide/svelte/icons/bluetooth-searching'
  import Gamepad2 from '@lucide/svelte/icons/gamepad-2'
  import { onMount } from 'svelte'
  import { askConfirmation } from '../lib/confirm.svelte'
  import { batteryText, foundPads, PAD_HELP, padsBlocker, pairedPads, pairingText, scanSecondsLeft } from '../lib/retro'
  import { retro } from '../lib/retro.svelte'
  import { sock } from '../lib/socket.svelte'

  // Manettes Bluetooth : les manettes associées (connectée ou non, batterie), l'oubli d'une manette, et l'association d'une
  // nouvelle (une recherche de 30 secondes qui ne montre que des manettes). Tout vient de BlueZ, suivi en direct.
  const pads = $derived(sock.pads)
  const paired = $derived(pairedPads(pads))
  const found = $derived(foundPads(pads))
  const blocker = $derived(padsBlocker(pads))
  const message = $derived(pairingText(pads?.pairing, pads))
  const pairing = $derived(pads?.pairing ?? null)
  const busy = $derived(pairing?.state === 'pairing' || pairing?.state === 'connecting')

  let now = $state(Date.now())
  $effect(() => {
    void pads?.scan_until
    now = Date.now() // l'heure est relue dès que la recherche commence : le décompte part de 30, pas de 31
  })
  onMount(() => {
    const timer = setInterval(() => (now = Date.now()), 500)
    return () => {
      clearInterval(timer)
      if (sock.pads?.scanning) void retro.stopScan() // quitter l'écran arrête la recherche : le Bluetooth n'est pas laissé à chercher
    }
  })
  const left = $derived(scanSecondsLeft(pads, now))

  async function forget(id: string, name: string): Promise<void> {
    if (await askConfirmation(`Oublier la manette « ${name} » ? Il faudra l'associer de nouveau pour s'en servir.`, 30)) await retro.forget(id)
  }
</script>

{#if blocker}
  <section class="card problem" data-testid="pads-blocker">
    <h2>Le Bluetooth n’est pas disponible</h2>
    <p>{blocker}</p>
  </section>
{/if}

{#if message}
  <p class="pairing" class:good={pairing?.state === 'done'} class:bad={pairing?.state === 'failed'} data-testid="pads-pairing" data-state={pairing?.state}>{message}</p>
{/if}

<h2 class="head">Mes manettes</h2>
<ul class="list" data-testid="pads-paired">
  {#each paired as pad (pad.id)}
    <li class="row" data-testid="pad-{pad.id}" data-connected={pad.connected}>
      <Gamepad2 size={40} aria-hidden="true" />
      <span class="what">
        <strong class="name">{pad.name}</strong>
        <span class="meta dim">
          {pad.connected ? 'Connectée' : 'Hors ligne : appuie sur son bouton central'}
          {#if pad.connected && batteryText(pad.battery)}
            <span class="batt"><Battery size={22} aria-hidden="true" /><span data-testid="pad-battery-{pad.id}">{batteryText(pad.battery)}</span></span>
          {/if}
        </span>
      </span>
      <button class="btn" data-testid="pad-forget-{pad.id}" onclick={() => forget(pad.id, pad.name)}>Oublier</button>
    </li>
  {:else}
    <li class="none dim" data-testid="pads-none">Aucune manette associée.</li>
  {/each}
</ul>

<h2 class="head">Nouvelle manette</h2>
{#if pads?.scanning}
  <section class="card scanning" data-testid="pads-scanning">
    <p class="state"><BluetoothSearching size={34} aria-hidden="true" /><span>Recherche de manettes… <span class="mono" data-testid="pads-left">{left} s</span></span></p>
    <ul class="help">
      {#each PAD_HELP as brand (brand.name)}
        <li><strong>{brand.name}</strong> : {brand.steps}</li>
      {/each}
    </ul>
    <ul class="list" data-testid="pads-found">
      {#each found as pad (pad.id)}
        <li class="row">
          <Gamepad2 size={40} aria-hidden="true" />
          <span class="what"><strong class="name">{pad.name}</strong><span class="meta dim mono">{pad.id}</span></span>
          <button class="btn primary" disabled={busy} data-testid="pad-pair-{pad.id}" onclick={() => retro.pair(pad.id)}>Associer</button>
        </li>
      {:else}
        <li class="none dim" data-testid="pads-found-none">Aucune manette trouvée pour l’instant : mets la tienne en mode association.</li>
      {/each}
    </ul>
    <button class="btn" data-testid="pads-stop" onclick={() => retro.stopScan()}>Arrêter la recherche</button>
  </section>
{:else}
  <button class="btn primary big" data-testid="pads-scan" disabled={busy || (blocker !== null && !pads?.blocked)} onclick={() => retro.scan()}>
    <Bluetooth size={36} aria-hidden="true" />Associer une manette
  </button>
  <p class="dim note">La recherche dure 30 secondes et ne montre que des manettes. Mets la manette en mode association avant de la lancer (PS4/PS5, Xbox, 8BitDo, Switch Pro).</p>
{/if}

<style>
  .head {
    margin: 16px 0 8px;
    font-size: var(--fs-small);
    color: var(--text-3);
  }
  .problem {
    border-color: var(--warn);
  }
  .problem h2 {
    color: var(--warn);
    margin: 0 0 6px;
  }
  .problem p {
    margin: 0;
  }
  .pairing {
    margin: 0 0 8px;
    padding: 10px 14px;
    border-radius: 12px;
    background: var(--surface-2);
    border: 1px solid var(--accent);
    font-size: var(--fs-small);
  }
  .pairing.good {
    border-color: var(--good);
  }
  .pairing.bad {
    border-color: var(--bad);
    color: var(--bad);
  }
  .list {
    list-style: none;
    margin: 0 0 10px;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 14px;
    min-height: 84px;
    padding: 8px 14px;
    border-radius: 14px;
    background: var(--surface-2);
    border: 1px solid var(--line);
  }
  .row[data-connected='true'] {
    border-color: var(--good);
  }
  .row :global(svg) {
    flex: none;
  }
  .what {
    display: flex;
    flex-direction: column;
    flex: 1 1 0;
    min-width: 0;
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-small);
  }
  .meta {
    font-size: var(--fs-cap);
  }
  .batt {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    margin-left: 8px;
    color: var(--text);
  }
  .none {
    padding: 8px 4px;
    font-size: var(--fs-small);
  }
  .scanning {
    display: flex;
    flex-direction: column;
    gap: 10px;
    align-items: stretch;
  }
  .state {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 0;
    font-size: var(--fs-small);
    color: var(--accent);
  }
  .help {
    margin: 0;
    padding-left: 20px;
    font-size: var(--fs-cap);
    line-height: 1.3;
  }
  .note {
    margin: 10px 0 0;
    font-size: var(--fs-cap);
  }
</style>
