// Aides pures de la page Bourse : formats français, mini-graphiques, fraîcheur, liste suivie.
// Testées dans markets.test.ts.

import type {
  MarketItem,
  MarketProvider,
  MarketRange,
  MarketRefresh,
  MarketSession,
  Watched,
} from './protocol'

export const MAX_WATCHED = 20
/** Au-delà, une cotation qu'on devrait rafraîchir toutes les 15 à 30 s est considérée comme ancienne. */
export const STALE_AFTER = 120

export const RANGES: readonly { id: MarketRange; label: string; long: string }[] = [
  { id: '1d', label: '1 j', long: '1 jour' },
  { id: '5d', label: '5 j', long: '5 jours' },
  { id: '1mo', label: '1 mois', long: '1 mois' },
  { id: '1y', label: '1 an', long: '1 an' },
]

export const REFRESH_OPTIONS: readonly { id: MarketRefresh; label: string }[] = [
  { id: 15, label: '15 s' },
  { id: 30, label: '30 s' },
  { id: 60, label: '1 min' },
  { id: 300, label: '5 min' },
]

export const SOURCE_LABELS: Record<string, string> = {
  binance: 'Binance',
  yahoo: 'Yahoo Finance',
  finnhub: 'Finnhub',
  demo: 'Démonstration',
}

export const SESSION_LABELS: Record<MarketSession, string> = {
  open: 'Marché ouvert',
  closed: 'Marché fermé',
  pre: 'Avant la séance',
  post: 'Après la séance',
}

export const SUGGESTIONS: Record<'crypto' | 'stock', string[]> = {
  crypto: ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA'],
  stock: ['MSTR', 'AAPL', 'NVDA', '^FCHI', 'CW8.PA', 'EURUSD=X'],
}

const NBSP = ' '
const MINUS = '−'

// --- nombres ---------------------------------------------------------------------------------

/** Décimales d'un prix : peu pour les gros montants (lisibles de loin), plus pour les petits. */
export function fractionDigits(value: number): number {
  const size = Math.abs(value)
  if (size === 0) return 2
  if (size >= 10_000) return 0
  if (size >= 1) return 2
  if (size >= 0.01) return 4
  return 6
}

/** « 61 250 € », « 231,20 $US », « 0,1234 € » ; « — » sans cours ; le code brut si Intl ne le connaît pas (« GBp »). */
export function formatPrice(value: number | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const digits = fractionDigits(value)
  const options = { minimumFractionDigits: digits, maximumFractionDigits: digits }
  if (currency && /^[A-Z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat('fr-FR', { ...options, style: 'currency', currency }).format(value)
    } catch {
      // code de devise inconnu d'Intl : on retombe sur le nombre suivi du code
    }
  }
  const text = new Intl.NumberFormat('fr-FR', options).format(value)
  return currency ? `${text}${NBSP}${currency}` : text
}

/** « +1,41 % », « −0,32 % », « 0,00 % » : le signe est toujours écrit, jamais seulement la couleur. */
export function formatPercent(pct: number | null | undefined): string {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return '—'
  const text = Math.abs(pct).toFixed(2).replace('.', ',')
  const sign = pct > 0 && text !== '0,00' ? '+' : pct < 0 && text !== '0,00' ? MINUS : ''
  return `${sign}${text}${NBSP}%`
}

/** Variation signée en monnaie : « +850 € », « −31,88 € ». */
export function formatChange(change: number | null | undefined, currency: string | null | undefined): string {
  if (change === null || change === undefined || !Number.isFinite(change)) return '—'
  const body = formatPrice(Math.abs(change), currency)
  return change > 0 ? `+${body}` : change < 0 ? `${MINUS}${body}` : body
}

export type Tone = 'up' | 'down' | 'flat'

export function toneOf(pct: number | null | undefined): Tone {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return 'flat'
  const rounded = Math.round(pct * 100) / 100
  return rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat'
}

// --- fraîcheur -------------------------------------------------------------------------------

/** Au-delà de ce délai sans échange pendant la séance, on le dit : cours différés, ou valeur peu échangée. */
export const LAG_AFTER = 300

/** Secondes depuis le dernier échange d'une action en séance, si c'est assez ancien pour être signalé. */
export function tradeLag(
  item: Pick<MarketItem, 'kind' | 'state' | 'traded_at'>,
  now: number,
): number | null {
  if (item.kind !== 'stock' || item.state !== 'open' || item.traded_at === null) return null
  const lag = now - item.traded_at
  return lag >= LAG_AFTER ? Math.round(lag) : null
}

/** « dernier échange il y a 15 min » : arrondi à la minute (ou à l'heure), pour ne pas hésiter entre 14 et 15. */
export function lagLabel(lag: number): string {
  if (lag < 3_600) return `dernier échange il y a ${Math.max(1, Math.round(lag / 60))}${NBSP}min`
  if (lag < 86_400) return `dernier échange il y a ${Math.round(lag / 3_600)}${NBSP}h`
  return `dernier échange il y a ${Math.round(lag / 86_400)}${NBSP}j`
}

