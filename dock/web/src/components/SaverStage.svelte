<script lang="ts">
  import { onMount } from 'svelte'
  import { music } from '../lib/music.svelte'
  import { SCENE_SECONDS, SCENE_TITLES, VIDEO_MAX_SECONDS, clockText, videoTitle, type SaverItem } from '../lib/saver'
  import { saver } from '../lib/saver.svelte'
  import { SCENE_H, SCENE_W, createScene, type Scene } from '../lib/scenes'
  import { screenSleep } from '../lib/screen.svelte'

  // L'économiseur d'écran : une scène animée ou une vidéo du Pi plein écran, qui change de temps en temps avec un fondu
  // au noir, la légende en bas (l'heure, le nom de la vue, le morceau qui joue). Un toucher le ferme sans rien déclencher
  // dessous (voir lib/screen.svelte.ts). Tout s'arrête quand il disparaît : minuteurs, dessin, décodage de la vidéo.

  const FADE_MS = 900
  const FRAME_MS = 1000 / 24 // 24 images par seconde suffisent à une scène lente, et ménagent le Pi

  let canvas = $state<HTMLCanvasElement>()
  let video = $state<HTMLVideoElement>()
  let item = $state<SaverItem | null>(null)
  let shown = $state(false)
  let now = $state(new Date())

  let scene: Scene | null = null
  let context: CanvasRenderingContext2D | null = null
  let started = 0
  let lastFrame = 0
  let frame = 0
  let alive = true
  let advanceTimer: ReturnType<typeof setTimeout> | undefined
  let swapTimer: ReturnType<typeof setTimeout> | undefined

  const clock = $derived(clockText(now))
  const title = $derived(item === null ? '' : item.kind === 'scene' ? SCENE_TITLES[item.id] : videoTitle(item.name))
  const song = $derived(music.active?.status === 'playing' ? music.active : null)

  function loop(stamp: number): void {
    frame = requestAnimationFrame(loop)
    if (!scene || !context || stamp - lastFrame < FRAME_MS) return
    lastFrame = stamp
    scene.draw(context, (stamp - started) / 1000)
  }

  function releaseVideo(): void {
    if (!video) return
    video.pause()
    video.removeAttribute('src')
    video.load() // libère le décodeur
  }

  function begin(next: SaverItem): void {
    clearTimeout(advanceTimer)
    cancelAnimationFrame(frame)
    item = next
    if (next.kind === 'scene') {
      releaseVideo()
      context = context ?? canvas?.getContext('2d', { alpha: false }) ?? null
      scene = createScene(next.id, Math.floor(Math.random() * 2 ** 31))
      started = performance.now()
      lastFrame = 0
      frame = requestAnimationFrame(loop)
      shown = true
      advanceTimer = setTimeout(advance, SCENE_SECONDS * 1_000)
    } else {
      scene = null
      if (!video) return advance()
      video.src = `/screensaver/${encodeURIComponent(next.name)}`
      video
        .play()
        .then(() => {
          if (alive) shown = true
        })
        .catch((error: unknown) => {
          // Une lecture interrompue (l'économiseur se ferme, une autre image arrive) n'est pas une vidéo cassée.
          if (error instanceof DOMException && error.name === 'AbortError') return
          if (item?.kind === 'video' && item.name === next.name) failed(next.name)
        })
      advanceTimer = setTimeout(advance, VIDEO_MAX_SECONDS * 1_000)
    }
  }

  /** Fondu au noir, puis ce qui vient ensuite. */
  function advance(): void {
    if (!alive) return
    shown = false
    clearTimeout(swapTimer)
    swapTimer = setTimeout(() => {
      if (alive) begin(saver.next(item))
    }, FADE_MS)
  }

  function failed(name: string): void {
    if (!alive) return // l'économiseur est déjà fermé : un arrêt de lecture n'est pas une vidéo illisible
    saver.markBad(name) // ce navigateur ne sait pas la lire : on ne la remontre pas
    advance()
  }

  onMount(() => {
    const clockTimer = setInterval(() => (now = new Date()), 15_000)
    void saver.refresh().finally(() => {
      if (alive) begin(saver.next(null))
    })
    return () => {
      alive = false
      clearInterval(clockTimer)
      clearTimeout(advanceTimer)
      clearTimeout(swapTimer)
      cancelAnimationFrame(frame)
      releaseVideo()
    }
  })
</script>

<div
  class="saver"
  data-testid="screensaver"
  data-kind={item?.kind ?? ''}
  data-item={item === null ? '' : item.kind === 'scene' ? item.id : item.name}
  role="presentation"
  onpointerdown={() => screenSleep.wake()}
  onpointerup={() => screenSleep.released()}
  onpointercancel={() => screenSleep.released()}
>
  <canvas
    bind:this={canvas}
    class="layer"
    class:on={shown && item?.kind === 'scene'}
    class:gone={item?.kind !== 'scene'}
    width={SCENE_W}
    height={SCENE_H}
  ></canvas>
  <video
    bind:this={video}
    class="layer"
    class:on={shown && item?.kind === 'video'}
    class:gone={item?.kind !== 'video'}
    muted
    playsinline
    preload="auto"
    onended={advance}
    onerror={() => item?.kind === 'video' && failed(item.name)}
  ></video>
  <div class="shade" class:on={shown}></div>
  <div class="caption" class:on={shown}>
    <div class="time" data-testid="saver-time">{clock.time}</div>
    <div class="date">{clock.date}</div>
    <div class="title" data-testid="saver-title">{title}</div>
    {#if song}
      <div class="song" data-testid="saver-song">♪ {song.title ?? 'Sans titre'}{song.artist ? ` · ${song.artist}` : ''}</div>
    {/if}
  </div>
</div>

<style>
  .saver {
    position: fixed;
    inset: 0;
    z-index: 99; /* au-dessus de tout, sous le voile noir de la veille (100) */
    background: #000;
    overflow: hidden;
    cursor: none;
    touch-action: none;
  }
  .layer {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    opacity: 0;
    transition: opacity 900ms ease;
  }
  .layer.on {
    opacity: 1;
  }
  .gone {
    display: none; /* ce qui ne sert pas ne dessine ni ne décode rien */
  }
  .shade {
    position: absolute;
    inset: auto 0 0 0;
    height: 46%;
    background: linear-gradient(to top, rgb(0 0 0 / 0.62), rgb(0 0 0 / 0));
    opacity: 0;
    transition: opacity 1500ms ease 600ms;
  }
  .shade.on {
    opacity: 1;
  }
  .caption {
    position: absolute;
    left: 40px;
    bottom: 30px;
    right: 40px;
    color: rgb(255 255 255 / 0.92);
    opacity: 0;
    transition: opacity 1500ms ease 800ms;
  }
  .caption.on {
    opacity: 1;
  }
  .time {
    font-size: 96px;
    font-weight: 300;
    line-height: 1;
    letter-spacing: -0.02em;
  }
  .date {
    margin-top: 4px;
    font-size: var(--fs-cap);
    text-transform: capitalize;
    opacity: 0.8;
  }
  .title {
    margin-top: 10px;
    font-size: var(--fs-small);
    font-weight: 500;
    opacity: 0.85;
  }
  .song {
    margin-top: 4px;
    font-size: var(--fs-cap);
    opacity: 0.7;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>
