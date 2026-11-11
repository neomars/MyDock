// Divertissement : Freetv (les chaînes de la Freebox) et Kiosque (ses partages Samba), lus par VLC sur l'écran du dock.
// Fonctions pures, testées dans media.test.ts. L'état et les actions sont dans media.svelte.ts.

import type { MediaEntry, PlayerSnapshot, TvChannel } from './protocol'

export type MediaView = 'menu' | 'tv' | 'files'

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

/**
 * Les chaînes qui répondent à une recherche : un numéro (« 6 » trouve la chaîne 6, puis celles dont le numéro commence
 * par 6) ou des mots du nom (tous doivent s'y trouver, dans n'importe quel ordre). Sans recherche, toute la liste.
 */
export function filterChannels(channels: readonly TvChannel[], query: string): TvChannel[] {
  const wanted = normalize(query)
  if (!wanted) return [...channels]
  if (/^\d+$/.test(wanted)) {
    const exact = channels.filter((c) => String(c.number) === wanted)
    const prefix = channels.filter((c) => c.number !== null && String(c.number) !== wanted && String(c.number).startsWith(wanted))
    return [...exact, ...prefix]
  }
  const words = wanted.split(/\s+/)
  return channels.filter((c) => {
    const name = normalize(c.name)
    return words.every((w) => name.includes(w))
  })
}

/** « 12 chaînes », « 1 chaîne », « aucune chaîne ». */
export function channelCount(n: number): string {
  return n === 0 ? 'aucune chaîne' : `${n} chaîne${n > 1 ? 's' : ''}`
}

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

/** Avancement d'une lecture de 0 à 1 ; `null` pour un direct ou tant que la durée est inconnue. */
export function progressRatio(player: PlayerSnapshot | null | undefined): number | null {
  if (!player || player.live || !player.length || player.position === null) return null
  return Math.min(1, Math.max(0, player.position / player.length))
}

/** La ligne d'état de la télécommande. */
export function stateText(player: PlayerSnapshot): string {
  switch (player.state) {
    case 'starting':
      return player.live ? 'Connexion à la chaîne…' : 'Ouverture du fichier…'
    case 'paused':
      return 'En pause'
    case 'playing':
      return player.live ? 'En direct' : `${formatPlayTime(player.position)} / ${formatPlayTime(player.length)}`
    case 'error':
      return player.error ?? 'La lecture a échoué.'
    default:
      return ''
  }
}

/** Le sous-titre de la tuile « Divertissement » de l'accueil pendant une lecture : « TF1 ». */
export const nowPlayingTitle = (player: PlayerSnapshot | null | undefined): string | null =>
  playerActive(player) ? (player?.title ?? null) : null

/** Pourquoi la lecture ne peut pas démarrer d'ici (rien : elle le peut). */
export function playerBlocker(player: PlayerSnapshot | null | undefined): string | null {
  if (!player) return null // un dock plus ancien : on essaie, le dock répondra
  if (!player.available) return "L'assistant de lecture de l'écran ne répond pas : il démarre avec l'écran du dock."
  if (player.helper && !player.helper.vlc) return 'VLC est absent du Pi : sudo apt install vlc-bin vlc-plugin-base vlc-plugin-video-output.'
  if (player.helper && !player.helper.touch) return 'swayidle ou wlrctl manque sur le Pi : sudo apt install swayidle wlrctl.'
  return null
}

/** Combien de secondes sautent les boutons « avancer » et « reculer ». */
export const SEEK_SECONDS = 30
