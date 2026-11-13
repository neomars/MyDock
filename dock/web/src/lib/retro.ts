// Retrogame : les jeux d'arcade de MAME sur l'écran du dock, joués à la manette Bluetooth.
// Fonctions pures, testées dans retro.test.ts. L'état et les actions sont dans retro.svelte.ts.

import { normalize, playerActive } from './media'
import type { PadDevice, PadsPairing, PadsSnapshot, PlayerSnapshot, RetroGame, RetroSnapshot, UploadInfo } from './protocol'

export type RetroView = 'games' | 'pads' | 'upload'

/** Un jeu est-il en cours (ou en train de démarrer) sur l'écran ? */
export const gameActive = (player: PlayerSnapshot | null | undefined): boolean => playerActive(player) && player?.engine === 'mame'

/** « aucun jeu », « 1 jeu », « 12 jeux ». */
export function gameCount(n: number): string {
  return n === 0 ? 'aucun jeu' : `${n} jeu${n > 1 ? 'x' : ''}`
}

/** Les jeux qui répondent à une recherche : chaque mot doit se trouver dans le nom, l'éditeur, l'année ou le nom MAME. */
export function filterGames(games: readonly RetroGame[], query: string): RetroGame[] {
  const wanted = normalize(query)
  if (!wanted) return [...games]
  const words = wanted.split(/\s+/)
  return games.filter((g) => {
    const haystack = normalize(`${g.title} ${g.maker} ${g.year} ${g.id}`)
    return words.every((w) => haystack.includes(w))
  })
}

/** L'émulation de MAME n'est pas parfaite : à dire, sans interdire de jouer. */
export function statusLabel(status: string): string | null {
  if (status === 'imperfect') return 'émulation imparfaite'
  if (status === 'preliminary') return 'en développement'
  return null
}

// --- les manettes ------------------------------------------------------------------------------------------------------

export const connectedPads = (pads: PadsSnapshot | null | undefined): PadDevice[] => (pads?.devices ?? []).filter((d) => d.connected)

/** « 80 % » ; rien quand la manette ne dit pas sa charge. */
export const batteryText = (battery: number | null | undefined): string =>
  battery === null || battery === undefined || !Number.isFinite(battery) ? '' : `${Math.round(Math.min(100, Math.max(0, battery)))} %`

/**
 * La pastille d'en-tête de la liste des jeux, en deux lignes : « Manette connectée » puis « Xbox Wireless Controller · 80 % »,
 * ou seulement « Aucune manette ». `text` est les deux ensemble (pour les lecteurs d'écran et les essais).
 */
export function padSummary(pads: PadsSnapshot | null | undefined): { label: string; detail: string; text: string; connected: boolean } {
  const connected = connectedPads(pads)
  if (connected.length === 0) return { label: 'Aucune manette', detail: '', text: 'Aucune manette', connected: false }
  if (connected.length === 1) {
    const [pad] = connected
    const charge = batteryText(pad.battery)
    const detail = `${pad.name}${charge ? ` · ${charge}` : ''}`
    return { label: 'Manette connectée', detail, text: `Manette connectée : ${detail}`, connected: true }
  }
  const detail = connected.map((pad) => pad.name).join(', ')
  return { label: `${connected.length} manettes connectées`, detail, text: `${connected.length} manettes connectées`, connected: true }
}

/** L'avertissement (qui n'empêche rien) quand on s'apprête à jouer sans manette. */
export const noPadWarning = (pads: PadsSnapshot | null | undefined): string | null =>
  connectedPads(pads).length > 0 || pads === null
    ? null
    : 'Aucune manette connectée : le jeu se lancera quand même. Touche « Manettes » pour en associer une.'

/** Les manettes associées, puis celles que la recherche vient de trouver. */
export const pairedPads = (pads: PadsSnapshot | null | undefined): PadDevice[] => (pads?.devices ?? []).filter((d) => d.paired)
export const foundPads = (pads: PadsSnapshot | null | undefined): PadDevice[] => (pads?.devices ?? []).filter((d) => !d.paired)

/** Les secondes qu'il reste à une recherche de manettes (0 : terminée). */
export function scanSecondsLeft(pads: PadsSnapshot | null | undefined, now: number): number {
  if (!pads?.scanning || pads.scan_until === null) return 0
  return Math.max(0, Math.round((pads.scan_until - now) / 1000))
}

/** La phrase qui accompagne l'association d'une manette ; `null` s'il n'y en a pas. */
export function pairingText(pairing: PadsPairing | null | undefined, pads: PadsSnapshot | null | undefined): string | null {
  if (!pairing) return null
  const pad = pads?.devices.find((d) => d.id === pairing.id)
  return `${pad ? `${pad.name} : ` : ''}${pairing.message}`
}

/** Pourquoi on ne peut pas chercher de manette d'ici (rien : on le peut). */
export function padsBlocker(pads: PadsSnapshot | null | undefined): string | null {
  if (!pads) return null // un dock plus ancien : on essaie, le dock répondra
  if (pads.available) return null
  if (pads.blocked) return 'Le Bluetooth est coupé : sudo rfkill unblock bluetooth, puis réessaie.'
  return pads.reason ?? 'Aucun adaptateur Bluetooth : le Pi 4 en a un intégré (sudo apt install bluez).'
}

