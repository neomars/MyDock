import { describe, expect, it } from 'vitest'
import { clickFollowed, isSwipeRight, isTap, keepsGesture, RESCUE_MS, SWIPE, TAP_SLOP, type Touched } from './swipe'

const at = (x: number, y: number, t = 0) => ({ x, y, t })

describe('isSwipeRight', () => {
  it('reconnaît un balayage franc de gauche à droite', () => {
    expect(isSwipeRight(at(300, 300), at(600, 310, 250))).toBe(true)
  })
  it('exige une vraie distance', () => {
    expect(isSwipeRight(at(300, 300), at(300 + SWIPE.minDistance - 1, 300, 200))).toBe(false)
    expect(isSwipeRight(at(300, 300), at(300 + SWIPE.minDistance, 300, 200))).toBe(true)
  })
  it('refuse un geste vers la gauche, ou qui ne bouge pas', () => {
    expect(isSwipeRight(at(600, 300), at(300, 300, 200))).toBe(false)
    expect(isSwipeRight(at(300, 300), at(300, 300, 200))).toBe(false)
  })
  it('refuse un geste trop vertical : c’est un défilement', () => {
    expect(isSwipeRight(at(300, 100), at(500, 400, 300))).toBe(false) // 200 à droite, 300 en bas
    expect(isSwipeRight(at(300, 300), at(500, 401, 300))).toBe(false) // un peu plus de un pour deux
    expect(isSwipeRight(at(300, 300), at(500, 400, 300))).toBe(true) // exactement deux pour un
  })
  it('refuse un doigt qui traîne', () => {
    expect(isSwipeRight(at(300, 300), at(700, 300, SWIPE.maxDurationMs + 1))).toBe(false)
    expect(isSwipeRight(at(300, 300), at(700, 300, SWIPE.maxDurationMs))).toBe(true)
  })
  it('demande moins de distance quand le geste part du bord gauche', () => {
    expect(isSwipeRight(at(10, 300), at(10 + SWIPE.edgeMinDistance, 300, 200))).toBe(true)
    expect(isSwipeRight(at(10, 300), at(10 + SWIPE.edgeMinDistance - 1, 300, 200))).toBe(false)
    expect(isSwipeRight(at(SWIPE.edgeWidth + 1, 300), at(SWIPE.edgeWidth + 1 + SWIPE.edgeMinDistance, 300, 200))).toBe(false)
  })
  it('se règle : les seuils sont des paramètres', () => {
    const strict = { ...SWIPE, minDistance: 400 }
    expect(isSwipeRight(at(300, 300), at(600, 300, 200), strict)).toBe(false)
  })
})

describe('keepsGesture', () => {
  const el = (extra: Partial<Touched> = {}): Touched => ({ tag: 'DIV', role: null, touchAction: 'auto', noSwipe: false, ...extra })

  it('laisse passer un appui sur un bouton, une tuile, du texte', () => {
    expect(keepsGesture([el({ tag: 'BUTTON' }), el(), el({ tag: 'SECTION' })])).toBe(false)
    expect(keepsGesture([])).toBe(false)
  })
  it('garde le geste pour un curseur, où qu’il soit dans la chaîne', () => {
    expect(keepsGesture([el({ tag: 'DIV' }), el({ role: 'slider' })])).toBe(true)
  })
  it('garde le geste pour tout ce qui déclare « touch-action: none » (progression, graphiques)', () => {
    expect(keepsGesture([el({ touchAction: 'none' })])).toBe(true)
    expect(keepsGesture([el({ touchAction: 'pan-y' })])).toBe(false)
  })
  it('garde le geste pour les champs de saisie et ce qui le demande', () => {
    for (const tag of ['INPUT', 'TEXTAREA', 'SELECT', 'input']) expect(keepsGesture([el({ tag })]), tag).toBe(true)
    expect(keepsGesture([el({ noSwipe: true })])).toBe(true)
  })
})

describe("l'appui rattrapé après un balayage rapide", () => {
  it('un appui est le même doigt, presque au même endroit', () => {
    expect(isTap({ id: 1, x: 500, y: 300 }, { id: 1, x: 503, y: 298 })).toBe(true)
    expect(isTap({ id: 1, x: 500, y: 300 }, { id: 1, x: 500 + TAP_SLOP, y: 300 })).toBe(true)
  })
  it('un doigt qui a bougé, ou un autre doigt, ne fait pas un appui', () => {
    expect(isTap({ id: 1, x: 500, y: 300 }, { id: 1, x: 500 + TAP_SLOP + 1, y: 300 })).toBe(false)
    expect(isTap({ id: 1, x: 500, y: 300 }, { id: 2, x: 500, y: 300 })).toBe(false)
  })
  it('le navigateur a fait son travail quand un clic suit le relâchement', () => {
    expect(clickFollowed(1_000, 1_000)).toBe(true)
    expect(clickFollowed(1_020, 1_000)).toBe(true)
    expect(clickFollowed(998, 1_000)).toBe(true) // quelques millisecondes d'écart d'horloge
  })
  it("sans clic depuis le relâchement, c'est à nous de le produire", () => {
    expect(clickFollowed(-Infinity, 1_000)).toBe(false)
    expect(clickFollowed(900, 1_000)).toBe(false)
  })
  it("le rattrapage ne dure qu'un instant", () => {
    expect(RESCUE_MS).toBeGreaterThan(500) // assez pour couvrir l'inertie que Chromium croit voir
    expect(RESCUE_MS).toBeLessThan(2_000) // pas assez pour toucher un appui sans rapport
  })
})
