// Les scènes animées de l'économiseur d'écran, dessinées en direct : des paysages, des fonds marins, une ville de nuit
// vue du ciel, une aurore, des étoiles. Rien n'est téléchargé et rien ne pèse sur la carte du Pi : chaque scène est un
// petit programme qui dessine une image après l'autre, lentement, sur un canevas de 512×300 que le navigateur agrandit
// à l'écran. Sans image préenregistrée, sans flou ni ombre étendue : quelques dégradés et quelques centaines de formes.
//
// Les nombres au hasard viennent d'un générateur à graine (mulberry32) : une scène recommencée est la même, et les
// tests peuvent la rejouer.

import type { SceneId } from './saver'

export const SCENE_W = 512
export const SCENE_H = 300

const W = SCENE_W
const H = SCENE_H
const TAU = Math.PI * 2

export type Rng = () => number

/** Un générateur pseudo-aléatoire à graine : mêmes nombres pour la même graine. */
export function mulberry32(seed: number): Rng {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

type RGB = readonly [number, number, number]

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t
const mix = (a: RGB, b: RGB, t: number): RGB => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]
const css = (c: RGB, alpha = 1): string => `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${alpha})`
const mod = (a: number, n: number): number => ((a % n) + n) % n

/**
 * Une ligne de crêtes de `n` points entre 0 et 1, qui se raccorde à elle-même (le bout rejoint le début) : on la fait
 * défiler sans couture. Des bruits de valeur à plusieurs échelles, chaque échelle deux fois plus fine et moitié moins haute.
 */
export function ridgeline(rng: Rng, n: number, base = 4, octaves = 5, gain = 0.55): number[] {
  const lattices = Array.from({ length: octaves }, (_, o) => Array.from({ length: base * 2 ** o }, rng))
  const raw = Array.from({ length: n }, (_, i) => {
    let value = 0
    let amplitude = 1
    let total = 0
    for (const lattice of lattices) {
      const x = (i / n) * lattice.length
      const k = Math.floor(x)
      const f = x - k
      const s = f * f * (3 - 2 * f)
      value += amplitude * (lattice[k % lattice.length] * (1 - s) + lattice[(k + 1) % lattice.length] * s)
      total += amplitude
      amplitude *= gain
    }
    return value / total
  })
  const low = Math.min(...raw)
  const span = Math.max(...raw) - low || 1
  return raw.map((v) => (v - low) / span)
}

/** La hauteur d'une ligne de crêtes à l'abscisse `x` (entre ses points : interpolée), qui boucle sur `n`. */
function ridgeAt(points: number[], position: number): number {
  const n = points.length
  const k = Math.floor(position)
  const f = position - k
  return points[mod(k, n)] * (1 - f) + points[mod(k + 1, n)] * f
}

export interface Scene {
  draw(ctx: CanvasRenderingContext2D, t: number): void
}

export type MakeCanvas = (width: number, height: number) => HTMLCanvasElement

const defaultCanvas: MakeCanvas = (width, height) => {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  return canvas
}

/** Un petit nuage doux, dessiné une fois : des taches rondes qui s'estompent. */
function cloudSprite(rng: Rng, make: MakeCanvas): HTMLCanvasElement | null {
  const sprite = make(120, 44)
  const ctx = sprite.getContext('2d')
  if (!ctx) return null
  for (let i = 0; i < 9; i++) {
    const x = 20 + rng() * 80
    const y = 14 + rng() * 16
    const r = 10 + rng() * 14
    const g = ctx.createRadialGradient(x, y, 0, x, y, r)
    g.addColorStop(0, 'rgba(255,255,255,0.55)')
    g.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = g
    ctx.fillRect(x - r, y - r, r * 2, r * 2)
  }
  return sprite
}

// --- Montagnes, lever du jour --------------------------------------------------------------------------------------

