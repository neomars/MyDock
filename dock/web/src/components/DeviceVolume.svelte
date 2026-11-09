<script lang="ts">
  import Volume1 from '@lucide/svelte/icons/volume-1'
  import Volume2 from '@lucide/svelte/icons/volume-2'
  import VolumeX from '@lucide/svelte/icons/volume-x'
  import { formatVolume } from '../lib/audio'
  import type { SpotifyDevice } from '../lib/protocol'
  import { deviceTitle } from '../lib/spotify'
  import { spotify } from '../lib/spotify.svelte'
  import Slider from './Slider.svelte'

  // Le volume d'un appareil Spotify Connect (une enceinte en Wi-Fi, un téléphone…) : Spotify le règle à distance.
  // Il n'est envoyé qu'au relâchement du doigt (chaque envoi est un appel chez Spotify, qui limite leur nombre).
  let { device }: { device: SpotifyDevice } = $props()

  const Icon = $derived((device.volume ?? 0) === 0 ? VolumeX : (device.volume ?? 0) < 50 ? Volume1 : Volume2)
</script>

<div class="bar" data-testid="device-volume">
  <span class="icon" aria-hidden="true"><Icon size={40} /></span>
  <Slider
    value={device.volume}
    label="Volume : {deviceTitle(device)}"
    valueText={formatVolume(device.volume, false)}
    testid="device-vol-slider"
    onCommit={(percent) => void spotify.setDeviceVolume(device, percent)}
  />
  <span class="value" data-testid="device-vol-value">{formatVolume(device.volume, false)}</span>
</div>

<style>
  .bar {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) 4.2em;
    align-items: center;
    gap: 10px;
  }
  .icon {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    border-radius: 16px;
    background: var(--surface-3);
    color: var(--text);
  }
  .value {
    text-align: right;
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
  }
</style>
