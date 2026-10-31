import { describe, expect, it } from 'vitest'
import {
  MAX_WATCHED,
  LAG_AFTER,
  REFRESH_OPTIONS,
  SOURCE_LABELS,
  STALE_AFTER,
  appended,
  axisLabel,
  cleanSymbol,
  closes,
  curve,
  formatChange,
  formatPercent,
  formatPrice,
  fractionDigits,
  freshness,
  isStale,
  lagLabel,
  providerProblems,
  sourcesOf,
  toneOf,
  tradeLag,
  watchedId,
} from './markets'
import type { MarketItem, MarketProvider } from './protocol'

const plain = (text: string): string => text.replace(/[  ]/g, ' ')

describe('prix', () => {
  it('garde peu de décimales pour les gros montants et plus pour les petits', () => {
    expect(fractionDigits(61_250.5)).toBe(0)
    expect(fractionDigits(2_840.12)).toBe(2)
    expect(fractionDigits(231.2)).toBe(2)
    expect(fractionDigits(0.1234)).toBe(4)
    expect(fractionDigits(0.000012)).toBe(6)
    expect(fractionDigits(-61_250)).toBe(0)
    expect(fractionDigits(0)).toBe(2) // zéro n'est pas « un tout petit prix »
  })

  it('écrit les prix à la française, avec la devise', () => {
    expect(plain(formatPrice(61_250.5, 'EUR'))).toBe('61 251 €')
    expect(plain(formatPrice(2_840.12, 'EUR'))).toBe('2 840,12 €')
    expect(plain(formatPrice(231.2, 'USD'))).toMatch(/^231,20 \$/)
    expect(plain(formatPrice(0.1234, 'EUR'))).toBe('0,1234 €')
  })

  it('retombe sur le code brut quand Intl ne connaît pas la devise', () => {
    expect(plain(formatPrice(1234.5, 'GBp'))).toBe('1 234,50 GBp')
    expect(plain(formatPrice(7512.3, null))).toBe('7 512,30')
    expect(plain(formatPrice(5, 'ZZZ'))).toContain('5')
  })

  it('n’invente rien quand le prix manque', () => {
    for (const bad of [null, undefined, NaN, Infinity]) expect(formatPrice(bad as number, 'EUR')).toBe('—')
  })
})

describe('variations', () => {
  it('écrit toujours le signe, jamais seulement la couleur', () => {
    expect(plain(formatPercent(1.409))).toBe('+1,41 %')
    expect(plain(formatPercent(-0.32))).toBe('−0,32 %')
    expect(plain(formatPercent(0))).toBe('0,00 %')
    expect(plain(formatPercent(0.001))).toBe('0,00 %') // arrondi à zéro : pas de « + » trompeur
    expect(plain(formatPercent(-0.004))).toBe('0,00 %')
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(NaN)).toBe('—')
  })

  it('écrit la variation en monnaie avec son signe', () => {
    expect(plain(formatChange(850.5, 'EUR'))).toBe('+850,50 €')
    expect(plain(formatChange(-31.88, 'EUR'))).toBe('−31,88 €')
    expect(plain(formatChange(0, 'EUR'))).toBe('0,00 €')
    expect(formatChange(null, 'EUR')).toBe('—')
  })

  it('classe la tendance après arrondi', () => {
    expect(toneOf(1.2)).toBe('up')
    expect(toneOf(-0.2)).toBe('down')
    expect(toneOf(0)).toBe('flat')
    expect(toneOf(0.004)).toBe('flat')
    expect(toneOf(null)).toBe('flat')
  })
})

