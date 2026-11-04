// Le balayage de gauche à droite : on revient de n'importe quelle page au menu principal.
//
// On regarde le geste du premier appui au relâchement (pas pendant) : assez long, assez horizontal, assez
// rapide. Les curseurs (volume, progression) et tout ce qui se fait au doigt de gauche à droite sont
// laissés tranquilles : un balayage qui commence dessus est le leur, jamais celui de la navigation.

export interface SwipeOptions {
  /** Distance horizontale minimale, en pixels. */
  minDistance: number
  /** Un geste qui part du bord gauche de l'écran (le « retour » des téléphones) a besoin de moins. */
  edgeWidth: number
  edgeMinDistance: number
  /** Le geste doit être au moins aussi horizontal que « deux pour un » : un défilement oblique n'en est pas un. */
  horizontalRatio: number
  /** Au-delà, ce n'est plus un balayage mais un doigt qui traîne. */
  maxDurationMs: number
}

export const SWIPE: SwipeOptions = {
  minDistance: 140,
  edgeWidth: 48,
  edgeMinDistance: 90,
  horizontalRatio: 2,
  maxDurationMs: 1100,
}

export interface Sample {
  x: number
  y: number
  /** Heure, en millisecondes. */
  t: number
}

/** Vrai quand le geste est un balayage vers la droite. */
export function isSwipeRight(start: Sample, end: Sample, options: SwipeOptions = SWIPE): boolean {
  const dx = end.x - start.x
  const dy = end.y - start.y
  if (end.t - start.t > options.maxDurationMs) return false
  const needed = start.x <= options.edgeWidth ? options.edgeMinDistance : options.minDistance
  return dx >= needed && dx >= options.horizontalRatio * Math.abs(dy)
}

/** Ce qu'il faut savoir d'un élément sous le doigt pour décider s'il garde le geste. */
export interface Touched {
  tag: string
  role: string | null
  touchAction: string
  /** Le composant a demandé qu'on ne lui prenne pas ses balayages (`data-noswipe`). */
  noSwipe: boolean
}

const OWN_GESTURE = new Set(['input', 'textarea', 'select'])

/** Vrai si l'un des éléments, du plus profond au plus haut, garde le geste pour lui. */
export function keepsGesture(chain: readonly Touched[]): boolean {
  return chain.some(
    (el) => el.noSwipe || el.role === 'slider' || el.touchAction === 'none' || OWN_GESTURE.has(el.tag.toLowerCase()),
  )
}

/** Remonte du point touché jusqu'à `root` (exclu) et décrit chaque élément. */
export function chainOf(target: EventTarget | null, root: HTMLElement): Touched[] {
  const chain: Touched[] = []
  let el = target instanceof Element ? target : null
  while (el && el !== root) {
    chain.push({
      tag: el.tagName,
      role: el.getAttribute('role'),
      touchAction: getComputedStyle(el).touchAction,
      noSwipe: el.hasAttribute('data-noswipe'),
    })
    el = el.parentElement
  }
  return chain
}

// --- l'appui qui suit un balayage rapide -----------------------------------------------------------------------------
//
// Chromium croit qu'un balayage rapide laisse un défilement en cours (une « inertie ») et prend le premier appui qui
// suit pour l'ordre de l'arrêter : les événements « pointeur » de cet appui arrivent, mais le clic, lui, n'est jamais
// produit. Sur l'accueil, c'est le bouton qu'on vient toucher qui resterait sans réponse. Pendant un court moment après
// un balayage de retour, on rattrape donc cet appui : s'il n'a pas produit de clic, on le produit nous-mêmes.

/** Au-delà de ce délai après le balayage, plus rien à rattraper : un appui ordinaire. */
export const RESCUE_MS = 900
/** Le doigt qui bouge de plus que cela entre l'appui et le relâchement ne fait pas un appui. */
export const TAP_SLOP = 12
/** Délai laissé au navigateur pour produire lui-même le clic (il le fait dans la même image que le relâchement). */
export const CLICK_GRACE_MS = 80

export interface PointerPoint {
  id: number
  x: number
  y: number
}

