<script lang="ts">
  import AudioLines from '@lucide/svelte/icons/audio-lines'
  import { onMount } from 'svelte'
  import VolumeBar from '../components/VolumeBar.svelte'
  import { appHue, appInitial, appNames, deviceIcon, recordingSummary } from '../lib/audio'
  import { audio } from '../lib/audio.svelte'
  import type { AudioDevice } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'

  // La page suit le thème « audio » (sorties, micro, applications) tant qu'elle est ouverte.
  onMount(() => sock.subscribe(['audio']))

  const data = $derived(audio.data)
  const out = $derived(audio.output)
  const mic = $derived(audio.input)
  const sinks = $derived(data?.sinks ?? [])
  const sources = $derived(data?.sources ?? [])
  const apps = $derived(data?.apps ?? [])
  const names = $derived(appNames(apps))
  const recording = $derived(recordingSummary(data?.recorders ?? []))
</script>

{#snippet picker(devices: AudioDevice[], current: AudioDevice | null, kind: 'sink' | 'source', label: string, testid: string)}
  {#if devices.length > 1}
    <div class="chips" role="radiogroup" aria-label={label} data-testid="{testid}-devices">
      {#each devices as device (device.id)}
        {@const Icon = deviceIcon(device.kind)}
        <button
          class="btn chip"
          role="radio"
          aria-checked={device.id === current?.id}
          data-testid="{testid}-device-{device.id}"
          onclick={() => void audio.setDefault(kind, device.id)}
        >
          <Icon size={22} aria-hidden="true" />
          <span>{device.label}</span>
        </button>
      {/each}
    </div>
  {/if}
{/snippet}

{#if audio.unavailable}
  <section class="empty" data-testid="sound-unavailable">
    <AudioLines size={64} aria-hidden="true" />
    <h1>Son indisponible</h1>
    <p class="muted">{audio.unavailable}</p>
  </section>
{:else if !data}
  <p class="muted" data-testid="sound-loading">Lecture du son du PC…</p>
{:else}
  <div class="layout" data-testid="sound-page">
    <div class="left">
      <section class="card" data-testid="out-card">
        <div class="head">
          <h2>Sortie</h2>
          {#if out}<span class="name" data-testid="out-label" title={out.detail ?? undefined}>{out.label}</span>{/if}
        </div>
        {#if out}
          <VolumeBar kind="sink" id={out.id} name={out.label} volume={out.volume} muted={out.muted} size="lg" testid="out" />
          {@render picker(sinks, out, 'sink', 'Sortie audio', 'out')}
        {:else}
          <p class="muted" data-testid="out-none">Aucune sortie audio détectée. Branche des haut-parleurs ou un casque : le dock les trouve tout seul.</p>
        {/if}
      </section>

      <section class="card" data-testid="mic-card">
        <div class="head">
          <h2>Micro</h2>
          {#if mic}<span class="name" data-testid="mic-label" title={mic.detail ?? undefined}>{mic.label}</span>{/if}
        </div>
        {#if mic}
          <VolumeBar kind="source" id={mic.id} name={mic.label} volume={mic.volume} muted={mic.muted} icon="mic" testid="mic" />
          {@render picker(sources, mic, 'source', 'Entrée audio', 'mic')}
          {#if recording}
            <p class="rec" data-testid="mic-recording"><i aria-hidden="true"></i>Micro utilisé par {recording}</p>
          {:else}
            <p class="rec idle" data-testid="mic-idle">Aucune application n’écoute le micro</p>
          {/if}
        {:else}
          <p class="muted" data-testid="mic-none">Aucun micro détecté.</p>
        {/if}
      </section>
    </div>

    <section class="card apps" data-testid="apps-card">
      <div class="head">
        <h2>Applications{apps.length ? ` (${apps.length})` : ''}</h2>
      </div>
      <div class="list">
        {#each apps as app (app.id)}
          <div class="app" class:idle={!app.playing} data-testid="app-{app.id}">
            <span class="avatar" style:--hue={appHue(app.name)} aria-hidden="true">{appInitial(app.name)}</span>
            <span class="who">
              <strong data-testid="app-{app.id}-name">{names[app.id]}</strong>
              {#if !app.playing}<small>en pause</small>{/if}
            </span>
            <VolumeBar kind="app" id={app.id} name={names[app.id]} volume={app.volume} muted={app.muted} compact testid="app-{app.id}" />
          </div>
        {/each}
        {#if apps.length === 0}
          <p class="muted none" data-testid="apps-empty">Aucune application ne joue de son pour l’instant. Lance Spotify, une vidéo… : elle apparaît ici toute seule.</p>
        {/if}
      </div>
    </section>
  </div>
{/if}

<style>
  .layout {
    display: grid;
    grid-template-columns: 468px minmax(0, 1fr);
    gap: 12px;
    height: 100%;
    min-height: 0;
  }
  .left {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }
  .card {
    padding: 10px 16px 12px;
  }
  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 4px;
  }
  .head h2 {
    flex: none;
    font-size: var(--fs-small);
    font-weight: 600;
    color: var(--text-2);
    letter-spacing: 0.02em;
  }
  .name {
    min-width: 0;
    font-size: var(--fs-small);
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* --- choix de la sortie / du micro --- */
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
  .chip {
    min-height: 48px;
    max-width: 100%;
    padding: 0 14px;
    font-size: var(--fs-small);
  }
  .chip span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .chip[aria-checked='true'] {
    background: var(--accent-soft);
    color: var(--accent);
    box-shadow: inset 0 0 0 2px var(--accent);
  }

  /* --- micro --- */
  .rec {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 6px;
    font-size: var(--fs-cap);
    font-weight: 600;
    color: var(--bad);
  }
  .rec i {
    flex: none;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--bad);
  }
  .rec.idle {
    font-weight: 400;
    color: var(--text-3);
  }

  /* --- applications --- */
  .apps {
    display: flex;
    flex-direction: column;
    min-height: 0;
    padding-bottom: 6px;
  }
  .list {
    flex: 1 1 0;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .app {
    display: grid;
    grid-template-columns: 36px 112px minmax(0, 1fr);
    align-items: center;
    gap: 8px;
    min-height: 64px;
    border-top: 1px solid var(--line);
  }
  .app:first-child {
    border-top: 0;
  }
  .app.idle .avatar,
  .app.idle .who strong {
    opacity: 0.55;
  }
  .avatar {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    border-radius: 10px;
    background: hsl(var(--hue) 50% 28%);
    color: hsl(var(--hue) 85% 86%);
    font-size: var(--fs-small);
    font-weight: 700;
  }
  .who {
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.2;
  }
  .who strong {
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .who small {
    font-size: var(--fs-cap);
    color: var(--text-3);
  }
  .none {
    padding: 10px 2px;
  }

  .empty {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    text-align: center;
    color: var(--text-3);
  }
  .empty h1 {
    font-size: var(--fs-title);
    color: var(--text);
  }
  .empty p {
    max-width: 600px;
  }
</style>
