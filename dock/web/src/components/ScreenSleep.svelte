<script lang="ts">
  import { untrack } from 'svelte'
  import { screenSleep } from '../lib/screen.svelte'
  import { sock } from '../lib/socket.svelte'

  const delay = $derived(sock.ui?.screen_sleep ?? 0)
  $effect(() => {
    const seconds = delay
    untrack(() => screenSleep.setDelay(seconds))
  })

  // Tout toucher, geste ou touche repousse la veille : on ne note que l'heure, sans rien déclencher.
  $effect(() => {
    const used = () => screenSleep.activity()
    const events = ['pointerdown', 'pointermove', 'pointerup', 'wheel', 'keydown'] as const
    for (const name of events) window.addEventListener(name, used, { capture: true, passive: true })
    return () => {
      for (const name of events) window.removeEventListener(name, used, { capture: true })
    }
  })
</script>

{#if screenSleep.phase !== 'awake'}
  <!-- Le voile noir, puis (doigt posé) le même voile invisible, le temps d'avaler le reste du geste. -->
  <div
    class="veil"
    class:waking={screenSleep.phase === 'waking'}
    data-testid="screen-sleep"
    data-phase={screenSleep.phase}
    role="presentation"
    onpointerdown={() => screenSleep.wake()}
    onpointerup={() => screenSleep.released()}
    onpointercancel={() => screenSleep.released()}
  ></div>
{/if}

<style>
  .veil {
    position: fixed;
    inset: 0;
    z-index: 100;
    background: #000;
    cursor: none;
    touch-action: none;
  }
  .veil.waking {
    background: transparent;
  }
</style>
