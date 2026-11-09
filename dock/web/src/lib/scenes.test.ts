import { describe, expect, it } from 'vitest'
import { SCENE_IDS, type SceneId } from './saver'
import { SCENE_H, SCENE_W, createScene, mulberry32, ridgeline } from './scenes'

/** Un faux contexte 2D qui accepte tout et se souvient du nombre d'appels : de quoi vérifier qu'une scène dessine. */
function fakeContext(): { ctx: CanvasRenderingContext2D; calls: { count: number; methods: Set<string> } } {
  const calls = { count: 0, methods: new Set<string>() }
  const make = (): unknown =>
    new Proxy(function () {}, {
      get: (_target, name) => {
        if (name === Symbol.toPrimitive) return () => 0
        return (...args: unknown[]) => {
          calls.count += 1
          calls.methods.add(String(name))
          void args
          return make()
        }
      },
      set: () => true,
      apply: () => make(),
    })
  return { ctx: make() as CanvasRenderingContext2D, calls }
}

const fakeCanvas = (): HTMLCanvasElement => ({ width: 0, height: 0, getContext: () => fakeContext().ctx }) as unknown as HTMLCanvasElement

describe('le hasard à graine', () => {
  it('redonne les mêmes nombres pour la même graine, d’autres pour une autre', () => {
    const a = mulberry32(42)
    const b = mulberry32(42)
    const first = Array.from({ length: 6 }, () => a())
    expect(Array.from({ length: 6 }, () => b())).toEqual(first)
    expect(Array.from({ length: 6 }, mulberry32(43))).not.toEqual(first)
    for (const v of first) expect(v).toBeGreaterThanOrEqual(0)
    for (const v of first) expect(v).toBeLessThan(1)
  })
})

describe('les crêtes', () => {
  it('tiennent entre 0 et 1, touchent les deux bords et se raccordent à elles-mêmes', () => {
    const line = ridgeline(mulberry32(7), 512)
    expect(line).toHaveLength(512)
    expect(Math.min(...line)).toBe(0)
    expect(Math.max(...line)).toBe(1)
    expect(Math.abs(line[0] - line[511])).toBeLessThan(0.08) // le bout rejoint le début : le défilement n'a pas de couture
  })
  it('sont les mêmes pour la même graine', () => {
    expect(ridgeline(mulberry32(3), 64)).toEqual(ridgeline(mulberry32(3), 64))
  })
})

describe.each(SCENE_IDS)('la scène « %s »', (id: SceneId) => {
  it('dessine, image après image, sans jamais échouer', () => {
    const { ctx, calls } = fakeContext()
    const scene = createScene(id, 1234, fakeCanvas)
    for (let t = 0; t < 400; t += 0.5) scene.draw(ctx, t)
    expect(calls.count).toBeGreaterThan(400 * 5)
    expect(calls.methods.has('fillRect') || calls.methods.has('fill')).toBe(true)
  })
  it('remplit toute l’image dès la première', () => {
    const rects: number[][] = []
    const { ctx } = fakeContext()
    const spy = new Proxy(ctx, {
      get: (target, name) => (name === 'fillRect' ? (...a: number[]) => void rects.push(a) : (target as never)[name]),
    }) as CanvasRenderingContext2D
    createScene(id, 9, fakeCanvas).draw(spy, 0)
    expect(rects.some(([x, y, w, h]) => x === 0 && y === 0 && w === SCENE_W && h === SCENE_H)).toBe(true)
  })
})
