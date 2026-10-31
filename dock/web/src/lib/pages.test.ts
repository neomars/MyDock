import { describe, expect, it } from 'vitest'
import { PAGES, movePage, pageById, pageRows, pagesSummary, shownPage, toggleHidden, visiblePages } from './pages'

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
      'power',
      'settings',
    ])
  })

  it('seules la Bourse, l’Alimentation (Wake-on-LAN) et les Réglages servent PC éteint', () => {
    expect(PAGES.filter((p) => !p.needsPc).map((p) => p.id)).toEqual(['markets', 'power', 'settings'])
  })

  it('pageById retombe sur la première page pour un identifiant inconnu', () => {
    expect(pageById('steam').title).toBe('Steam')
    expect(pageById('nope' as never).id).toBe('music')
  })

  it('visiblePages applique l’ordre puis laisse les autres dans l’ordre par défaut', () => {
    const ids = visiblePages(['markets', 'system'], []).map((p) => p.id)
    expect(ids.slice(0, 2)).toEqual(['markets', 'system'])
    expect(ids.slice(2)).toEqual(['music', 'sound', 'apps', 'steam', 'office', 'power', 'settings'])
  })

  it('on ne peut jamais cacher les Réglages', () => {
    const ids = visiblePages([], ['settings', 'steam']).map((p) => p.id)
    expect(ids).toContain('settings')
    expect(ids).not.toContain('steam')
  })
})

describe('pageRows', () => {
  it('liste les neuf pages, cachées comprises, dans l’ordre effectif', () => {
    const rows = pageRows(['power', 'music'], ['music', 'steam'])
    expect(rows.map((r) => r.page.id).slice(0, 3)).toEqual(['power', 'music', 'sound'])
    expect(rows).toHaveLength(9)
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
    const next = movePage([], [], 'power', -1)
    expect(next).toHaveLength(9)
    expect(next.slice(5, 9)).toEqual(['markets', 'power', 'system', 'settings'])
    expect(movePage(next, [], 'power', 1).slice(5, 9)).toEqual(['markets', 'system', 'power', 'settings'])
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

describe('shownPage', () => {
  const visible = visiblePages([], ['steam'])

  it('garde la page demandée quand elle est visible', () => {
    expect(shownPage('system', visible)).toBe('system')
  })

  it('retombe sur la première page visible quand la page demandée est cachée', () => {
    expect(shownPage('steam', visible)).toBe('music')
    expect(shownPage('steam', visiblePages(['power'], ['steam']))).toBe('power')
  })

  it('ne casse pas sans aucune page', () => {
    expect(shownPage('steam', [])).toBe('steam')
  })
})

describe('pagesSummary', () => {
  it('compte les pages et nomme les cachées', () => {
    expect(pagesSummary(pageRows([], []))).toBe('9 pages visibles sur 9.')
    expect(pagesSummary(pageRows([], ['steam']))).toBe('8 pages visibles sur 9 (cachée : Steam).')
    expect(pagesSummary(pageRows([], ['steam', 'office']))).toBe('7 pages visibles sur 9 (cachées : Steam, Office).')
  })
})

