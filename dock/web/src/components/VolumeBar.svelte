<script lang="ts">
  import Mic from '@lucide/svelte/icons/mic'
  import MicOff from '@lucide/svelte/icons/mic-off'
  import { formatVolume, speakerIcon } from '../lib/audio'
  import { audio, type AudioTarget } from '../lib/audio.svelte'
  import Slider from './Slider.svelte'

  let {
    kind,
    id,
    name,
    volume,
    muted,
    icon = 'speaker',
    size = 'md',
    compact = false,
    testid,
  }: {
    kind: AudioTarget
    id: string
    /** Ce qu'on règle (« Haut-parleurs », « Spotify »…), pour les étiquettes d'accessibilité. */
    name: string
    volume: number | null
    muted: boolean
    icon?: 'speaker' | 'mic'
    size?: 'md' | 'lg'
    /** Rangée d'une liste : le curseur d'abord, le bouton muet à droite. */
    compact?: boolean
    testid: string
  } = $props()

  const isMuted = $derived(audio.muted(kind, id, muted))
  const Speaker = $derived(speakerIcon(volume, isMuted))
  const noun = $derived(icon === 'mic' ? 'le micro' : 'le son')

  function commit(value: number): void {
    audio.setVolume(kind, id, value)
    if (isMuted && value > 0) void audio.setMuted(kind, id, false) // bouger le curseur d'un son coupé le rétablit
  }
</script>

<div class="bar {size}" class:compact>
  <button
    class="mute"
    class:on={isMuted}
    aria-label="{isMuted ? 'Rétablir' : 'Couper'} {noun} ({name})"
    aria-pressed={isMuted}
    data-testid="{testid}-mute"
    onclick={() => void audio.setMuted(kind, id, !isMuted)}
  >
    {#if icon === 'mic'}
      {#if isMuted}<MicOff size={size === 'lg' ? 34 : 28} aria-hidden="true" />{:else}<Mic size={size === 'lg' ? 34 : 28} aria-hidden="true" />{/if}
    {:else}
      <Speaker size={size === 'lg' ? 34 : 28} aria-hidden="true" />
    {/if}
  </button>
  <Slider
    {size}
    value={volume}
    max={audio.max}
    label="Volume : {name}"
    valueText={formatVolume(volume, isMuted)}
    muted={isMuted}
    testid="{testid}-slider"
    onInput={(v) => audio.setVolume(kind, id, v)}
    onCommit={commit}
  />
  <span class="value" class:dim={isMuted} data-testid="{testid}-value">{formatVolume(volume, isMuted)}</span>
</div>

<style>
  .bar {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) 4.2em;
    align-items: center;
    gap: 10px;
  }
  .mute {
    display: grid;
    place-items: center;
    width: 52px;
    height: 52px;
    border-radius: 14px;
    background: var(--surface-3);
    color: var(--text);
  }
  .lg .mute {
    width: 56px;
    height: 56px;
  }
  .compact {
    grid-template-columns: minmax(0, 1fr) 3.4em auto;
  }
  .compact .mute {
    order: 3;
    width: 48px;
    height: 48px;
  }
  .compact .value {
    font-size: var(--fs-small);
  }
  .mute:active {
    background: var(--line);
    transform: scale(0.95);
  }
  .mute.on {
    background: var(--bad-soft);
    color: var(--bad);
    box-shadow: inset 0 0 0 1px var(--bad);
  }
  .value {
    text-align: right;
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
  }
  .lg .value {
    font-size: var(--fs-large);
  }
  .value.dim {
    color: var(--text-3);
  }
</style>
