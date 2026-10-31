// Mises en forme (français). Fonctions pures, testées dans format.test.ts.

const WEEKDAYS_FORMAT = new Intl.DateTimeFormat('fr-FR', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
})
const CLOCK_FORMAT = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' })
const CLOCK_SECONDS_FORMAT = new Intl.DateTimeFormat('fr-FR', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

export const formatClock = (date: Date): string => CLOCK_FORMAT.format(date)
export const formatClockSeconds = (date: Date): string => CLOCK_SECONDS_FORMAT.format(date)

export function formatDate(date: Date): string {
  const text = WEEKDAYS_FORMAT.format(date)
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** « 3 h 17 min », « 12 min », « 2 j 4 h », « moins d'une minute ». */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3_600)
  const minutes = Math.floor((seconds % 3_600) / 60)
  if (days > 0) return hours > 0 ? `${days} j ${hours} h` : `${days} j`
  if (hours > 0) return minutes > 0 ? `${hours} h ${String(minutes).padStart(2, '0')} min` : `${hours} h`
  if (minutes > 0) return `${minutes} min`
  return "moins d'une minute"
}

/** « 4 s », « 1 min 05 s ». */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  if (total < 60) return `${total} s`
  return `${Math.floor(total / 60)} min ${String(total % 60).padStart(2, '0')} s`
}

export const ifaceKindLabel = (kind: string): string =>
  kind === 'wifi' ? 'Wi-Fi' : kind === 'ethernet' ? 'Ethernet' : kind

export function sessionLabel(type: string): string {
  switch (type) {
    case 'wayland':
      return 'Wayland'
    case 'x11':
      return 'X11'
    case 'tty':
      return 'Console'
    default:
      return 'Session inconnue'
  }
}

export interface LatencyStats {
  n: number
  min: number
  median: number
  p95: number
  max: number
}

/** Statistiques d'une série de mesures (en ms) ; null si la série est vide. */
export function latencyStats(samples: number[]): LatencyStats | null {
  if (samples.length === 0) return null
  const sorted = [...samples].sort((a, b) => a - b)
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)]
  const middle = sorted.length / 2
  const median =
    sorted.length % 2 === 1 ? sorted[Math.floor(middle)] : (sorted[middle - 1] + sorted[middle]) / 2
  return { n: sorted.length, min: sorted[0], median, p95: at(0.95), max: sorted[sorted.length - 1] }
}

/** « 4,2 ms » ; une décimale sous 10 ms, entier au-delà. */
export function formatMs(ms: number): string {
  const text = ms < 10 ? ms.toFixed(1).replace('.', ',') : String(Math.round(ms))
  return `${text} ms`
}

export const NBSP = '\u00a0'
const decimal = (value: number, digits: number): string =>
  value.toFixed(digits).replace('.', ',')

/** « 12 % » (espace insécable, comme en typographie française). */
export const formatPercent = (value: number): string => `${Math.round(value)}${NBSP}%`

/** Taille en octets : « 512 o », « 12 Ko », « 612 Mo », « 1,4 Go ». Base 1024. */
export function formatBytes(bytes: number): string {
  const n = Math.max(0, bytes)
  const units = ['o', 'Ko', 'Mo', 'Go', 'To']
  let value = n
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 || value >= 100 ? 0 : value >= 10 ? 1 : 1
  return `${decimal(value, digits).replace(/,0$/, '')}${NBSP}${units[unit]}`
}

/** Débit : « 0 o/s », « 120 Ko/s », « 1,8 Mo/s ». */
export const formatRate = (bytesPerSecond: number): string => `${formatBytes(bytesPerSecond)}/s`

export const formatTemp = (celsius: number): string => `${Math.round(celsius)}${NBSP}°C`

/** « 3,2 GHz » au-delà de 1000 MHz, sinon « 900 MHz ». */
export function formatFreq(mhz: number): string {
  return mhz >= 1000 ? `${decimal(mhz / 1000, 1)}${NBSP}GHz` : `${Math.round(mhz)}${NBSP}MHz`
}

export const formatWatts = (watts: number): string => `${decimal(watts, 1)}${NBSP}W`

/** Durée d'un morceau : « 3:42 », « 1:02:15 ». */
export function formatTrackTime(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = String(total % 60).padStart(2, '0')
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}`
    : `${minutes}:${seconds}`
}

/** « 11,4 / 32 Go » : l'unité est celle du total, écrite une seule fois (tient dans une petite tuile). */
export function formatUsage(used: number, total: number): string {
  const units = ['o', 'Ko', 'Mo', 'Go', 'To']
  let unit = 0
  let scaled = Math.max(0, total)
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024
    unit += 1
  }
  const part = (value: number): string => {
    const x = value / 1024 ** unit
    return decimal(x, x >= 100 || unit === 0 ? 0 : 1).replace(/,0$/, '')
  }
  return `${part(used)}${NBSP}/${NBSP}${part(total)}${NBSP}${units[unit]}`
}

const SHORT_DATE = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short' })
const LONG_DATE = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' })

/** « à l'instant », « il y a 5 min », « il y a 3 h », « hier », « il y a 4 j », puis la date. */
export function formatAgo(seconds: number, nowSeconds: number): string {
  const elapsed = Math.max(0, nowSeconds - seconds)
  if (elapsed < 45) return 'à l’instant'
  if (elapsed < 3_600) return `il y a ${Math.max(1, Math.round(elapsed / 60))}${NBSP}min`
  if (elapsed < 86_400) return `il y a ${Math.round(elapsed / 3_600)}${NBSP}h`
  if (elapsed < 2 * 86_400) return 'hier'
  if (elapsed < 30 * 86_400) return `il y a ${Math.floor(elapsed / 86_400)}${NBSP}j`
  const date = new Date(seconds * 1_000)
  const sameYear = date.getFullYear() === new Date(nowSeconds * 1_000).getFullYear()
  return (sameYear ? SHORT_DATE : LONG_DATE).format(date)
}
