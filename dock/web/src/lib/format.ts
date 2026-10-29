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
