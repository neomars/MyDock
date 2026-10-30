<script lang="ts">
  import BatteryCharging from '@lucide/svelte/icons/battery-charging'
  import BatteryMedium from '@lucide/svelte/icons/battery-medium'
  import X from '@lucide/svelte/icons/x'
  import { onMount } from 'svelte'
  import Chart from '../components/Chart.svelte'
  import Meter from '../components/Meter.svelte'
  import { clock } from '../lib/clock.svelte'
  import {
    formatBytes,
    formatDuration,
    formatFreq,
    formatPercent,
    formatRate,
    formatTemp,
    formatUsage,
    sessionLabel,
  } from '../lib/format'
  import type { ProcInfo, ProcsData, SystemData } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { diskLabel, niceMax, summarizeGpus, toneFor } from '../lib/system'
  import { notify } from '../lib/toast.svelte'

  // La page suit « system » (mesures + historique de 60 s) et « procs » (processus) tant qu'elle est ouverte.
  onMount(() => sock.subscribe(['system', 'procs']))

  const sys = $derived(sock.topics.system as SystemData | undefined)
  const procs = $derived(sock.topics.procs as ProcsData | undefined)
  const host = $derived(sock.host)
  const uptime = $derived(host ? formatDuration(clock.now.getTime() / 1000 - host.boot_time) : '')
  const gpu = $derived(sys ? summarizeGpus(sys.gpus) : null)
  const gpuSeries = $derived(sys?.history.gpu ?? [])
  const netMax = $derived(sys ? niceMax([...sys.history.down, ...sys.history.up]) : 100_000)

  let view = $state<'load' | 'net'>('load')

  // --- arrêt d'un processus ----------------------------------------------------------------------

  /** Processus qui a ignoré la demande polie : on propose alors de forcer son arrêt. */
  let stubborn = $state<number | null>(null)

  async function stop(proc: ProcInfo, force = false): Promise<void> {
    const ack = await sock.call('system.kill', { pid: proc.pid, started: proc.started, force })
    if (ack.cancelled) {
      notify('Annulé : rien n’a été arrêté.', 'info')
    } else if (!ack.ok) {
      notify(ack.e?.msg ?? 'Arrêt impossible.', 'bad', 6_000)
    } else if (ack.r.gone) {
      stubborn = null
      notify(`« ${ack.r.name} » est arrêté.`, 'good')
    } else {
      stubborn = proc.pid
      notify(`« ${ack.r.name} » ne s’arrête pas : touche « Forcer ».`, 'info', 6_000)
    }
  }
</script>