describe('fraîcheur', () => {
  const now = 1_793_370_000
  it('dit l’âge d’une cotation', () => {
    expect(freshness(now - 2, now)).toBe('à l’instant')
    expect(plain(freshness(now - 12, now))).toBe('il y a 12 s')
    expect(plain(freshness(now - 125, now))).toBe('il y a 2 min')
    expect(plain(freshness(now - 3 * 3_600 - 10, now))).toBe('il y a 3 h')
    expect(plain(freshness(now - 3 * 86_400, now))).toBe('il y a 3 j')
    expect(freshness(now + 30, now)).toBe('à l’instant') // horloge du Pi en avance : jamais d’âge négatif
    expect(freshness(null, now)).toBe('—')
  })

  it('juge « ancienne » une cotation dont la mise à jour a échoué ou qui date de plus de deux minutes', () => {
    expect(isStale({ fetched: now - 10, error: null }, now)).toBe(false)
    expect(isStale({ fetched: now - STALE_AFTER - 1, error: null }, now)).toBe(true)
    expect(isStale({ fetched: now - 10, error: 'Yahoo ne répond pas' }, now)).toBe(true)
    expect(isStale({ fetched: null, error: null }, now)).toBe(false) // pas encore de cotation : « chargement »
  })

  it('résume les fournisseurs en panne avec le délai de reprise', () => {
    const providers: MarketProvider[] = [
      { id: 'binance', label: 'Binance', state: 'paused', message: 'Binance n’est pas accessible depuis ce pays (HTTP 451).', retry_at: now + 44.2 },
      { id: 'yahoo', label: 'Yahoo Finance', state: 'ok', message: null, retry_at: null },
      { id: 'finnhub', label: 'Finnhub', state: 'error', message: 'Finnhub refuse l’accès (HTTP 401).', retry_at: null },
      { id: 'demo', label: 'Démonstration', state: 'idle', message: null, retry_at: null },
    ]
    expect(providerProblems(providers, now).map(plain)).toEqual([
      'Binance n’est pas accessible depuis ce pays (HTTP 451). Nouvel essai dans 45 s.',
      'Finnhub refuse l’accès (HTTP 401).',
    ])
    expect(providerProblems([{ id: 'x', label: 'X', state: 'error', message: 'plantage', retry_at: null }], now)).toEqual(['X : plantage'])
  })

  it('nomme les sources des cotations affichées', () => {
    const item = (source: string | null) => ({ source }) as MarketItem
    expect(sourcesOf([item('binance'), item('yahoo'), item('binance'), item(null)])).toBe('Binance · Yahoo Finance')
    expect(sourcesOf([item('inconnu')])).toBe('inconnu')
    expect(sourcesOf([])).toBe('')
    expect(Object.keys(SOURCE_LABELS)).toEqual(['binance', 'yahoo', 'finnhub', 'demo'])
  })
})

describe('mini-graphiques', () => {
  it('trace une courbe qui monte de gauche à droite et du bas vers le haut', () => {
    const geo = curve([1, 2, 3], 100, 40, 0)
    expect(geo).not.toBeNull()
    expect(geo?.line).toBe('0,40 50,20 100,0')
    expect(geo?.area).toBe('0,40 0,40 50,20 100,0 100,40')
    expect(geo?.min).toBe(1)
    expect(geo?.max).toBe(3)
    expect(geo?.last).toEqual([100, 0])
  })

  it('laisse une marge pour que le trait ne soit pas rogné', () => {
    const geo = curve([5, 1, 5], 100, 40, 4)
    expect(geo?.line).toBe('4,4 50,36 96,4')
  })

  it('trace une série plate au milieu', () => {
    expect(curve([7, 7, 7], 90, 30, 0)?.line).toBe('0,15 45,15 90,15')
  })

  it('ne trace rien sans au moins deux points valides', () => {
    expect(curve([], 100, 40)).toBeNull()
    expect(curve([3], 100, 40)).toBeNull()
    expect(curve([3, NaN], 100, 40)).toBeNull()
    expect(curve([3, NaN, 4], 100, 40)).not.toBeNull() // un trou est sauté
  })

  it('extrait les cours d’un historique', () => {
    expect(closes([[10, 1.5], [20, 2.5]])).toEqual([1.5, 2.5])
  })

  it('étiquette l’axe du temps selon la période', () => {
    const at = Date.UTC(2026, 9, 30, 14, 20) / 1_000
    expect(axisLabel(at, '1d', 'UTC')).toBe('14:20')
    expect(axisLabel(at, '5d', 'UTC')).toMatch(/^ven\.? 30$/)
    expect(axisLabel(at, '1mo', 'UTC')).toBe('30 oct.')
    expect(axisLabel(at, '1y', 'UTC')).toBe('oct. 26')
  })
})

