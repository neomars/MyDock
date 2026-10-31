// Aides pures de la page Musique. Testées dans music.test.ts.

import type { MusicData, PlayerInfo } from './protocol'

/** Le lecteur que le dock pilote (l'agent le désigne ; on se replie sur le premier). */
export function activePlayer(data: MusicData | undefined | null): PlayerInfo | null {
  if (!data || !data.players || data.players.length === 0) return null
  return data.players.find((p) => p.id === data.active) ?? data.players[0]
}

/**
 * Position dans le morceau à l'instant `now` (ms, horloge `performance.now()`), sachant que
 * l'instantané reçu à `receivedAt` la donnait pour `position_ms`. Elle avance seulement en lecture
 * et ne dépasse jamais la durée.
 */
export function positionNow(player: PlayerInfo, receivedAt: number, now: number): number | null {
  if (player.position_ms === null) return null
  let position = player.position_ms
  if (player.status === 'playing') position += Math.max(0, now - receivedAt) * (player.rate || 1)
  if (player.length_ms !== null) position = Math.min(position, player.length_ms)
  return Math.max(0, Math.round(position))
}

/** Position (ms) correspondant à un appui à `ratio` (0 à 1) de la barre ; bornée à la durée. */
export function ratioToMs(ratio: number, lengthMs: number): number {
  return Math.round(Math.max(0, Math.min(1, ratio)) * lengthMs)
}

/** Part du morceau déjà jouée (0 à 1) ; 0 si la durée est inconnue. */
export function progressRatio(positionMs: number | null, lengthMs: number | null): number {
  if (positionMs === null || !lengthMs) return 0
  return Math.max(0, Math.min(1, positionMs / lengthMs))
}

/** « Artiste · Album » sans trou quand l'un manque. */
export function subtitle(player: Pick<PlayerInfo, 'artist' | 'album'>): string {
  return [player.artist, player.album].filter(Boolean).join(' · ')
}
