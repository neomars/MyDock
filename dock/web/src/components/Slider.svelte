<script lang="ts">
  import { clampVolume, keyVolume, ratioToVolume, volumeToRatio } from '../lib/audio'

  let {
    value,
    max = 100,
    label,
    valueText,
    muted = false,
    disabled = false,
    size = 'md',
    testid,
    onInput,
    onCommit,
  }: {
    /** Valeur publiée par l'agent ; null tant qu'elle est inconnue. */
    value: number | null
    max?: number
    label: string
    /** Texte lu par les lecteurs d'écran (« 54 % », « Muet »). */
    valueText?: string
    muted?: boolean
    disabled?: boolean
    size?: 'md' | 'lg'
    testid?: string
    /** Appelée à chaque mouvement du doigt. */
    onInput?: (value: number) => void
    /** Appelée au relâchement, avec la valeur finale. */
    onCommit: (value: number) => void
  } = $props()

  const THUMB = { md: 30, lg: 38 }

  let track = $state<HTMLDivElement>()
  /** Pendant le glissement c'est le doigt qui décide... */
  let drag = $state<number | null>(null)
  /** ... puis la valeur choisie reste affichée le temps que l'agent la confirme. */
  let held = $state<number | null>(null)
  let releaseTimer: ReturnType<typeof setTimeout> | undefined

  const interactive = $derived(!disabled && value !== null)
  const shown = $derived(drag ?? held ?? value ?? 0)
  const ratio = $derived(volumeToRatio(shown, max))

  $effect(() => {
    if (held !== null && value === held) held = null // l'agent a rattrapé le doigt
  })

  function valueAt(event: PointerEvent): number {
    const rect = track!.getBoundingClientRect()
    const pad = THUMB[size] / 2
    const usable = rect.width - 2 * pad
    return ratioToVolume(usable > 0 ? (event.clientX - rect.left - pad) / usable : 0, max)
  }

  function push(next: number): void {
    if (next === drag) return
    drag = next
    onInput?.(next)
  }

  function down(event: PointerEvent): void {
    if (!interactive) return
    clearTimeout(releaseTimer)
    held = null
    track!.setPointerCapture(event.pointerId)
    push(valueAt(event))
  }

  function move(event: PointerEvent): void {
    if (drag !== null && track?.hasPointerCapture(event.pointerId)) push(valueAt(event))
  }

  function settle(final: number): void {
    held = final
    clearTimeout(releaseTimer)
    releaseTimer = setTimeout(() => (held = null), 1_200)
  }

  function up(event: PointerEvent): void {
    if (drag === null) return
    const final = valueAt(event)
    drag = null
    settle(final)
    onCommit(final)
  }

  function cancel(): void {
    drag = null
  }

  function key(event: KeyboardEvent): void {
    if (!interactive) return
    const next = keyVolume(event.key, clampVolume(shown, max), max)
    if (next === null) return
    event.preventDefault()
    settle(next)
    onInput?.(next)
    onCommit(next)
  }
</script>

<div
  bind:this={track}
  class="track {size}"
  class:muted
  class:dragging={drag !== null}
  class:off={!interactive}
  role="slider"
  tabindex={interactive ? 0 : -1}
  aria-label={label}
  aria-valuemin={0}
  aria-valuemax={max}
  aria-valuenow={Math.round(shown)}
  aria-valuetext={valueText}
  aria-disabled={!interactive}
  data-testid={testid}
  style:--r={ratio}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={cancel}
  onkeydown={key}
>
  <div class="rail"><div class="fill"></div></div>
  {#if value !== null}<div class="thumb"></div>{/if}
</div>

<style>
  .track {
    --rail: 12px;
    --thumb: 30px;
    --hit: 52px;
    --pad: calc(var(--thumb) / 2);
    position: relative;
    display: flex;
    align-items: center;
    height: var(--hit);
    min-width: 0;
    touch-action: none;
    border-radius: 12px;
  }
  .track.lg {
    --rail: 16px;
    --thumb: 38px;
    --hit: 56px;
  }
  .track:focus-visible {
    outline: 3px solid var(--accent);
    outline-offset: 2px;
  }
  .rail {
    width: 100%;
    height: var(--rail);
    border-radius: 999px;
    background: var(--surface-3);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    width: calc(var(--pad) + (100% - 2 * var(--pad)) * var(--r));
    border-radius: 999px;
    background: var(--accent);
  }
  .thumb {
    position: absolute;
    top: 50%;
    left: calc(var(--pad) + (100% - 2 * var(--pad)) * var(--r));
    width: var(--thumb);
    height: var(--thumb);
    margin: calc(var(--thumb) / -2) 0 0 calc(var(--thumb) / -2);
    border-radius: 50%;
    background: var(--text);
    border: 1px solid rgb(0 0 0 / 0.35);
  }
  .dragging .thumb {
    background: var(--accent);
  }
  .muted .fill {
    background: var(--text-3);
  }
  .off {
    opacity: 0.45;
  }
</style>