describe('liste suivie', () => {
  it('identifie une valeur par son type et son symbole', () => {
    expect(watchedId({ kind: 'crypto', symbol: 'BTC' })).toBe('crypto:BTC')
  })

  it('met le symbole sous la forme que le serveur comprend', () => {
    expect(cleanSymbol('stock', ' mstr ')).toBe('MSTR')
    expect(cleanSymbol('stock', '^fchi')).toBe('^FCHI')
    expect(cleanSymbol('stock', 'cw8 .pa')).toBe('CW8.PA')
    expect(cleanSymbol('crypto', 'btc-eur')).toBe('BTC')
    expect(cleanSymbol('stock', 'brk-b')).toBe('BRK-B') // un tiret fait partie d’un symbole de valeur
  })

  it('ajoute à la fin, sans doublon et pas au-delà du maximum', () => {
    const list = [{ kind: 'crypto' as const, symbol: 'BTC' }]
    expect(appended(list, { kind: 'stock', symbol: 'MSTR' })).toEqual([
      { kind: 'crypto', symbol: 'BTC' },
      { kind: 'stock', symbol: 'MSTR' },
    ])
    expect(appended(list, { kind: 'crypto', symbol: 'BTC' })).toBeNull()
    expect(appended(list, { kind: 'stock', symbol: 'BTC' })).not.toBeNull() // même symbole, autre type
    const full = Array.from({ length: MAX_WATCHED }, (_, i) => ({ kind: 'stock' as const, symbol: `S${i}` }))
    expect(appended(full, { kind: 'stock', symbol: 'NEW' })).toBeNull()
    expect(list).toHaveLength(1) // la liste d’origine n’est pas modifiée
  })
})


describe('dernier échange', () => {
  const now = 1_793_370_000
  const open = { kind: 'stock' as const, state: 'open' as const, traded_at: now - 900 }

  it('signale une action en séance dont le dernier échange a plus de cinq minutes', () => {
    expect(tradeLag(open, now)).toBe(900)
    expect(plain(lagLabel(900))).toBe('dernier échange il y a 15 min')
    expect(plain(lagLabel(895))).toBe('dernier échange il y a 15 min') // l'horloge de l'écran retarde de quelques secondes
    expect(plain(lagLabel(300))).toBe('dernier échange il y a 5 min')
    expect(plain(lagLabel(7_300))).toBe('dernier échange il y a 2 h')
    expect(plain(lagLabel(3 * 86_400))).toBe('dernier échange il y a 3 j')
    expect(tradeLag({ ...open, traded_at: now - LAG_AFTER }, now)).toBe(LAG_AFTER)
    expect(tradeLag({ ...open, traded_at: now - LAG_AFTER + 1 }, now)).toBeNull()
  })

  it('ne dit rien d’une crypto, d’un marché fermé, ni d’une heure inconnue', () => {
    expect(tradeLag({ ...open, kind: 'crypto' }, now)).toBeNull()
    expect(tradeLag({ ...open, state: 'closed' }, now)).toBeNull()
    expect(tradeLag({ ...open, state: 'pre' }, now)).toBeNull()
    expect(tradeLag({ ...open, state: null }, now)).toBeNull()
    expect(tradeLag({ ...open, traded_at: null }, now)).toBeNull()
  })
})

describe('fréquence', () => {
  it('propose quatre rythmes, du plus vif au plus calme', () => {
    expect(REFRESH_OPTIONS.map((o) => o.id)).toEqual([15, 30, 60, 300])
    expect(REFRESH_OPTIONS.map((o) => o.label)).toEqual(['15 s', '30 s', '1 min', '5 min'])
  })
})