/** Vrai quand le relâchement suit l'appui du même doigt, presque au même endroit. */
export function isTap(down: PointerPoint, up: PointerPoint, slop: number = TAP_SLOP): boolean {
  return down.id === up.id && Math.hypot(up.x - down.x, up.y - down.y) <= slop
}

/** Vrai quand un clic a eu lieu depuis le relâchement (à quelques millisecondes près) : le navigateur a fait son travail. */
export function clickFollowed(lastClickAt: number, upAt: number): boolean {
  return lastClickAt >= upAt - 5
}

const TAPPABLE = 'button:not(:disabled), a[href], [role="button"]'

let rescueUntil = 0
let pending: (PointerPoint & { target: EventTarget | null }) | null = null
let lastClickAt = -Infinity
let rescueInstalled = false

function installRescue(): void {
  if (rescueInstalled || typeof window === 'undefined') return
  rescueInstalled = true
  window.addEventListener('click', () => (lastClickAt = performance.now()), true)
  window.addEventListener(
    'pointerdown',
    (event) => {
      pending = null
      if (performance.now() > rescueUntil || event.pointerType === 'mouse' || !event.isPrimary) return
      pending = { id: event.pointerId, x: event.clientX, y: event.clientY, target: event.target }
    },
    true,
  )
  window.addEventListener(
    'pointerup',
    (event) => {
      const down = pending
      pending = null
      if (!down || !isTap(down, { id: event.pointerId, x: event.clientX, y: event.clientY })) return
      rescueUntil = 0 // un seul appui à rattraper
      const upAt = performance.now()
      setTimeout(() => {
        if (clickFollowed(lastClickAt, upAt)) return
        const button = down.target instanceof Element ? down.target.closest<HTMLElement>(TAPPABLE) : null
        button?.click()
      }, CLICK_GRACE_MS)
    },
    true,
  )
}

/** À appeler quand un balayage de retour vient d'être reconnu : le prochain appui sera rattrapé s'il perd son clic. */
export function expectSuppressedTap(): void {
  installRescue()
  rescueUntil = performance.now() + RESCUE_MS
}

/**
 * Action Svelte : appelle `onSwipe` quand un balayage vers la droite part de l'intérieur de l'élément.
 * Les événements « pointeur » couvrent le doigt comme la souris ; le défilement vertical reste au navigateur
 * (l'élément déclare `touch-action: pan-y`), qui annule alors le geste (`pointercancel`).
 */
export function swipeRight(node: HTMLElement, onSwipe: () => void) {
  let handler = onSwipe
  let start: (Sample & { id: number }) | null = null
  let swallowUntil = 0

  const end = (event: PointerEvent): void => {
    if (!start || event.pointerId !== start.id) return
    const from = start
    stop()
    if (isSwipeRight(from, { x: event.clientX, y: event.clientY, t: event.timeStamp })) {
      swallowUntil = performance.now() + 350 // le doigt se lève parfois sur un bouton : ce relâchement n'est pas un appui
      expectSuppressedTap()
      handler()
    }
  }
  const cancel = (event: PointerEvent): void => {
    if (start && event.pointerId === start.id) stop()
  }
  const stop = (): void => {
    start = null
    window.removeEventListener('pointerup', end, true)
    window.removeEventListener('pointercancel', cancel, true)
  }
  const down = (event: PointerEvent): void => {
    if (!event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) return
    stop()
    if (keepsGesture(chainOf(event.target, node))) return
    start = { x: event.clientX, y: event.clientY, t: event.timeStamp, id: event.pointerId }
    // Pour la souris, le relâchement peut avoir lieu hors de l'élément : on écoute la fenêtre.
    window.addEventListener('pointerup', end, true)
    window.addEventListener('pointercancel', cancel, true)
  }
  const click = (event: MouseEvent): void => {
    if (performance.now() < swallowUntil) {
      event.stopPropagation()
      event.preventDefault()
    }
  }

  node.addEventListener('pointerdown', down)
  node.addEventListener('click', click, true)
  return {
    update(next: () => void) {
      handler = next
    },
    destroy() {
      stop()
      node.removeEventListener('pointerdown', down)
      node.removeEventListener('click', click, true)
    },
  }
}
