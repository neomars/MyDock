import { describe, expect, it } from 'vitest'
import { formatAgo } from './format'
import {
  MAX_PINNED,
  filterApps,
  idLabel,
  kindIcon,
  kindLabel,
  moved,
  pinnedTiles,
  sortGames,
  toggled,
} from './launcher'
import type { AppInfo, GameInfo } from './protocol'

const app = (id: string, name: string, generic: string | null = null): AppInfo => ({ id, name, generic, icon: null })
const game = (appid: number, name: string, played: number | null = null): GameInfo => ({ appid, name, size: 1, played })

describe('applications du lanceur', () => {
  const catalog = [app('a.desktop', 'Firefox', 'Navigateur Web'), app('b.desktop', 'Éditeur de texte')]

  it('suivent l’ordre choisi et gardent un nom pour une application disparue', () => {
    const tiles = pinnedTiles(['b.desktop', 'org.gone.App.desktop', 'a.desktop'], catalog)
    expect(tiles.map((t) => t.name)).toEqual(['Éditeur de texte', 'org.gone.App', 'Firefox'])
    expect(tiles.map((t) => t.app?.id ?? null)).toEqual(['b.desktop', null, 'a.desktop'])
    expect(pinnedTiles(['a.desktop'], undefined)[0].app).toBeNull() // catalogue pas encore reçu
    expect(idLabel('x.desktop')).toBe('x')
  })

  it('se cherchent sans tenir compte des accents, de la casse ni de l’ordre des mots', () => {
    expect(filterApps(catalog, 'editeur').map((a) => a.id)).toEqual(['b.desktop'])
    expect(filterApps(catalog, 'ÉDITEUR TEXTE').map((a) => a.id)).toEqual(['b.desktop'])
    expect(filterApps(catalog, 'web firefox').map((a) => a.id)).toEqual(['a.desktop']) // catégorie comprise
    expect(filterApps(catalog, 'zzz')).toEqual([])
    expect(filterApps(catalog, '   ')).toBe(catalog)
    expect(filterApps(catalog, '')).toBe(catalog)
  })

  it('s’ajoutent et se retirent, dans la limite de la grille', () => {
    expect(toggled(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggled(['a', 'b'], 'a')).toEqual(['b'])
    const full = Array.from({ length: MAX_PINNED }, (_, i) => `app${i}.desktop`)
    expect(toggled(full, 'nouvelle.desktop')).toBeNull()
    expect(toggled(full, 'app3.desktop')).toHaveLength(MAX_PINNED - 1) // on peut toujours retirer
  })

  it('se déplacent d’un cran sans sortir de la liste', () => {
    expect(moved(['a', 'b', 'c'], 1, -1)).toEqual(['b', 'a', 'c'])
    expect(moved(['a', 'b', 'c'], 1, 1)).toEqual(['a', 'c', 'b'])
    const list = ['a', 'b', 'c']
    expect(moved(list, 0, -1)).toBe(list)
    expect(moved(list, 2, 1)).toBe(list)
    expect(moved(list, 9, 1)).toBe(list)
    expect(list).toEqual(['a', 'b', 'c']) // la liste d'origine n'est jamais modifiée
  })
})

describe('jeux Steam', () => {
  it('mettent le jeu en cours d’abord, puis les plus récents, puis l’ordre alphabétique', () => {
    const games = [game(1, 'Zèbre', 100), game(2, 'Alpha', 500), game(3, 'Bêta'), game(4, 'Ancien', null), game(5, 'Milieu', 300)]
    expect(sortGames(games, null).map((g) => g.appid)).toEqual([2, 5, 1, 4, 3])
    expect(sortGames(games, 3).map((g) => g.appid)).toEqual([3, 2, 5, 1, 4])
    expect(games.map((g) => g.appid)).toEqual([1, 2, 3, 4, 5]) // l'original reste intact
  })
})

describe('documents', () => {
  it('ont un nom de famille français', () => {
    expect(kindLabel('writer')).toBe('Texte')
    expect(kindLabel('calc')).toBe('Tableur')
    expect(kindLabel('impress')).toBe('Présentation')
    expect(kindLabel('inconnu' as never)).toBe('Document')
  })

  it('ont une icône par famille, et une de secours', () => {
    const kinds = ['writer', 'calc', 'impress', 'draw', 'other'] as const
    expect(new Set(kinds.map((k) => kindIcon(k))).size).toBe(kinds.length)
    expect(kindIcon('inconnu' as never)).toBe(kindIcon('other'))
  })

  it('se datent en langage courant', () => {
    const now = 1_787_000_000 // mi-août 2026 : soixante jours plus tôt, c'est la même année
    expect(formatAgo(now - 5, now)).toBe('à l’instant')
    expect(formatAgo(now + 100, now)).toBe('à l’instant') // horloge du PC en avance
    expect(formatAgo(now - 300, now)).toBe('il y a 5 min')
    expect(formatAgo(now - 3 * 3_600, now)).toBe('il y a 3 h')
    expect(formatAgo(now - 30 * 3_600, now)).toBe('hier')
    expect(formatAgo(now - 4 * 86_400, now)).toBe('il y a 4 j')
    expect(formatAgo(now - 60 * 86_400, now)).toMatch(/^\d{1,2} \S+\.?$/) // « 18 juin »
    expect(formatAgo(now - 800 * 86_400, now)).toMatch(/\d{4}$/) // une autre année : l'année est dite
  })
})
