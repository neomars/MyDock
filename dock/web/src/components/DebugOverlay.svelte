<script lang="ts">
  // ?debug=1 : images par seconde, état des liaisons et dernier aller-retour avec le dock.
  // À consulter sur le vrai Pi pour vérifier les 60 images/s visées.
  import { sock } from '../lib/socket.svelte'

  let fps = $state(0)
  let frames = 0
  let last = performance.now()

  $effect(() => {
    let raf = 0
    const loop = (now: number) => {
      frames += 1
      if (now - last >= 1000) {
        fps = Math.round((frames * 1000) / (now - last))
        frames = 0
        last = now
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  })
</script>

<div class="debug mono" data-testid="debug">
  {fps} i/s · WS {sock.status} · dock {sock.lastPingMs === null ? '–' : sock.lastPingMs.toFixed(1)} ms ·
  PC {sock.link?.state ?? '–'}
</div>

<style>
  .debug {
    position: fixed;
    top: 14px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 60;
    padding: 2px 8px;
    border-radius: 6px;
    background: rgb(0 0 0 / 0.75);
    font-size: 14px;
    pointer-events: none;
  }
</style>
