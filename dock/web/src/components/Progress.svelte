<script lang="ts">
  import { formatTrackTime } from '../lib/format'
  import { progressRatio, ratioToMs } from '../lib/music'

  let {
    position,
    length,
    seekable,
    onSeek,
  }: {
    position: number | null
    length: number | null
    seekable: boolean
    onSeek: (ms: number) => void
  } = $props()

  let track = $state<HTMLDivElement>()
  /** Pendant le glissement (et un instant après) c'est le doigt qui décide, pas le lecteur. */
  let drag = $state<number | null>(null)
  let release: ReturnType<typeof setTimeout> | undefined

  const interactive = $derived(seekable && length !== null && length > 0)
  const ratio = $derived(drag ?? progressRatio(position, length))
  const shown = $derived(drag !== null && length ? ratioToMs(drag, length) : position)

  function ratioAt(event: PointerEvent): number {
    const rect = track!.getBoundingClientRect()
    return (event.clientX - rect.left) / rect.width
  }

  function down(event: PointerEvent): void {
    if (!interactive) return
    clearTimeout(release)
    track!.setPointerCapture(event.pointerId)
    drag = Math.max(0, Math.min(1, ratioAt(event)))
  }

  function move(event: PointerEvent): void {
    if (drag !== null && track?.hasPointerCapture(event.pointerId)) {
      drag = Math.max(0, Math.min(1, ratioAt(event)))
    }
  }

  function up(event: PointerEvent): void {
    if (drag === null) return
    const target = Math.max(0, Math.min(1, ratioAt(event)))
    onSeek(ratioToMs(target, length ?? 0))
    drag = target
    release = setTimeout(() => (drag = null), 900) // le temps que le lecteur confirme la nouvelle position
  }

  function cancel(): void {
    drag = null
  }
</script>

<div class="wrap">
  <span class="time" data-testid="time-elapsed">{shown !== null ? formatTrackTime(shown) : '–:––'}</span>
  <div
    bind:this={track}
    class="track"
    class:interactive
    role="slider"
    tabindex={interactive ? 0 : -1}
    aria-label="Position dans le morceau"
    aria-valuemin={0}
    aria-valuemax={length ?? 0}
    aria-valuenow={shown ?? 0}
    aria-disabled={!interactive}
    data-testid="progress"
    onpointerdown={down}
    onpointermove={move}
    onpointerup={up}
    onpointercancel={cancel}
  >
    <div class="rail">
      <div class="fill" style:width="{ratio * 100}%"></div>
    </div>
    {#if interactive}<div class="thumb" class:dragging={drag !== null} style:left="{ratio * 100}%"></div>{/if}
  </div>
  <span class="time end" data-testid="time-total">{length ? formatTrackTime(length) : ''}</span>
</div>

<style>
  /* Comme dans l'application officielle : le temps écoulé, la barre, la durée, sur une seule ligne (la page tient ainsi
     dans l'écran). L'espace de chaque côté de la barre laisse la place à la poignée, qui déborde de 20 px à chaque bout. */
  .wrap {
    display: flex;
    align-items: center;
    gap: 26px;
    width: 100%;
  }
  .track {
    position: relative;
    flex: 1 1 0;
    min-width: 0;
    height: 56px;
    display: flex;
    align-items: center;
    touch-action: none;
  }
  .rail {
    width: 100%;
    height: 16px;
    border-radius: 999px;
    background: var(--surface-3);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--accent);
    border-radius: 999px;
  }
  .thumb {
    position: absolute;
    top: 50%;
    width: 40px;
    height: 40px;
    margin: -20px 0 0 -20px;
    border-radius: 50%;
    background: var(--text);
    box-shadow: 0 0 0 4px var(--bg);
  }
  .thumb.dragging {
    width: 52px;
    height: 52px;
    margin: -26px 0 0 -26px;
    background: var(--accent);
  }
  .time {
    flex: none;
    min-width: 2.4em;
    font-size: var(--fs-small);
    color: var(--text-2);
  }
  .time.end {
    text-align: right;
  }
</style>