function mountains(rng: Rng, make: MakeCanvas): Scene {
  const N = 512
  const layers = (
    [
      { y: 0.5, amp: 0.2, speed: 1.5, color: [255, 176, 150], haze: 0.7 },
      { y: 0.57, amp: 0.24, speed: 3, color: [226, 128, 148], haze: 0.5 },
      { y: 0.64, amp: 0.28, speed: 6, color: [160, 98, 138], haze: 0.34 },
      { y: 0.72, amp: 0.3, speed: 10, color: [92, 76, 128], haze: 0.2 },
      { y: 0.81, amp: 0.3, speed: 16, color: [42, 46, 92], haze: 0.1 },
      { y: 0.93, amp: 0.27, speed: 26, color: [15, 21, 46], haze: 0 },
    ] as const
  ).map((layer) => ({ ...layer, points: ridgeline(rng, N, 4, 6, 0.5) }))
  const cloud = cloudSprite(rng, make)
  const clouds = Array.from({ length: 5 }, () => ({ x: rng() * (W + 160), y: 20 + rng() * 80, speed: 1.5 + rng() * 2.5, scale: 0.8 + rng() * 0.9 }))

  return {
    draw(ctx, t) {
      const day = 0.5 + 0.5 * Math.sin(t / 40) // de l'aube (0) au crépuscule (1), en boucle
      const top = mix([22, 36, 88], [50, 30, 94], day)
      const horizon = mix([255, 168, 114], [255, 114, 150], day)
      const sky = ctx.createLinearGradient(0, 0, 0, H * 0.66)
      sky.addColorStop(0, css(top))
      sky.addColorStop(1, css(horizon))
      ctx.fillStyle = sky
      ctx.fillRect(0, 0, W, H)

      const sx = W * 0.68
      const sy = H * (0.5 - 0.06 * Math.sin(t / 40))
      const glow = ctx.createRadialGradient(sx, sy, 2, sx, sy, 150)
      glow.addColorStop(0, 'rgba(255,238,196,0.95)')
      glow.addColorStop(0.14, 'rgba(255,204,148,0.55)')
      glow.addColorStop(1, 'rgba(255,164,124,0)')
      ctx.fillStyle = glow
      ctx.fillRect(0, 0, W, H)

      if (cloud) {
        ctx.globalAlpha = 0.5
        for (const c of clouds) {
          const x = mod(c.x + t * c.speed, W + 240) - 120
          ctx.drawImage(cloud, x, c.y, 120 * c.scale, 44 * c.scale)
        }
        ctx.globalAlpha = 1
      }

      for (const layer of layers) {
        const offset = (t * layer.speed) / W // la crête avance d'une largeur d'image tous les W / vitesse secondes
        ctx.beginPath()
        ctx.moveTo(0, H)
        for (let x = 0; x <= W; x += 3) {
          const h = ridgeAt(layer.points, (x / W + offset) * N)
          ctx.lineTo(x, H * layer.y - h * H * layer.amp)
        }
        ctx.lineTo(W, H)
        ctx.closePath()
        ctx.fillStyle = css(mix(layer.color, horizon, layer.haze)) // la brume du lointain prend la couleur de l'horizon
        ctx.fill()
      }
    },
  }
}

// --- Fonds marins ----------------------------------------------------------------------------------------------------