{#if sys?.unavailable}
  <section class="card unavailable" data-testid="system-unavailable">
    <h2>Mesures indisponibles</h2>
    <p class="muted">{sys.unavailable}</p>
  </section>
{:else if sys}
  <div class="layout" data-testid="system-page">
    <div class="left">
      <div class="tiles">
        <section class="tile" data-testid="tile-cpu">
          <h2>Processeur</h2>
          <div class="value {toneFor(sys.cpu.percent)}" data-testid="cpu-value">{formatPercent(sys.cpu.percent)}</div>
          <div class="sub">
            {#if sys.cpu.freq_mhz}<span>{formatFreq(sys.cpu.freq_mhz)}</span>{/if}
            {#if sys.cpu.temp_c !== null}<span>{formatTemp(sys.cpu.temp_c)}</span>{/if}
          </div>
        </section>
        <section class="tile" data-testid="tile-mem">
          <h2>Mémoire</h2>
          <div class="value {toneFor(sys.mem.percent)}" data-testid="mem-value">{formatPercent(sys.mem.percent)}</div>
          <div class="sub">{formatUsage(sys.mem.used, sys.mem.total)}</div>
        </section>
        <section class="tile" data-testid="tile-gpu">
          <h2>Carte graphique</h2>
          {#if gpu}
            <div class="value" class:asleep={gpu.asleep} data-testid="gpu-value">{gpu.value}</div>
            <div class="sub two" title={gpu.sub}>{gpu.sub}</div>
          {:else}
            <div class="value asleep">—</div>
            <div class="sub">Aucune détectée</div>
          {/if}
        </section>
        <section class="tile" data-testid="tile-net">
          <h2>Réseau</h2>
          <div class="value net" data-testid="net-down">↓ {formatRate(sys.net.down)}</div>
          <div class="sub" data-testid="net-up">↑ {formatRate(sys.net.up)}</div>
        </section>
      </div>

      <section class="card chartcard" data-testid="chart-card">
        <div class="charthead">
          <div class="legend">
            {#if view === 'load'}
              <span class="key"><i style:background="var(--accent)"></i>Processeur {formatPercent(sys.cpu.percent)}</span>
              <span class="key"><i style:background="var(--good)"></i>Mémoire {formatPercent(sys.mem.percent)}</span>
              {#if gpuSeries.some((v) => v !== null)}
                <span class="key"><i style:background="var(--warn)"></i>GPU</span>
              {/if}
            {:else}
              <span class="key"><i style:background="var(--accent)"></i>Réception</span>
              <span class="key"><i style:background="var(--warn)"></i>Envoi</span>
              <span class="dim">échelle {formatRate(netMax)}</span>
            {/if}
          </div>
          <div class="seg" role="tablist" aria-label="Graphique">
            <button role="tab" aria-selected={view === 'load'} data-testid="view-load" onclick={() => (view = 'load')}>Charge</button>
            <button role="tab" aria-selected={view === 'net'} data-testid="view-net" onclick={() => (view = 'net')}>Réseau</button>
          </div>
        </div>
        <div class="plot">
          {#if view === 'load'}
            <Chart
              label="Charge du processeur, de la mémoire et de la carte graphique sur 60 secondes"
              series={[
                { values: sys.history.mem, color: 'var(--good)' },
                { values: gpuSeries, color: 'var(--warn)' },
                { values: sys.history.cpu, color: 'var(--accent)', fill: true },
              ]}
            />
          {:else}
            <Chart
              label="Débit réseau sur 60 secondes"
              max={netMax}
              series={[
                { values: sys.history.up, color: 'var(--warn)' },
                { values: sys.history.down, color: 'var(--accent)', fill: true },
              ]}
            />
          {/if}
        </div>
        <div class="axis dim"><span>il y a 60 s</span><span>maintenant</span></div>
      </section>

      <section class="card cores" data-testid="cores">
        <h2>Cœurs ({sys.cpu.cores.length})</h2>
        <div class="bars">
          {#each sys.cpu.cores as core, index (index)}
            <div class="core" title="Cœur {index + 1} : {core} %">
              <div class="corefill {toneFor(core)}" style:height="{Math.max(3, core)}%"></div>
            </div>
          {/each}
        </div>
      </section>

      {#if host}
        <p class="identity dim" data-testid="system-host" title="{host.os} · noyau {host.kernel}">
          <strong>{host.hostname}</strong> · {sessionLabel(host.session.type)}{host.session.desktop ? ` ${host.session.desktop}` : ''}
          · allumé depuis <span data-testid="uptime">{uptime}</span>
        </p>
      {/if}
    </div>

    <div class="right">
      <section class="card procs" data-testid="procs">
        <div class="procshead">
          <h2>Processus{procs ? ` (${procs.count})` : ''}</h2>
          <span class="cols"><span>CPU</span><span>Mém.</span></span>
        </div>
        <div class="list">
          {#if !procs}
            <p class="muted">Lecture des processus…</p>
          {/if}
          {#each procs?.procs ?? [] as proc (proc.pid)}
            <div class="proc" data-testid="proc-{proc.pid}">
              <span class="pname" title="{proc.name} · PID {proc.pid} · {proc.user}">{proc.name}</span>
              <span class="pnum">{formatPercent(proc.cpu)}</span>
              <span class="pnum">{formatBytes(proc.mem)}</span>
              {#if proc.killable && stubborn === proc.pid}
                <button class="stopbtn force" aria-label="Forcer l’arrêt de {proc.name}" data-testid="force-{proc.pid}" onclick={() => stop(proc, true)}>Forcer</button>
              {:else if proc.killable}
                <button class="stopbtn" aria-label="Terminer {proc.name}" data-testid="kill-{proc.pid}" onclick={() => stop(proc)}>
                  <X size={26} aria-hidden="true" />
                </button>
              {:else}
                <span class="stopbtn locked" title="Processus du système ou d’une autre session : protégé"></span>
              {/if}
            </div>
          {/each}
        </div>
      </section>

      <section class="card disks" data-testid="disks">
        <h2>Stockage et énergie</h2>
        {#each sys.disks as disk (disk.mount)}
          <div class="disk" data-testid="disk-{diskLabel(disk)}">
            <span class="dlabel" title="{disk.mount} · {disk.device} · {disk.fs}">{diskLabel(disk)}</span>
            <Meter percent={disk.percent} label="Occupation de {diskLabel(disk)}" />
            <span class="dtext">{formatUsage(disk.used, disk.total)}</span>
          </div>
        {/each}
        {#if sys.disks.length === 0}<p class="muted">Aucun disque local détecté.</p>{/if}
        <p class="extra dim" data-testid="disk-io">
          Disque ↓ {formatRate(sys.disk_io.read)} ↑ {formatRate(sys.disk_io.write)}{sys.temps.disk != null ? ` · SSD ${formatTemp(sys.temps.disk)}` : ''}
        </p>
        {#if sys.battery}
          <p class="extra battery" data-testid="battery">
            {#if sys.battery.plugged}<BatteryCharging size={22} aria-hidden="true" />{:else}<BatteryMedium size={22} aria-hidden="true" />{/if}
            Batterie {formatPercent(sys.battery.percent)}
            {sys.battery.plugged ? '· sur secteur' : '· sur batterie'}
          </p>
        {/if}
      </section>
    </div>
  </div>
{:else}
  <p class="muted" data-testid="system-loading">Lecture des mesures du PC…</p>
{/if}

<style>
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr);
    gap: 12px;
    height: 100%;
    min-height: 0;
  }
  .left,
  .right {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
    min-height: 0;
  }
  .unavailable h2 {
    color: var(--warn);
  }

  /* --- tuiles --- */
  .tiles {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
  }
  .tile {
    min-width: 0;
    padding: 10px 12px 9px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }
  .tile h2 {
    font-size: var(--fs-cap);
    font-weight: 600;
    color: var(--text-3);
    white-space: nowrap;
  }
  .value {
    font-size: 30px;
    font-weight: 700;
    line-height: 1.15;
    white-space: nowrap;
  }
  .value.net {
    font-size: 20px;
    line-height: 1.6;
  }
  .value.asleep {
    color: var(--text-2);
    font-size: 24px;
    line-height: 1.4;
  }
  .value.good {
    color: var(--text);
  }
  .value.warn {
    color: var(--warn);
  }
  .value.bad {
    color: var(--bad);
  }
  .sub {
    font-size: var(--fs-cap);
    color: var(--text-2);
    line-height: 1.25;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .sub span {
    display: block;
  }
  .sub.two {
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  /* --- graphique --- */
  .chartcard {
    display: flex;
    flex-direction: column;
    padding: 8px 14px 8px;
    flex: 1 1 0;
    min-height: 150px;
  }
  .charthead {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    font-size: var(--fs-cap);
    color: var(--text-2);
    min-width: 0;
  }
  .key {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }
  .key i {
    width: 12px;
    height: 12px;
    border-radius: 3px;
  }
  .seg {
    display: flex;
    flex: none;
    background: var(--surface-2);
    border-radius: 10px;
  }
  .seg button {
    min-height: 48px;
    padding: 0 14px;
    border-radius: 8px;
    font-size: var(--fs-cap);
    font-weight: 600;
    color: var(--text-2);
  }
  .seg button[aria-selected='true'] {
    background: var(--surface-3);
    color: var(--accent);
  }
  .plot {
    flex: 1 1 0;
    min-height: 60px;
  }
  .axis {
    display: flex;
    justify-content: space-between;
    font-size: var(--fs-cap);
    line-height: 1.1;
  }

  /* --- cœurs --- */
  .cores {
    padding: 6px 14px 8px;
  }
  .cores h2 {
    margin-bottom: 4px;
  }
  .bars {
    display: flex;
    gap: 3px;
    height: 34px;
  }
  .core {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    align-items: flex-end;
    background: var(--surface-2);
    border-radius: 3px;
    overflow: hidden;
  }
  .corefill {
    width: 100%;
    border-radius: 3px;
  }
  .corefill.good {
    background: var(--accent);
  }
  .corefill.warn {
    background: var(--warn);
  }
  .corefill.bad {
    background: var(--bad);
  }
  .identity {
    font-size: var(--fs-cap);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .identity strong {
    color: var(--text-2);
  }

  /* --- processus --- */
  .procs {
    display: flex;
    flex-direction: column;
    flex: 1 1 0;
    min-height: 0;
    padding: 8px 10px 4px 14px;
  }
  .procshead {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding-right: 70px;
  }
  .procshead h2,
  .cores h2 {
    font-size: var(--fs-small);
    font-weight: 600;
    color: var(--text-2);
    letter-spacing: 0.02em;
  }
  .procshead h2 {
    margin-bottom: 2px;
  }
  .cols {
    display: grid;
    grid-template-columns: 58px 74px;
    gap: 6px;
    text-align: right;
    font-size: var(--fs-cap);
    color: var(--text-3);
  }
  .list {
    flex: 1 1 0;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .proc {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 58px 74px 64px;
    align-items: center;
    gap: 6px;
    height: 48px;
    border-top: 1px solid var(--line);
  }
  .proc:first-child {
    border-top: 0;
  }
  .pname {
    font-size: var(--fs);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pnum {
    text-align: right;
    font-size: var(--fs-small);
    color: var(--text-2);
    white-space: nowrap;
  }
  .stopbtn {
    display: grid;
    place-items: center;
    justify-self: end;
    width: 48px;
    height: 48px;
    border-radius: 12px;
    color: var(--text-2);
  }
  button.stopbtn:active {
    background: var(--bad-soft);
    color: var(--bad);
  }
  .stopbtn.force {
    width: 64px;
    background: var(--bad-soft);
    color: var(--bad);
    font-size: var(--fs-cap);
    font-weight: 700;
    box-shadow: inset 0 0 0 1px var(--bad);
  }
  .stopbtn.locked {
    opacity: 0;
  }

  /* --- stockage --- */
  .disks {
    padding: 8px 14px 8px;
  }
  .disks h2 {
    margin-bottom: 4px;
  }
  .disk {
    display: grid;
    grid-template-columns: 74px minmax(0, 1fr) 118px;
    align-items: center;
    gap: 10px;
    min-height: 30px;
  }
  .dlabel {
    font-size: var(--fs-small);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .dtext {
    font-size: var(--fs-cap);
    color: var(--text-2);
    text-align: right;
    white-space: nowrap;
  }
  .extra {
    margin-top: 3px;
    font-size: var(--fs-cap);
    line-height: 1.25;
  }
  .battery {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--text-2);
  }
</style>
