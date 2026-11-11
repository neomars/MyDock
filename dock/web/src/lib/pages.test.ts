import { describe, expect, it } from 'vitest'
import { HOME_PAGES, PAGES, movePage, pageById, pageRows, pagesSummary, toggleHidden, visiblePages } from './pages'

describe('pages', () => {
  it('suivent l’ordre du cahier des charges', () => {
    expect(PAGES.map((p) => p.id)).toEqual([
      'music',
      'sound',
      'apps',
      'steam',
      'office',
      'markets',
      'system',
      'media',
      'power',
      'settings',
    ])
  })

  it('l’Alimentation n’a plus de gros bouton : « Divertissement » prend sa place à l’accueil', () => {
    expect(HOME_PAGES.map((p) => p.id)).toEqual(['music', 'sound', 'apps', 'steam', 'office', 'markets', 'system', 'media', 'settings'])
    expect(pageById('power').title).toBe('Alimentation') // la page existe toujours : la barre du haut l’ouvre
    expect(pageById('media').tab).toBe('Divertissement')
  })

  it('seules la Bourse, le Divertissement, l’Alimentation (Wake-on-LAN) et les Réglages servent PC éteint', () => {
    expect(PAGES.filter((p) => !p.needsPc).map((p) => p.id)).toEqual(['markets', 'media', 'power', 'settings'])
  })

  it('pageById retombe sur la première page pour un identifiant inconnu', () => {
    expect(pageById('steam').title).toBe('Steam')
    expect(pageById('nope' as never).id).toBe('music')
  })

  it('visiblePages applique l’ordre puis laisse les autres dans l’ordre par défaut', () => {
    const ids = visiblePages(['markets', 'system'], []).map((p) => p.id)
    expect(ids.slice(0, 2)).toEqual(['markets', 'system'])
    expect(ids.slice(2)).toEqual(['music', 'sound', 'apps', 'steam', 'office', 'media', 'settings'])
  })

  it('on ne peut jamais cacher les Réglages', () => {
    const ids = visiblePages([], ['settings', 'steam']).map((p) => p.id)
    expect(ids).toContain('settings')
    expect(ids).not.toContain('steam')
  })
})

describe('pageRows', () => {
  it('liste les neuf pages de l’accueil, cachées comprises, dans l’ordre effectif (jamais l’Alimentation)', () => {
    const rows = pageRows(['media', 'music'], ['music', 'steam'])
    expect(rows.map((r) => r.page.id).slice(0, 3)).toEqual(['media', 'music', 'sound'])
    expect(rows).toHaveLength(9)
    expect(rows.map((r) => r.page.id)).not.toContain('power')
    expect(rows.filter((r) => !r.visible).map((r) => r.page.id)).toEqual(['music', 'steam'])
  })

  it('montre toujours les Réglages, même s’ils figurent parmi les pages cachées', () => {
    expect(pageRows([], ['settings']).find((r) => r.page.id === 'settings')?.visible).toBe(true)
  })

  it('ignore les identifiants inconnus et les doublons de la configuration', () => {
    const rows = pageRows(['nope' as never, 'system'], [])
    expect(rows.map((r) => r.page.id)).toHaveLength(9)
    expect(new Set(rows.map((r) => r.page.id)).size).toBe(9)
  })
})

describe('movePage', () => {
  it('donne l’ordre complet des neuf pages après un déplacement', () => {
    const next = movePage([], [], 'media', -1)
    expect(next).toHaveLength(9)
    expect(next.slice(5, 9)).toEqual(['markets', 'media', 'system', 'settings'])
    expect(movePage(next, [], 'media', 1).slice(5, 9)).toEqual(['markets', 'system', 'media', 'settings'])
  })

  it('ne bouge pas une page au bord de la liste', () => {
    expect(movePage([], [], 'music', -1)[0]).toBe('music')
    expect(movePage([], [], 'settings', 1).at(-1)).toBe('settings')
  })

  it('compte aussi les pages cachées : la fenêtre les montre toutes', () => {
    const next = movePage([], ['sound'], 'apps', -1)
    expect(next.slice(0, 3)).toEqual(['music', 'apps', 'sound'])
  })
})

describe('toggleHidden', () => {
  it('cache puis remontre une page', () => {
    expect(toggleHidden([], 'steam')).toEqual(['steam'])
    expect(toggleHidden(['steam', 'office'], 'steam')).toEqual(['office'])
  })

  it('ne cache jamais les Réglages', () => {
    expect(toggleHidden(['steam'], 'settings')).toEqual(['steam'])
    expect(toggleHidden(['settings'], 'settings')).toEqual([])
  })
})

describe('pagesSummary', () => {
  it('compte les pages et nomme les cachées', () => {
    expect(pagesSummary(pageRows([], []))).toBe('9 pages visibles sur 9.')
    expect(pagesSummary(pageRows([], ['steam']))).toBe('8 pages visibles sur 9 (cachée : Steam).')
    expect(pagesSummary(pageRows([], ['steam', 'office']))).toBe('7 pages visibles sur 9 (cachées : Steam, Office).')
  })
})


describe('les couleurs des boutons', () => {
  const channel = (hex: string, at: number): number => {
    const v = Number.parseInt(hex.slice(at, at + 2), 16) / 255
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  }
  const luminance = (hex: string): number => 0.2126 * channel(hex, 1) + 0.7152 * channel(hex, 3) + 0.0722 * channel(hex, 5)
  const contrastWithWhite = (hex: string): number => 1.05 / (luminance(hex) + 0.05)

  it('le libellé blanc se lit sur le bas de chaque bouton (contraste d’au moins 4,5 : 1)', () => {
    for (const page of PAGES) expect(contrastWithWhite(page.tone.to), page.id).toBeGreaterThanOrEqual(4.5)
  })
  it('l’icône blanche se voit sur le haut de chaque bouton (contraste d’au moins 3 : 1)', () => {
    for (const page of PAGES) expect(contrastWithWhite(page.tone.from), page.id).toBeGreaterThanOrEqual(3)
  })
  it('chaque page a sa teinte : on reconnaît un bouton à sa couleur autant qu’à son dessin', () => {
    expect(new Set(PAGES.map((p) => p.tone.from)).size).toBe(PAGES.length)
    for (const page of PAGES) expect(page.tone.from, page.id).toMatch(/^#[0-9a-f]{6}$/)
  })
})