function ocean(rng: Rng): Scene {
  const particles = Array.from({ length: 90 }, () => ({ x: rng() * W, y: rng() * H, speed: 2 + rng() * 6, sway: rng() * TAU, size: 0.5 + rng() * 1.6 }))
  const schools = [0, 1].map((s) => ({
    y: H * (0.32 + 0.2 * s),
    speed: 20 + 9 * s,
    dir: s === 0 ? 1 : -1,
    start: rng() * (W + 140),
    fish: Array.from({ length: 14 }, () => ({ dx: (rng() - 0.5) * 70, dy: (rng() - 0.5) * 34, phase: rng() * TAU, size: 4 + rng() * 2.5 })),
  }))
  const kelp = Array.from({ length: 9 }, (_, i) => ({ x: (i + 0.5) * (W / 9) + (rng() - 0.5) * 24, height: 60 + rng() * 70, phase: rng() * TAU }))
  const bed = ridgeline(rng, 128, 3, 4, 0.5)

  return {
    draw(ctx, t) {
      const water = ctx.createLinearGradient(0, 0, 0, H)
      water.addColorStop(0, 'rgb(28,126,168)')
      water.addColorStop(0.45, 'rgb(10,70,112)')
      water.addColorStop(1, 'rgb(3,18,38)')
      ctx.fillStyle = water
      ctx.fillRect(0, 0, W, H)

      // Des rais de lumière qui descendent de la surface et se déplacent doucement.
      const light = ctx.createLinearGradient(0, 0, 0, H * 0.9)
      light.addColorStop(0, 'rgba(190,240,255,0.2)')
      light.addColorStop(1, 'rgba(190,240,255,0)')
      ctx.fillStyle = light
      for (let i = 0; i < 7; i++) {
        const x = (W / 6) * i + 34 * Math.sin(t * 0.11 + i * 1.7) - 20
        const lean = 70 + 25 * Math.sin(t * 0.07 + i)
        ctx.beginPath()
        ctx.moveTo(x, 0)
        ctx.lineTo(x + 30 + 8 * Math.sin(t * 0.2 + i), 0)
        ctx.lineTo(x + 30 - lean, H * 0.9)
        ctx.lineTo(x - lean - 16, H * 0.9)
        ctx.closePath()
        ctx.fill()
      }

      ctx.fillStyle = 'rgba(210,240,255,0.55)'
      for (const p of particles) {
        const y = mod(p.y - t * p.speed, H)
        ctx.globalAlpha = 0.2 + 0.5 * (1 - y / H)
        ctx.fillRect(p.x + Math.sin(t * 0.5 + p.sway) * 8, y, p.size, p.size)
      }
      ctx.globalAlpha = 1

      ctx.fillStyle = 'rgba(6,26,42,0.82)'
      for (const school of schools) {
        const x = mod(school.start + t * school.speed * school.dir, W + 160) - 80
        for (const fish of school.fish) {
          const fx = x + fish.dx
          const fy = school.y + fish.dy + Math.sin(t * 1.3 + fish.phase) * 4
          ctx.beginPath()
          ctx.ellipse(fx, fy, fish.size, fish.size * 0.4, 0, 0, TAU)
          ctx.moveTo(fx - school.dir * fish.size * 0.8, fy)
          ctx.lineTo(fx - school.dir * fish.size * 1.7, fy - fish.size * 0.55)
          ctx.lineTo(fx - school.dir * fish.size * 1.7, fy + fish.size * 0.55)
          ctx.closePath()
          ctx.fill()
        }
      }

      ctx.beginPath()
      ctx.moveTo(0, H)
      for (let i = 0; i < 128; i++) ctx.lineTo((i / 127) * W, H * 0.93 - bed[i] * H * 0.07)
      ctx.lineTo(W, H)
      ctx.closePath()
      ctx.fillStyle = 'rgb(2,10,20)'
      ctx.fill()

      ctx.strokeStyle = 'rgba(10,64,56,0.9)'
      ctx.lineWidth = 2.6
      ctx.lineCap = 'round'
      for (const strand of kelp) {
        ctx.beginPath()
        ctx.moveTo(strand.x, H * 0.95)
        for (let k = 1; k <= 8; k++) {
          ctx.lineTo(strand.x + Math.sin(t * 0.9 + strand.phase + k * 0.5) * k * 1.7, H * 0.95 - (k / 8) * strand.height)
        }
        ctx.stroke()
      }
    },
  }
}

// --- Ville de nuit, vue aérienne -------------------------------------------------------------------------------------

