// Économiseur d'écran : les réglages proposés, et le choix de ce qui passe à l'écran (fonctions pures, testées dans
// saver.test.ts). Le dessin des scènes est dans scenes.ts.

export interface SaverOption {
  /** Secondes d'inactivité avant l'économiseur ; 0 : jamais. Mêmes valeurs que côté dock (store.py). */
  id: number
  label: string
}

export const SAVER_OPTIONS: readonly SaverOption[] = [
  { id: 0, label: 'Jamais' },
  { id: 60, label: '1 min' },
  { id: 120, label: '2 min' },
  { id: 300, label: '5 min' },
  { id: 600, label: '10 min' },
  { id: 900, label: '15 min' },
  { id: 1800, label: '30 min' },
]

export type SaverContent = 'all' | 'scenes' | 'videos'

export const CONTENT_OPTIONS: readonly { id: SaverContent; label: string }[] = [
  { id: 'all', label: 'Tout' },
  { id: 'scenes', label: 'Scènes' },
  { id: 'videos', label: 'Mes vidéos' },
]

export const SCENE_IDS = ['mountains', 'ocean', 'city', 'aurora', 'space'] as const
export type SceneId = (typeof SCENE_IDS)[number]

/** Les noms que l'économiseur affiche, comme les légendes des vues aériennes d'Apple TV. */
export const SCENE_TITLES: Record<SceneId, string> = {
  mountains: 'Montagnes, lever du jour',
  ocean: 'Fonds marins',
  city: 'Ville de nuit, vue aérienne',
  aurora: 'Aurore boréale',
  space: 'Voyage parmi les étoiles',
}

/** Une vidéo du dossier du Pi (thème « /api/screensaver »). */
export interface SaverVideo {
  name: string
  size: number
  mime: string
}

export type SaverItem = { kind: 'scene'; id: SceneId } | { kind: 'video'; name: string }

/** Une scène dure ce temps-là ; une vidéo joue jusqu'au bout, mais au plus ce temps-là. */
export const SCENE_SECONDS = 75
export const VIDEO_MAX_SECONDS = 150

/** « 5 minutes » : pour les phrases. */
export function saverPhrase(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  return `${minutes} minute${minutes > 1 ? 's' : ''}`
}

/** L'écran s'éteint avant (ou en même temps) : l'économiseur n'aurait jamais le temps de paraître. */
export function saverNeverShows(saverSeconds: number, sleepSeconds: number): boolean {
  return saverSeconds > 0 && sleepSeconds > 0 && saverSeconds >= sleepSeconds
}

/** Le nom d'une vidéo, sans son extension : c'est sa légende. */
export function videoTitle(name: string): string {
  return name.replace(/\.[^.]+$/, '').replace(/[_]+/g, ' ').trim() || name
}

/** Les vidéos qu'on peut encore montrer (celles que le navigateur n'a pas refusé de lire sont écartées). */
export function playable(videos: readonly SaverVideo[], bad: ReadonlySet<string>): SaverVideo[] {
  return videos.filter((v) => !bad.has(v.name))
}

const same = (a: SaverItem | null, b: SaverItem): boolean =>
  a !== null && a.kind === b.kind && (a.kind === 'scene' ? a.id === (b as { id: SceneId }).id : a.name === (b as { name: string }).name)

/**
 * Ce qui passe ensuite. « Tout » mêle scènes et vidéos (une fois sur deux une vidéo, quand il y en a) ; « Mes vidéos »
 * sans aucune vidéo lisible retombe sur les scènes plutôt que sur un écran vide. On ne rejoue jamais deux fois de suite
 * la même chose quand il y a le choix. `rng` : un nombre dans [0, 1[ (Math.random, ou un faux dans les tests).
 */
export function pickNext(
  content: SaverContent,
  videos: readonly SaverVideo[],
  bad: ReadonlySet<string>,
  previous: SaverItem | null,
  rng: () => number,
): SaverItem {
  const clips: SaverItem[] = content === 'scenes' ? [] : playable(videos, bad).map((v) => ({ kind: 'video', name: v.name }))
  const scenes: SaverItem[] = content === 'videos' && clips.length > 0 ? [] : SCENE_IDS.map((id) => ({ kind: 'scene', id }))
  const useClip = clips.length > 0 && (scenes.length === 0 || rng() < 0.5)
  const pool = useClip ? clips : scenes
  const fresh = pool.filter((item) => !same(previous, item))
  const choices = fresh.length > 0 ? fresh : pool
  return choices[Math.min(choices.length - 1, Math.floor(rng() * choices.length))]
}

/** « 14:05 » et « mardi 10 novembre » pour la légende. */
export function clockText(now: Date): { time: string; date: string } {
  return {
    time: now.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }),
    date: now.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' }),
  }
}
