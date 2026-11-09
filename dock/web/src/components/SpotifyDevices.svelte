<script lang="ts">
  import Airplay from '@lucide/svelte/icons/airplay'
  import Car from '@lucide/svelte/icons/car'
  import CircleAlert from '@lucide/svelte/icons/circle-alert'
  import Gamepad2 from '@lucide/svelte/icons/gamepad-2'
  import Laptop from '@lucide/svelte/icons/laptop'
  import MonitorSpeaker from '@lucide/svelte/icons/monitor-speaker'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import Smartphone from '@lucide/svelte/icons/smartphone'
  import Speaker from '@lucide/svelte/icons/speaker'
  import Tablet from '@lucide/svelte/icons/tablet'
  import Tv from '@lucide/svelte/icons/tv'
  import type { Component } from 'svelte'
  import { deviceIcon } from '../lib/audio'
  import { audio } from '../lib/audio.svelte'
  import { music } from '../lib/music.svelte'
  import type { SpotifyDevice, SpotifyDeviceType } from '../lib/protocol'
  import { accountView, canTransferTo, deviceSubtitle, deviceTitle } from '../lib/spotify'
  import { spotify } from '../lib/spotify.svelte'
  import OutputRow from './OutputRow.svelte'
  import SpotifyConnect from './SpotifyConnect.svelte'
  import SpotifyNotice from './SpotifyNotice.svelte'

  // La sortie sonore : où la musique sort. Deux listes : les appareils Spotify Connect du compte (ce PC, enceintes en
  // Wi-Fi, téléphones, télés : un toucher leur passe la musique) et les sorties du PC (haut-parleurs, casque, HDMI,
  // Bluetooth : celles que ce PC utilise quand la musique y joue).

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ICONS: Record<SpotifyDeviceType, Component<any>> = {
    Computer: Laptop,
    Tablet: Tablet,
    Smartphone: Smartphone,
    Speaker: Speaker,
    CastAudio: Speaker,
    CastVideo: Airplay,
    TV: Tv,
    AVR: MonitorSpeaker,
    STB: Tv,
    AudioDongle: Speaker,
    GameConsole: Gamepad2,
    Automobile: Car,
    Unknown: MonitorSpeaker,
  }

  const ready = $derived(accountView(spotify.status) === 'ready' && !spotify.statusError)
  const list = $derived(spotify.devices.state === 'ready' ? spotify.devices.data : [])
  const sinks = $derived(audio.data?.sinks ?? [])
  /** La musique joue sur un autre appareil que ce PC : ses sorties ne servent pas pour l'instant. */
  const elsewhere = $derived(spotify.output !== null && !spotify.output.this_pc)
  const playing = $derived(music.active?.status === 'playing')

  function pick(device: SpotifyDevice): void {
    if (device.active) return
    void spotify.transferTo(device, playing || music.active === null)
  }
</script>

{#if !ready}
  <SpotifyConnect />
{:else}
  <div class="head">
    <h1 data-testid="sp-devices-title">Sortie sonore</h1>
    <button class="refresh" aria-label="Actualiser la liste" data-testid="sp-devices-refresh" onclick={() => void spotify.refreshDevices()}>
      <RefreshCw size={36} aria-hidden="true" />
    </button>
  </div>

  <section data-testid="sp-devices">
    <h2>Appareils Spotify</h2>
    {#if spotify.devices.state === 'loading' || spotify.devices.state === 'idle'}
      <SpotifyNotice icon={RefreshCw} title="Recherche des appareils…" testid="sp-devices-loading" />
    {:else if spotify.devices.state === 'error'}
      <SpotifyNotice icon={CircleAlert} title="La liste n’a pas pu être lue" testid="sp-devices-error" tone="bad">
        <p data-testid="sp-devices-error-message">{spotify.devices.message}</p>
      </SpotifyNotice>
    {:else if list.length === 0}
      <SpotifyNotice icon={Speaker} title="Aucun appareil Spotify" testid="sp-devices-empty">
        <p>Ouvre Spotify sur le PC, ou allume ton enceinte : elle apparaît ici dès que Spotify la voit.</p>
      </SpotifyNotice>
    {:else}
      {#each list as device (device.id)}
        <OutputRow
          icon={ICONS[device.type]}
          title={deviceTitle(device)}
          subtitle={deviceSubtitle(device)}
          active={device.active}
          busy={spotify.switching === device.id}
          disabled={!canTransferTo(device) || (spotify.switching !== null && spotify.switching !== device.id)}
          testid="sp-device-{device.id}"
          onTap={() => pick(device)}
        />
      {/each}
    {/if}
  </section>

  {#if sinks.length > 0}
    <section class="pc" class:elsewhere data-testid="sp-sinks">
      <h2>Sorties du PC</h2>
      {#if elsewhere}
        <p class="note dim" data-testid="sp-sinks-note">Elles servent quand la musique joue sur ce PC.</p>
      {/if}
      {#each sinks as sink (sink.id)}
        <OutputRow
          icon={deviceIcon(sink.kind)}
          title={sink.label}
          subtitle={sink.detail ?? ''}
          active={audio.output?.id === sink.id}
          testid="sp-sink-{sink.id}"
          onTap={() => void audio.setDefault('sink', sink.id)}
        />
      {/each}
    </section>
  {/if}
{/if}

<style>
  .head {
    position: sticky;
    top: -14px;
    margin-top: -14px;
    padding-top: 14px;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    background: var(--bg);
  }
  h1 {
    font-size: var(--fs-title);
    font-weight: 700;
    letter-spacing: -0.01em;
    margin: 0 0 4px;
  }
  .refresh {
    display: grid;
    place-items: center;
    width: 84px;
    height: 84px;
    border-radius: 50%;
  }
  .refresh:active {
    background: var(--surface-3);
  }
  h2 {
    margin: 6px 8px 4px;
    font-size: var(--fs-small);
    font-weight: 700;
    color: var(--text-2);
  }
  .note {
    margin: 0 8px 4px;
    font-size: var(--fs-cap);
  }
  .pc.elsewhere {
    opacity: 0.6;
  }
</style>