function city(rng: Rng): Scene {
  const WW = 1024
  const WH = 768
  /** Des rues espacées au hasard, rangées pour que la dernière rejoigne la première : le quadrillage boucle. */
  const streets = (length: number): number[] => {
    const gaps: number[] = []
    let sum = 0
    while (sum < length - 30) {
      const gap = 24 + rng() * 40
      gaps.push(gap)
      sum += gap
    }
    const scale = length / sum
    let at = 0
    return gaps.map((gap) => (at += gap * scale) - gap * scale)
  }
  const xs = streets(WW)
  const ys = streets(WH)
  /** Les îlots entre les rues : chacun a sa clarté (un quartier animé, un parc presque noir) et ses fenêtres, groupées dedans. */
  const blocks = ys.flatMap((y, j) =>
    xs.map((x, i) => {
      const w = (xs[i + 1] ?? WW) - x
      const h = (ys[j + 1] ?? WH) - y
      const glow = rng() < 0.12 ? 0 : 0.25 + rng() * 0.75
      const lights = Array.from({ length: Math.round((glow * (w * h)) / 300) }, () => ({
        dx: 3 + rng() * Math.max(1, w - 6),
        dy: 3 + rng() * Math.max(1, h - 6),
        phase: rng() * TAU,
        size: 1 + rng() * 1.1,
      }))
      return { x, y, w, h, glow, phase: rng() * TAU, lights }
    }),
  )
  const cars = Array.from({ length: 220 }, () => {
    const horizontal = rng() < 0.5
    return {
      horizontal,
      road: Math.floor(rng() * (horizontal ? ys.length : xs.length)),
      start: rng() * (horizontal ? WW : WH),
      speed: (rng() < 0.5 ? -1 : 1) * (22 + rng() * 52),
      lane: rng() < 0.5 ? -1.4 : 1.4,
    }
  })

  return {
    draw(ctx, t) {
      const bg = ctx.createLinearGradient(0, 0, 0, H)
      bg.addColorStop(0, '#070a16')
      bg.addColorStop(1, '#0d0f1c')
      ctx.fillStyle = bg
      ctx.fillRect(0, 0, W, H)

      const cx = t * 5
      const cy = t * 3
      ctx.save()
      ctx.translate(W / 2, H / 2 + 14)
      ctx.rotate(-0.42) // le plan de la ville est vu de biais, comme depuis un hélicoptère
      ctx.scale(1.15, 0.64)

      // Les îlots : un halo chaud plus ou moins fort, puis leurs fenêtres. Un îlot à cheval sur la couture du
      // quadrillage (là où il boucle) est dessiné une seconde fois de l'autre côté.
      for (const b of blocks) {
        if (b.glow === 0) continue
        const bx = mod(b.x - cx, WW) - WW / 2
        const by = mod(b.y - cy, WH) - WH / 2
        const shimmer = 0.85 + 0.15 * Math.sin(t * 0.4 + b.phase)
        const fill = `rgba(255,${150 + Math.round(b.glow * 40)},70,${(0.05 + b.glow * 0.13 * shimmer).toFixed(3)})`
        for (const ox of bx + b.w > WW / 2 ? [0, -WW] : [0]) {
          for (const oy of by + b.h > WH / 2 ? [0, -WH] : [0]) {
            ctx.fillStyle = fill
            ctx.fillRect(bx + ox + 1.5, by + oy + 1.5, b.w - 3, b.h - 3)
            ctx.fillStyle = 'rgb(255,222,160)'
            for (const light of b.lights) {
              ctx.globalAlpha = 0.2 + 0.6 * Math.abs(Math.sin(t * 0.6 + light.phase))
              ctx.fillRect(bx + ox + light.dx, by + oy + light.dy, light.size, light.size)
            }
            ctx.globalAlpha = 1
          }
        }
      }

      // Les rues : un halo large et pâle dessous, le filet lumineux dessus (les grandes avenues, une sur cinq, plus vives).
      const strokeStreets = (style: string, width: number, only: (index: number) => boolean): void => {
        ctx.strokeStyle = style
        ctx.lineWidth = width
        ctx.beginPath()
        xs.forEach((x, i) => {
          if (!only(i)) return
          const lx = mod(x - cx, WW) - WW / 2
          ctx.moveTo(lx, -WH / 2)
          ctx.lineTo(lx, WH / 2)
        })
        ys.forEach((y, i) => {
          if (!only(i)) return
          const ly = mod(y - cy, WH) - WH / 2
          ctx.moveTo(-WW / 2, ly)
          ctx.lineTo(WW / 2, ly)
        })
        ctx.stroke()
      }
      const avenue = (i: number): boolean => i % 5 === 0
      const side = (i: number): boolean => i % 5 !== 0
      ctx.lineCap = 'butt'
      strokeStreets('rgba(255,176,84,0.09)', 7, avenue)
      strokeStreets('rgba(255,170,76,0.07)', 3.4, side)
      strokeStreets('rgba(255,218,150,0.8)', 2, avenue)
      strokeStreets('rgba(255,172,78,0.5)', 1, side)

      for (const car of cars) {
        const along = mod(car.start + car.speed * t, car.horizontal ? WW : WH)
        const head = car.speed > 0 ? 'rgb(255,248,220)' : 'rgb(255,70,52)'
        const dir = Math.sign(car.speed)
        let x: number
        let y: number
        if (car.horizontal) {
          x = mod(along - cx, WW) - WW / 2
          y = mod(ys[car.road] - cy, WH) - WH / 2 + car.lane
        } else {
          x = mod(xs[car.road] - cx, WW) - WW / 2 + car.lane
          y = mod(along - cy, WH) - WH / 2
        }
        ctx.strokeStyle = car.speed > 0 ? 'rgba(255,240,200,0.4)' : 'rgba(255,80,60,0.4)'
        ctx.lineWidth = 1
        ctx.beginPath()
        if (car.horizontal) {
          ctx.moveTo(x - dir * 9, y)
          ctx.lineTo(x, y)
        } else {
          ctx.moveTo(x, y - dir * 9)
          ctx.lineTo(x, y)
        }
        ctx.stroke()
        ctx.fillStyle = head
        ctx.fillRect(x - 1, y - 0.8, 2, 1.6)
      }
      ctx.restore()

      const vignette = ctx.createRadialGradient(W / 2, H / 2, H * 0.3, W / 2, H / 2, H * 0.95)
      vignette.addColorStop(0, 'rgba(0,0,0,0)')
      vignette.addColorStop(1, 'rgba(0,0,0,0.7)')
      ctx.fillStyle = vignette
      ctx.fillRect(0, 0, W, H)
    },
  }
}

