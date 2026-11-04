// Association d'un PC au dock : ce que l'écran en montre. Fonctions pures, sans état (voir pairing.svelte.ts).

import type { DockInfo, PairingInfo, PairingLast, PortalInfo } from './protocol'

/** Essais incorrects permis par code (la même limite que dock/server/mydock_dock/pairing.py). */
export const MAX_WRONG = 5

/** Un nouveau PC annoncé il y a moins de ce délai fait afficher « associé » ; plus ancien, c'est de l'histoire. */
export const FRESH_PAIRING_MS = 20_000

/** Un PC est configuré (par association ou saisi à la main dans les Réglages). */
export function isPaired(dock: DockInfo | null | undefined): boolean {
  return !!dock && !!dock.pc.host && dock.pc.token_set
}

/** Le portail répond : un PC peut y télécharger son paquet et s'associer. */
export function portalReady(portal: PortalInfo | null | undefined): boolean {
  return !!portal && portal.enabled && portal.port !== null && !portal.error
}

/**
 * Les adresses à montrer au PC, `max` au plus : les adresses IP d'abord (elles marchent toujours),
 * puis « nom.local » qui est plus facile à retenir mais que certains réseaux ne résolvent pas.
 * Le nom .local garde toujours sa place quand il y en a un.
 */
export function shownUrls(urls: readonly string[], max = 3): string[] {
  const local = urls.filter((url) => /\.local(:\d+)?$/i.test(url)).slice(0, 1)
  const ips = urls.filter((url) => !local.includes(url))
  return [...ips.slice(0, Math.max(1, max - local.length)), ...local].slice(0, Math.max(1, max))
}

/** Millisecondes avant l'expiration du code (jamais négatif) ; null quand aucun code n'est affiché. */
export function remainingMs(pairing: PairingInfo | null | undefined, now: number): number | null {
  if (!pairing?.open) return null
  return Math.max(0, pairing.expires_at - now)
}

/** « 3 essais restants » quand le code a déjà subi des erreurs ; null tant qu'aucune n'a eu lieu. */
export function attemptsNote(pairing: PairingInfo | null | undefined): string | null {
  if (!pairing?.open || pairing.wrong_left >= MAX_WRONG) return null
  if (pairing.wrong_left <= 0) return 'Plus aucun essai : un nouveau code va être affiché.'
  return `${pairing.wrong_left} essai${pairing.wrong_left > 1 ? 's' : ''} restant${pairing.wrong_left > 1 ? 's' : ''} sur ce code`
}

export interface Notice {
  tone: 'good' | 'warn' | 'bad' | 'dim'
  text: string
}

/** Ce que dit la dernière session terminée (Réglages), ou null quand il n'y a rien à dire. */
export function lastNotice(last: PairingLast | null | undefined): Notice | null {
  switch (last?.result) {
    case 'paired':
      return { tone: 'good', text: `PC « ${last.name} » associé.` }
    case 'expired':
      return { tone: 'warn', text: 'Le code a expiré (il est valable dix minutes).' }
    case 'locked':
      return { tone: 'bad', text: 'Trop de codes incorrects : ce code a été annulé.' }
    default:
      return null
  }
}

/** Le message à afficher quand un PC vient de s'associer ; null si c'est de l'histoire ancienne. */
export function justPaired(last: PairingLast | null | undefined, now: number, seen: number): string | null {
  if (last?.result !== 'paired' || last.at <= seen) return null
  if (now - last.at > FRESH_PAIRING_MS) return null
  return `PC « ${last.name} » associé : le dock s'y connecte.`
}