/** Comment mettre une manette en mode association, marque par marque. */
export const PAD_HELP: readonly { name: string; steps: string }[] = [
  { name: 'Manette PS4 / PS5', steps: 'Maintiens PS + Partage (Create) jusqu’à ce que la barre lumineuse clignote vite.' },
  { name: 'Manette Xbox', steps: 'Allume-la (bouton Xbox), puis maintiens le petit bouton d’association, sur le dessus, jusqu’au clignotement rapide.' },
  { name: '8BitDo', steps: 'Éteinte, maintiens Start + Y (mode Switch) ou Start + X (mode Xbox), jusqu’au clignotement rapide.' },
  { name: 'Switch Pro', steps: 'Maintiens le petit bouton d’association, en haut, jusqu’à ce que les voyants courent.' },
]

// --- les broches du bouton ------------------------------------------------------------------------------------------------

/** La broche physique du connecteur de 40 broches de chaque GPIO (BCM) qu'on peut choisir. */
export const PHYSICAL_PIN: Readonly<Record<number, number>> = {
  2: 3, 3: 5, 4: 7, 14: 8, 15: 10, 17: 11, 18: 12, 27: 13, 22: 15, 23: 16, 24: 18, 10: 19, 9: 21,
  25: 22, 11: 23, 8: 24, 7: 26, 5: 29, 6: 31, 12: 32, 13: 33, 19: 35, 16: 36, 26: 37, 20: 38, 21: 40,
} // prettier-ignore

export const GPIO_CHOICES: readonly number[] = Object.keys(PHYSICAL_PIN)
  .map(Number)
  .sort((a, b) => a - b)

/** « GPIO17 (broche 11) ». */
export const gpioLabel = (pin: number): string => `GPIO${pin}${PHYSICAL_PIN[pin] ? ` (broche ${PHYSICAL_PIN[pin]})` : ''}`

/** Le choix suivant ou précédent dans la liste des broches (sans dépasser). */
export function stepGpio(pin: number, direction: 1 | -1): number {
  const at = GPIO_CHOICES.indexOf(pin)
  if (at < 0) return GPIO_CHOICES[0]
  return GPIO_CHOICES[Math.min(GPIO_CHOICES.length - 1, Math.max(0, at + direction))]
}

// --- la liste et le lancement ---------------------------------------------------------------------------------------------

/** La vérification de la liste : 0 à 1 ; `null` quand elle ne tourne pas. */
export function scanRatio(retro: RetroSnapshot | null | undefined): number | null {
  if (!retro || retro.state !== 'scanning' || retro.total <= 0) return null
  return Math.min(1, Math.max(0, retro.done / retro.total))
}

/** Pourquoi on ne peut pas lancer de jeu d'ici (rien : on le peut). */
export function retroBlocker(player: PlayerSnapshot | null | undefined, retro: RetroSnapshot | null | undefined): string | null {
  if (retro && !retro.mame) return 'MAME est absent du Pi : sudo apt install mame.'
  if (player && !player.available) return "L'assistant de lecture de l'écran ne répond pas : il démarre avec l'écran du dock."
  if (player?.helper && player.helper.mame === false) return 'MAME est absent du Pi : sudo apt install mame.'
  return null
}

/** Ce qu'on dit à la place d'une liste vide. */
export function emptyText(retro: RetroSnapshot | null | undefined, query: string): string {
  if (query) return 'Aucun jeu ne correspond.'
  if (retro?.state === 'scanning') return 'Vérification des jeux en cours…'
  return `Aucun jeu : copie des fichiers .zip dans ${retro?.roms_dir ?? 'le dossier des ROMs'} ou envoie-les depuis le PC.`
}

/** La ligne d'état de l'écran « en jeu ». */
export function gameStateText(player: PlayerSnapshot): string {
  if (player.state === 'starting') return 'Lancement de MAME…'
  return 'En jeu'
}

// --- l'envoi de jeux depuis le PC -----------------------------------------------------------------------------------------

/** « 14:32 » : le temps qu'il reste à la session d'envoi. */
export function countdown(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export const uploadSecondsLeft = (upload: UploadInfo | null | undefined, now: number): number =>
  upload?.open ? Math.max(0, Math.ceil((upload.expires_at - now) / 1000)) : 0

/** Comment s'est finie la dernière session d'envoi ; `null` s'il n'y en a pas (ou si elle est ouverte). */
export function uploadResultText(upload: UploadInfo | null | undefined): string | null {
  if (!upload || upload.open || !upload.last) return null
  const { result, files } = upload.last
  const received = files === 0 ? 'aucun jeu reçu' : files === 1 ? '1 jeu reçu' : `${files} jeux reçus`
  if (result === 'locked') return `Trop de codes faux : l’envoi a été annulé (${received}).`
  if (result === 'expired') return `La session d’envoi a expiré (${received}).`
  return `Envoi terminé : ${received}.`
}

/** La page à ouvrir sur le PC : la première adresse du Pi, sinon son nom. */
export const uploadAddress = (upload: UploadInfo | null | undefined, hostname: string | undefined): string =>
  (upload?.open ? upload.urls[0] : undefined) ?? (hostname ? `http://${hostname}.local/jeux` : 'http://adresse-du-pi/jeux')
