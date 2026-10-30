// Aides pures de la page Système : résumé du GPU, noms de disques, tracé du graphique. Testées dans system.test.ts.

import { formatFreq, formatPercent, formatTemp } from './format'
import type { DiskInfo, GpuInfo } from './protocol'

export interface GpuSummary {
  /** Valeur en gros : « 35 % », « En veille », « 450 MHz ». */
  value: string
  /** Ligne du dessous : le nom court de la carte et sa température. */
  sub: string
  /** La carte dédiée dort : on le dit, ce n'est pas une panne. */
  asleep: boolean
}

/** « NVIDIA RTX 4000 Ada Generation Laptop GPU » -> « RTX 4000 Ada » (tient dans une tuile). */
export function shortGpuName(name: string): string {
  return name
    .replace(/^(NVIDIA|AMD|Intel)\s+/i, '')
    .replace(/\s+(Generation|Laptop|GPU|Graphics)\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * Ce que la tuile « Carte graphique » affiche : la charge de la carte qui travaille ; sinon
 * « En veille » si la carte dédiée dort (portable hybride) ; sinon la fréquence de l'Intel.
 */
export function summarizeGpus(gpus: GpuInfo[]): GpuSummary | null {
  const busy = gpus.find((g) => g.state === 'active' && g.util !== null)
  if (busy) {
    const temp = busy.temp_c !== null ? ` · ${formatTemp(busy.temp_c)}` : ''
    return { value: formatPercent(busy.util ?? 0), sub: `${shortGpuName(busy.name)}${temp}`, asleep: false }
  }
  const asleep = gpus.find((g) => g.state === 'suspended')
  if (asleep) return { value: 'En veille', sub: `${shortGpuName(asleep.name)} dort`, asleep: true }
  const any = gpus.find((g) => g.state === 'active')
  if (!any) return null
  if (any.freq_mhz !== null) return { value: formatFreq(any.freq_mhz), sub: shortGpuName(any.name), asleep: false }
  return { value: '—', sub: any.note ?? shortGpuName(any.name), asleep: false }
}

/** « / » devient « Système », « /home/mars/Jeux » devient « Jeux », « /media/mars/CLE » devient « CLE ». */
export function diskLabel(disk: Pick<DiskInfo, 'mount'>): string {
  if (disk.mount === '/') return 'Système'
  const parts = disk.mount.split('/').filter(Boolean)
  return parts[parts.length - 1] ?? disk.mount
}

export type Tone = 'good' | 'warn' | 'bad'

/** Couleur d'une jauge : vert, orange au-dessus de 70 %, rouge au-dessus de 90 %. */
export function toneFor(percent: number): Tone {
  return percent >= 90 ? 'bad' : percent >= 70 ? 'warn' : 'good'
}

export const HISTORY_SECONDS = 60

export interface Run {
  /** Valeur de l'attribut SVG `points` (x de 0 à 59, y de 0 en haut à 100 en bas). */
  points: string
  /** Même tracé fermé jusqu'à l'axe, pour remplir sous la courbe. */
  area: string
}

/**
 * Découpe une série en tronçons continus (une valeur absente, `null`, interrompt la courbe : une
 * carte endormie n'est pas une charge de 0 %). Les valeurs sont alignées à droite : la plus récente est
 * toujours au bord droit, le graphique se remplit de droite à gauche pendant la première minute.
 */
export function chartRuns(values: (number | null)[], max = 100): Run[] {
  const runs: Run[] = []
  let current: [number, number][] = []
  const offset = HISTORY_SECONDS - values.length
  const flush = () => {
    if (current.length > 0) {
      const points = current.map(([x, y]) => `${x},${y}`).join(' ')
      const first = current[0][0]
      const last = current[current.length - 1][0]
      runs.push({ points, area: `${first},100 ${points} ${last},100` })
    }
    current = []
  }
  values.forEach((value, index) => {
    if (value === null || Number.isNaN(value)) return flush()
    const y = 100 - Math.max(0, Math.min(100, (value / max) * 100))
    current.push([offset + index, Math.round(y * 10) / 10])
  })
  flush()
  return runs
}

/** Le plus grand débit de la série, arrondi au palier supérieur (pour une échelle lisible). */
export function niceMax(values: (number | null)[], floor = 100_000): number {
  const peak = Math.max(floor, ...values.map((v) => v ?? 0))
  const exponent = 10 ** Math.floor(Math.log10(peak))
  return Math.ceil(peak / exponent) * exponent
}
