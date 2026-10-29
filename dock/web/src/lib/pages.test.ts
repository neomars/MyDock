import { describe, expect, it } from 'vitest'
import { PAGES, pageById, visiblePages } from './pages'

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