// --- Aurore boréale ------------------------------------------------------------------------------------------------------

function aurora(rng: Rng): Scene {
  const stars = Array.from({ length: 150 }, () => ({ x: rng() * W, y: rng() * H * 0.7, size: 0.4 + rng() * 0.9, phase: rng() * TAU }))
  const curtains = (
    [
      { color: [70, 255, 160], base: 0.34, wave: 22 },
      { color: [50, 210, 235], base: 0.42, wave: 18 },
      { color: [160, 100, 255], base: 0.3, wave: 26 },
    ] as const
  ).map((c, i) => ({ ...c, shift: i * 2.1 + rng() * 2 }))
  const ridge = ridgeline(rng, 128, 4, 5, 0.5)
  const pines = Array.from({ length: 34 }, () => ({ x: rng() * W, size: 8 + rng() * 14 }))

  return {
    draw(ctx, t) {
      const sky = ctx.createLinearGradient(0, 0, 0, H)
      sky.addColorStop(0, '#02050f')
      sky.addColorStop(1, '#0a1a34')
      ctx.fillStyle = sky
      ctx.fillRect(0, 0, W, H)

      ctx.fillStyle = '#fff'
      for (const s of stars) {
        ctx.globalAlpha = 0.35 + 0.6 * Math.abs(Math.sin(t * 0.8 + s.phase))
        ctx.fillRect(s.x, s.y, s.size, s.size)
      }
      ctx.globalAlpha = 1

      ctx.globalCompositeOperation = 'lighter'
      for (const c of curtains) {
        const glow = ctx.createLinearGradient(0, H * 0.08, 0, H * 0.82)
        glow.addColorStop(0, css(c.color, 0))
        glow.addColorStop(0.38, css(c.color, 0.5))
        glow.addColorStop(1, css(c.color, 0))
        ctx.fillStyle = glow
        ctx.beginPath()
        const bottoms: number[] = []
        for (let x = 0; x <= W; x += 4) {
          const top = H * c.base + c.wave * Math.sin(x * 0.012 + t * 0.35 + c.shift) + c.wave * 0.5 * Math.sin(x * 0.031 - t * 0.5 + 2 * c.shift)
          ctx.lineTo(x, top)
          bottoms.push(top + H * (0.3 + 0.14 * Math.sin(x * 0.02 + t * 0.2 + c.shift)))
        }
        for (let i = bottoms.length - 1; i >= 0; i--) ctx.lineTo(i * 4, bottoms[i])
        ctx.closePath()
        ctx.fill()
      }
      ctx.globalCompositeOperation = 'source-over'

      ctx.fillStyle = '#02050b'
      ctx.beginPath()
      ctx.moveTo(0, H)
      for (let i = 0; i < 128; i++) ctx.lineTo((i / 127) * W, H * 0.9 - ridge[i] * H * 0.1)
      ctx.lineTo(W, H)
      ctx.closePath()
      ctx.fill()
      for (const p of pines) {
        const base = H * 0.9 - ridge[Math.min(127, Math.round((p.x / W) * 127))] * H * 0.1 + 2
        ctx.beginPath()
        ctx.moveTo(p.x - p.size * 0.32, base)
        ctx.lineTo(p.x, base - p.size)
        ctx.lineTo(p.x + p.size * 0.32, base)
        ctx.closePath()
        ctx.fill()
      }
    },
  }
}

