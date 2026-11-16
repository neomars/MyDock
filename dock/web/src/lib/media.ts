// Divertissement : Kiosque (les partages Samba de la Freebox), lu par VLC sur l'écran du dock.
// Fonctions pures, testées dans media.test.ts. L'état et les actions sont dans media.svelte.ts.

import type { MediaEntry, PlayerAudio, PlayerSnapshot } from './protocol'

export type MediaView = 'menu' | 'files'

/** Une lecture est-elle en cours ou en train de démarrer ? */
export const playerActive = (player: PlayerSnapshot | null | undefined): boolean =>
  player?.state === 'starting' || player?.state === 'playing' || player?.state === 'paused'

/** « 4:05 », « 1:02:03 » : une position ou une durée de lecture. */
export function formatPlayTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '–:––'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}

/** « 1,4 Go », « 350 Mo », « 12 Ko » : la taille d'un fichier. */
export function fileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  const units: [number, string][] = [
    [1024 ** 3, 'Go'],
    [1024 ** 2, 'Mo'],
    [1024, 'Ko'],
  ]
  for (const [size, unit] of units) {
    if (bytes >= size) {
      const value = bytes / size
      return `${value >= 100 || Number.isInteger(value) ? Math.round(value) : value.toFixed(1).replace('.', ',')} ${unit}`
    }
  }
  return `${bytes} o`
}

/** Sans accents ni majuscules : pour chercher « télé » en tapant « tele ». */
export const normalize = (text: string): string =>
  text
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .trim()

/** Le chemin d'un dossier d'un partage, en morceaux (« Films/Dessins » -> ["Films", "Dessins"]). */
export const pathParts = (path: string): string[] => path.split('/').filter(Boolean)

export const joinPath = (path: string, name: string): string => [...pathParts(path), name].join('/')

/** Le dossier au-dessus ; `null` à la racine du partage. */
export function parentPath(path: string): string | null {
  const parts = pathParts(path)
  return parts.length === 0 ? null : parts.slice(0, -1).join('/')
}

/** « Disque dur › Films › Dessins » : où l'on est. */
export function whereText(share: string | null, path: string): string {
  return [share ?? 'Partages', ...pathParts(path)].join(' › ')
}

/** Les entrées affichables d'un dossier : les dossiers d'abord (le dock les envoie déjà triés), puis les vidéos. */
export const folderCount = (entries: readonly MediaEntry[]): number => entries.filter((e) => e.dir).length

/** Avancement d'une lecture de 0 à 1 ; `null` tant que la durée est inconnue. */
export function progressRatio(player: PlayerSnapshot | null | undefined): number | null {
  if (!player || !player.length || player.position === null) return null
  return Math.min(1, Math.max(0, player.position / player.length))
}

/** La ligne d'état de la télécommande. */
export function stateText(player: PlayerSnapshot): string {
  switch (player.state) {
    case 'starting':
      return 'Ouverture du fichier…'
    case 'paused':
      return 'En pause'
    case 'playing':
      return `${formatPlayTime(player.position)} / ${formatPlayTime(player.length)}`
    case 'error':
      return player.error ?? 'La lecture a échoué.'
    default:
      return ''
  }
}

/** Le sous-titre de la tuile « Divertissement » de l'accueil pendant une lecture : le titre de la vidéo. */
export const nowPlayingTitle = (player: PlayerSnapshot | null | undefined): string | null =>
  playerActive(player) ? (player?.engine === 'mame' ? `En jeu : ${player.title ?? ''}`.trim() : (player?.title ?? null)) : null

/** Pourquoi la lecture ne peut pas démarrer d'ici (rien : elle le peut). */
export function playerBlocker(player: PlayerSnapshot | null | undefined): string | null {
  if (!player) return null // un dock plus ancien : on essaie, le dock répondra
  if (!player.available) return "L'assistant de lecture de l'écran ne répond pas : il démarre avec l'écran du dock."
  if (player.helper && !player.helper.vlc) return 'VLC est absent du Pi : sudo apt install vlc-bin vlc-plugin-base vlc-plugin-video-output.'
  if (player.helper && !player.helper.touch) return 'swayidle, ou wlrctl (ou xdotool), manque sur le Pi : sudo apt install swayidle wlrctl.'
  return null
}

/** Combien de secondes sautent les boutons « avancer » et « reculer ». */
export const SEEK_SECONDS = 30

/**
 * Un échec de lecture en deux parties : la phrase, et ce que VLC (ou smbclient) a écrit (« Détail : « … ». ») : le détail
 * s'affiche en plus petit, car il sert à comprendre, pas à lire.
 */
export function splitFailure(text: string): { main: string; detail: string | null } {
  const match = /^(.*?)\s*Détail : «\s*(.*?)\s*»\.?\s*$/s.exec(text)
  return match ? { main: match[1], detail: match[2] || null } : { main: text, detail: null }
}

/** L'avance du son des vidéos se règle de 50 ms en 50 ms, de 0 à 1 000 ms. */
export const ADVANCE_STEP_MS = 50
export const ADVANCE_MAX_MS = 1_000

/** Le prochain réglage de l'avance du son, borné (`direction` : +1 ou −1). */
export const stepAdvance = (current: number, direction: 1 | -1): number =>
  Math.min(ADVANCE_MAX_MS, Math.max(0, Math.round(current / ADVANCE_STEP_MS) * ADVANCE_STEP_MS + direction * ADVANCE_STEP_MS))

/** « 150 ms », « 1,0 s » : l'avance du son, lisible. */
export const advanceText = (ms: number): string => (ms >= 1_000 ? `${(ms / 1_000).toFixed(1).replace('.', ',')} s` : `${ms} ms`)

export type AudioLevel = 'good' | 'warn' | 'dim'

/** Une phrase sur où sort le son pendant une lecture : `null` quand il n'y a rien à dire (un dock plus ancien, ou rien ne joue). */
export function audioLine(audio: PlayerAudio | null | undefined): { text: string; level: AudioLevel } | null {
  if (!audio) return null
  if (audio.mode === 'local') return { text: 'Son : sur le Pi', level: 'dim' }
  switch (audio.state) {
    case 'streaming':
      return { text: 'Son : sur le PC', level: 'good' }
    case 'connecting':
    case 'idle':
      return { text: 'Son : connexion au PC…', level: 'dim' }
    case 'offline':
      return { text: 'PC hors ligne : pas de son', level: 'warn' }
    case 'old':
      return { text: 'Agent du PC trop ancien : pas de son', level: 'warn' }
    case 'error':
      return { text: audio.detail ? `Pas de son : ${audio.detail}` : 'Pas de son', level: 'warn' }
    default:
      return null
  }
}
