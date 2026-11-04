import { describe, expect, it } from 'vitest'
import { gridWidth, HOME_DEFAULTS, homeLayout } from './home'

// La place d'un écran de 1024 × 600 : 88 px de barre du haut, 16 px de marge tout autour.
const W = 992
const H = 480
const { gap, minWidth, minHeight, maxWidth, maxHeight } = HOME_DEFAULTS

describe('homeLayout', () => {
  it('neuf pages : trois colonnes sur trois rangées, de larges boutons', () => {
    expect(homeLayout(9, W, H)).toEqual({ cols: 3, rows: 3, width: 321, height: 150, gap })
  })
  it('huit ou sept pages : la même grille, avec une case vide', () => {
    expect(homeLayout(8, W, H)).toMatchObject({ cols: 3, rows: 3, width: 321, height: 150 })
    expect(homeLayout(7, W, H)).toMatchObject({ cols: 3, rows: 3, width: 321, height: 150 })
  })
  it('moins de pages, des boutons plus gros', () => {
    expect(homeLayout(6, W, H)).toMatchObject({ cols: 3, rows: 2, width: 321, height: 233 })
    expect(homeLayout(4, W, H)).toMatchObject({ cols: 2, rows: 2, width: 489, height: 233 })
  })
  it('trois pages : deux gros boutons sur la première rangée, le troisième centré dessous', () => {
    expect(homeLayout(3, W, H)).toMatchObject({ cols: 2, rows: 2, width: 489, height: 233 })
  })
  it('jamais plus grand que le plafond', () => {
    expect(homeLayout(2, W, H)).toMatchObject({ cols: 2, rows: 1, width: 489, height: maxHeight })
    expect(homeLayout(1, W, H)).toMatchObject({ cols: 1, rows: 1, width: maxWidth, height: maxHeight })
  })
  it('un bandeau au-dessus de la grille (association, PC éteint) rétrécit la hauteur, pas le nombre de colonnes', () => {
    expect(homeLayout(9, W, 374)).toMatchObject({ cols: 3, rows: 3, width: 321, height: 115 })
  })
  it('trop de boutons pour la place : on prend l’arrangement le moins serré, jamais rien', () => {
    const layout = homeLayout(12, W, 374)
    expect(layout.cols * layout.rows).toBeGreaterThanOrEqual(12)
    expect(layout.width).toBeGreaterThan(0)
    expect(layout.height).toBeGreaterThan(0)
  })
  it('tient toujours dans la place donnée, pour tous les nombres de boutons et plusieurs écrans', () => {
    for (const [width, height] of [[W, H], [W, 374], [800, 400], [1280, 700], [480, 320]] as const) {
      for (let count = 1; count <= 12; count++) {
        const layout = homeLayout(count, width, height)
        const label = `${count} boutons dans ${width}×${height}`
        expect(gridWidth(layout), label).toBeLessThanOrEqual(width)
        expect(layout.rows * layout.height + (layout.rows - 1) * layout.gap, label).toBeLessThanOrEqual(height)
        expect(layout.cols * layout.rows, label).toBeGreaterThanOrEqual(count)
        expect(layout.width, label).toBeLessThanOrEqual(maxWidth)
        expect(layout.height, label).toBeLessThanOrEqual(maxHeight)
      }
    }
  })
  it('quand la place le permet, chaque bouton a de quoi écrire son libellé et dessiner son icône', () => {
    for (let count = 1; count <= 9; count++) {
      const layout = homeLayout(count, W, H)
      expect(layout.width, `${count} boutons`).toBeGreaterThanOrEqual(minWidth)
      expect(layout.height, `${count} boutons`).toBeGreaterThanOrEqual(minHeight)
    }
  })
  it("n'agrandit jamais la surface d'un bouton quand on en ajoute", () => {
    let previous = Infinity
    for (let count = 1; count <= 9; count++) {
      const { width, height } = homeLayout(count, W, H)
      expect(width * height).toBeLessThanOrEqual(previous)
      previous = width * height
    }
  })
  it('ne calcule rien tant que la place est inconnue ou qu’il n’y a rien à montrer', () => {
    expect(homeLayout(9, 0, 0).width).toBe(0)
    expect(homeLayout(9, W, 0).height).toBe(0)
    expect(homeLayout(0, W, H).width).toBe(0)
    expect(homeLayout(-3, W, H).width).toBe(0)
  })
  it('une place minuscule donne des tailles nulles, jamais négatives', () => {
    const layout = homeLayout(9, 20, 20)
    expect(layout.width).toBeGreaterThanOrEqual(0)
    expect(layout.height).toBeGreaterThanOrEqual(0)
  })
  it('se règle : les tailles sont des paramètres', () => {
    expect(homeLayout(6, W, H, { maxHeight: 100 }).height).toBe(100)
    expect(homeLayout(9, W, H, { gap: 30 })).toMatchObject({ gap: 30, width: 310, height: 140 })
  })
})

describe('gridWidth', () => {
  it('est la largeur de la rangée pleine : le conteneur y centre la dernière rangée', () => {
    expect(gridWidth({ cols: 3, rows: 3, width: 321, height: 150, gap: 14 })).toBe(3 * 321 + 2 * 14)
    expect(gridWidth({ cols: 1, rows: 1, width: 200, height: 100, gap: 14 })).toBe(200)
  })
})