// --- Voyage parmi les étoiles --------------------------------------------------------------------------------------------

function space(rng: Rng, make: MakeCanvas): Scene {
  const nebula = make(256, 150)
  const paint = nebula.getContext('2d')
  if (paint) {
    paint.globalCompositeOperation = 'lighter'
    const colors: RGB[] = [[130, 64, 210], [44, 96, 230], [34, 176, 176], [226, 84, 164], [92, 60, 200], [30, 120, 210]]
    for (const c of colors) {
      const x = rng() * 256
      const y = rng() * 150
      const r = 50 + rng() * 60
      const g = paint.createRadialGradient(x, y, 0, x, y, r)
      g.addColorStop(0, css(c, 0.3))
      g.addColorStop(1, css(c, 0))
      paint.fillStyle = g
      paint.fillRect(x - r, y - r, r * 2, r * 2)
    }
  }
  const star = () => ({ x: rng() * 2 - 1, y: rng() * 2 - 1, z: 0.05 + rng() * 0.95, tint: rng() })
  const stars = Array.from({ length: 260 }, star)
  let before = 0

  return {
    draw(ctx, t) {
      const dt = before === 0 ? 0 : Math.min(0.25, t - before)
      before = t
      ctx.fillStyle = '#02030a'
      ctx.fillRect(0, 0, W, H)
      ctx.globalAlpha = 0.85
      ctx.drawImage(nebula, -24 - 16 * Math.sin(t / 30), -12 - 8 * Math.cos(t / 25), W + 48, H + 24)
      ctx.globalAlpha = 1
      for (let i = 0; i < stars.length; i++) {
        const s = stars[i]
        s.z -= dt * 0.045
        const x = W / 2 + (s.x / s.z) * W * 0.18
        const y = H / 2 + (s.y / s.z) * H * 0.18
        if (s.z < 0.03 || x < -4 || x > W + 4 || y < -4 || y > H + 4) {
          stars[i] = { ...star(), z: 0.9 + rng() * 0.1 }
          continue
        }
        const size = 0.35 + (1 - s.z) * 1.8
        ctx.fillStyle = s.tint < 0.7 ? css([235, 240, 255], 0.35 + (1 - s.z) * 0.65) : s.tint < 0.9 ? css([170, 200, 255], 0.35 + (1 - s.z) * 0.65) : css([255, 220, 170], 0.35 + (1 - s.z) * 0.65)
        ctx.fillRect(x, y, size, size)
      }
    },
  }
}

/** La scène `id`, prête à dessiner. `seed` : sa graine (la même graine, la même scène) ; `make` : fabrique de canevas. */
export function createScene(id: SceneId, seed: number, make: MakeCanvas = defaultCanvas): Scene {
  const rng = mulberry32(seed)
  switch (id) {
    case 'mountains':
      return mountains(rng, make)
    case 'ocean':
      return ocean(rng)
    case 'city':
      return city(rng)
    case 'aurora':
      return aurora(rng)
    case 'space':
      return space(rng, make)
  }
}
