<script lang="ts">
  import Cable from '@lucide/svelte/icons/cable'
  import Wifi from '@lucide/svelte/icons/wifi'
  import PageHeader from '../components/PageHeader.svelte'
  import { clock } from '../lib/clock.svelte'
  import { formatDuration, ifaceKindLabel, sessionLabel } from '../lib/format'
  import { sock } from '../lib/socket.svelte'

  const host = $derived(sock.host)
  const uptime = $derived(host ? formatDuration(clock.now.getTime() / 1000 - host.boot_time) : '')
  const wolIface = $derived(sock.dock?.wol.iface)
</script>

<PageHeader title="Système" />

{#if host}
  <div class="grid">
    <section class="card" data-testid="system-host">
      <h2>Ce PC</h2>
      <p class="hostname">{host.hostname}</p>
      <p class="muted os">{host.os}</p>
      <div class="chips">
        <span class="chip accent">
          {sessionLabel(host.session.type)}{host.session.desktop ? ` · ${host.session.desktop}` : ''}
        </span>
        <span class="chip">Noyau {host.kernel}</span>
        <span class="chip">{host.arch}</span>
      </div>
      <p class="uptime">Allumé depuis <strong data-testid="uptime">{uptime}</strong></p>
      <p class="dim small">
        Agent {host.agent.version} · Python {host.python}
        {#if host.agent.demo}· <strong>mode démo</strong>{/if}
      </p>
    </section>

    <section class="card" data-testid="system-network">
      <h2>Réseau</h2>
      {#if host.interfaces.length === 0}
        <p class="muted">Aucune interface physique détectée.</p>
      {/if}
      {#each host.interfaces as itf (itf.name)}
        <div class="iface">
          <span class="ico" class:on={itf.up}>
            {#if itf.kind === 'wifi'}<Wifi size={30} />{:else}<Cable size={30} />{/if}
          </span>
          <div class="info">
            <div class="top">
              <strong>{ifaceKindLabel(itf.kind)}</strong>
              <span class="dim mono small">{itf.name}</span>
              <span class="chip" class:good={itf.up}>{itf.up ? 'actif' : 'inactif'}</span>
              {#if wolIface === itf.name}<span class="chip accent">réveil</span>{/if}
            </div>
            <div class="dim mono small">
              {itf.ipv4.join(', ') || 'pas d’adresse IPv4'} · {itf.mac}
            </div>
          </div>
        </div>
      {/each}
    </section>

    <section class="card soon">
      <p class="muted">
        Processeur par cœur, mémoire, carte graphique, températures, disques et processus arrivent à
        l'étape b.
      </p>
    </section>
  </div>
{:else}
  <p class="muted">Lecture des informations du PC…</p>
{/if}

<style>
  .grid {
    display: grid;
    grid-template-columns: 1fr 1.15fr;
    gap: 16px;
  }
  .soon {
    grid-column: 1 / -1;
    background: transparent;
    border-style: dashed;
  }
  .hostname {
    font-size: 40px;
    font-weight: 700;
    line-height: 1.1;
    overflow-wrap: anywhere;
  }
  .os {
    margin-bottom: 12px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 14px;
  }
  .uptime {
    font-size: var(--fs-large);
  }
  .small {
    font-size: var(--fs-small);
    margin-top: 6px;
  }
  .iface {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 10px 0;
  }
  .iface + .iface {
    border-top: 1px solid var(--line);
  }
  .ico {
    display: grid;
    place-items: center;
    width: 52px;
    height: 52px;
    border-radius: 12px;
    background: var(--surface-3);
    color: var(--text-3);
    flex: none;
  }
  .ico.on {
    background: var(--good-soft);
    color: var(--good);
  }
  .info {
    min-width: 0;
  }
  .top {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