/** « à l'instant », « il y a 12 s », « il y a 3 min », « il y a 2 h », « il y a 3 j ». */
export function freshness(fetched: number | null | undefined, now: number): string {
  if (fetched === null || fetched === undefined) return '—'
  const seconds = Math.max(0, Math.round(now - fetched))
  if (seconds < 5) return 'à l’instant'
  if (seconds < 60) return `il y a ${seconds}${NBSP}s`
  if (seconds < 3_600) return `il y a ${Math.floor(seconds / 60)}${NBSP}min`
  if (seconds < 86_400) return `il y a ${Math.floor(seconds / 3_600)}${NBSP}h`
  return `il y a ${Math.floor(seconds / 86_400)}${NBSP}j`
}

/** Une cotation est « ancienne » si sa dernière mise à jour a échoué ou si elle date de plus de deux minutes. */
export function isStale(item: Pick<MarketItem, 'fetched' | 'error'>, now: number): boolean {
  if (item.fetched === null) return false
  return item.error !== null || now - item.fetched > STALE_AFTER
}

/** Les fournisseurs qui posent problème, une phrase chacun (avec le délai de reprise pendant une pause). */
export function providerProblems(providers: MarketProvider[], now: number): string[] {
  const problems: string[] = []
  for (const provider of providers) {
    if (provider.state !== 'error' && provider.state !== 'paused') continue
    const message = provider.message ?? 'ne répond pas'
    const wait =
      provider.state === 'paused' && provider.retry_at !== null
        ? ` Nouvel essai dans ${Math.max(1, Math.ceil(provider.retry_at - now))}${NBSP}s.`
        : ''
    problems.push(`${message.startsWith(provider.label) ? message : `${provider.label} : ${message}`}${wait}`)
  }
  return problems
}

/** « Binance · Yahoo Finance » : d'où viennent les cotations affichées. */
export function sourcesOf(items: MarketItem[]): string {
  const seen = new Set<string>()
  for (const item of items) if (item.source) seen.add(SOURCE_LABELS[item.source] ?? item.source)
  return [...seen].join(' · ')
}

// --- mini-graphiques --------------------------------------------------------------------------

export interface Geometry {
  /** Points de la ligne (« x,y x,y… »). */
  line: string
  /** Polygone sous la ligne, pour le remplissage. */
  area: string
  min: number
  max: number
  /** Position du dernier point. */
  last: [number, number]
}

const fixed = (n: number): string => (Math.round(n * 10) / 10).toString()

/** La courbe de `values` dans un cadre `width`×`height` ; null s'il n'y a pas de quoi tracer. */
export function curve(values: number[], width: number, height: number, pad = 3): Geometry | null {
  const clean = values.filter((v) => Number.isFinite(v))
  if (clean.length < 2) return null
  const min = Math.min(...clean)
  const max = Math.max(...clean)
  const span = max - min
  const x = (i: number) => pad + ((width - 2 * pad) * i) / (clean.length - 1)
  const y = (v: number) => (span === 0 ? height / 2 : height - pad - ((height - 2 * pad) * (v - min)) / span)
  const points = clean.map((v, i) => `${fixed(x(i))},${fixed(y(v))}`)
  const lastIndex = clean.length - 1
  return {
    line: points.join(' '),
    area: `${fixed(x(0))},${height} ${points.join(' ')} ${fixed(x(lastIndex))},${height}`,
    min,
    max,
    last: [x(lastIndex), y(clean[lastIndex])],
  }
}

/** Les cours d'un historique `[heure, cours]` sous forme de valeurs pour `curve`. */
export const closes = (points: readonly (readonly [number, number])[]): number[] => points.map((p) => p[1])

/** Étiquette d'axe du temps selon la période : 14:30 ; lun. 26 ; 26 oct. ; oct. 26. */
export function axisLabel(at: number, range: MarketRange, timeZone?: string): string {
  const date = new Date(at * 1_000)
  const base = { timeZone }
  switch (range) {
    case '1d':
      return date.toLocaleTimeString('fr-FR', { ...base, hour: '2-digit', minute: '2-digit' })
    case '5d':
      return date.toLocaleDateString('fr-FR', { ...base, weekday: 'short', day: 'numeric' })
    case '1mo':
      return date.toLocaleDateString('fr-FR', { ...base, day: 'numeric', month: 'short' })
    default:
      return date.toLocaleDateString('fr-FR', { ...base, month: 'short', year: '2-digit' })
  }
}

// --- liste suivie ----------------------------------------------------------------------------

export const watchedId = (w: Watched): string => `${w.kind}:${w.symbol}`

export const KIND_LABELS = { crypto: 'Crypto', stock: 'Action, indice…' } as const

/** Le symbole tel que le serveur le comprend : majuscules, sans espaces, sans « -EUR » pour une crypto. */
export function cleanSymbol(kind: Watched['kind'], typed: string): string {
  const symbol = typed.trim().toUpperCase().replace(/\s+/g, '')
  return kind === 'crypto' ? symbol.split('-')[0] : symbol
}

/** La liste après avoir ajouté `item` ; null s'il y est déjà ou si elle est pleine. */
export function appended(list: Watched[], item: Watched, max = MAX_WATCHED): Watched[] | null {
  if (list.some((w) => watchedId(w) === watchedId(item)) || list.length >= max) return null
  return [...list, { kind: item.kind, symbol: item.symbol }]
}
