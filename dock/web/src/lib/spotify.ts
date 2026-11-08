// Aides pures de la page Spotify (aucune dépendance au réseau ni à Svelte). Testées dans spotify.test.ts.

import { formatTrackTime } from './format'
import type {
  LibrarySection,
  LoopMode,
  PlayerInfo,
  SpotifyItem,
  SpotifySearch,
  SpotifyStatus,
  SpotifyTrack,
} from './protocol'

/** Répéter, dans l'ordre du bouton de Spotify : rien, puis la liste, puis le morceau, puis rien. */
export function nextLoop(mode: LoopMode | null): LoopMode {
  if (mode === 'none' || mode === null) return 'playlist'
  return mode === 'playlist' ? 'track' : 'none'
}

export function loopLabel(mode: LoopMode | null): string {
  if (mode === 'playlist') return 'Répéter la liste (touche pour répéter le morceau)'
  if (mode === 'track') return 'Répéter le morceau (touche pour arrêter de répéter)'
  return 'Répéter : désactivé (touche pour répéter la liste)'
}

/** « 12 titres », « 1 titre » ; rien quand le nombre est inconnu. */
export function countText(count: number | null, noun: string): string | null {
  if (count === null) return null
  return `${count} ${noun}${count > 1 ? 's' : ''}`
}

/** « 3:42 », ou rien quand la durée est inconnue. */
export function durationText(ms: number | null): string {
  return ms === null ? '' : formatTrackTime(ms)
}

/** « A · B », sans trou quand un morceau de texte manque. */
function joined(parts: (string | null | undefined)[]): string {
  return parts.filter((part): part is string => Boolean(part)).join(' · ')
}

/** La ligne sous le nom, dans une liste : l'essentiel d'abord, car elle se coupe à droite. */
export function itemSubtitle(item: SpotifyItem): string {
  switch (item.kind) {
    case 'track':
      return joined([item.artists, item.album])
    case 'album':
      return joined([item.year, item.artists])
    case 'artist':
      return item.genres ?? 'Artiste'
    case 'playlist':
      return joined([item.owner, countText(item.tracks, 'titre')])
  }
}

const norm = (text: string | null | undefined): string => (text ?? '').trim().toLocaleLowerCase('fr')

/** Ce morceau est-il celui que joue le lecteur ? Le titre, et l'artiste quand les deux le disent. */
export function isNowPlaying(track: SpotifyTrack, player: PlayerInfo | null | undefined): boolean {
  if (!player?.title || norm(player.title) !== norm(track.name)) return false
  if (player.artist && track.artists && norm(player.artist) !== norm(track.artists)) return false
  return true
}

export type AccountView = 'loading' | 'ready' | 'unconfigured' | 'refused'

/** Ce que la recherche et la bibliothèque doivent montrer : le contenu, ou comment connecter le compte. */
export function accountView(status: SpotifyStatus | null): AccountView {
  if (status === null) return 'loading'
  if (!status.configured) return 'unconfigured'
  return status.connected ? 'ready' : 'refused'
}

export interface SectionInfo {
  id: LibrarySection
  label: string
  /** Une phrase sous le nom, sur le bouton de la bibliothèque. */
  hint: string
}

/** Les rubriques de « Ta bibliothèque », dans l'ordre de Spotify. */
export const SECTIONS: readonly SectionInfo[] = [
  { id: 'playlists', label: 'Playlists', hint: 'Les tiennes et celles que tu suis' },
  { id: 'liked', label: 'Titres likés', hint: 'Tes coups de cœur' },
  { id: 'albums', label: 'Albums', hint: 'Ceux que tu as enregistrés' },
  { id: 'artists', label: 'Artistes', hint: 'Ceux que tu suis' },
  { id: 'recent', label: 'Écoutés récemment', hint: 'Les derniers morceaux joués' },
]

export const sectionLabel = (id: LibrarySection): string => SECTIONS.find((s) => s.id === id)?.label ?? id

export interface SearchGroup {
  id: 'tracks' | 'artists' | 'albums' | 'playlists'
  label: string
  items: SpotifyItem[]
}

/** Les groupes non vides d'une recherche, dans l'ordre où on les lit : morceaux, artistes, albums, playlists. */
export function searchGroups(result: SpotifySearch | null | undefined): SearchGroup[] {
  if (!result) return []
  const groups: SearchGroup[] = [
    { id: 'tracks', label: 'Morceaux', items: result.tracks },
    { id: 'artists', label: 'Artistes', items: result.artists },
    { id: 'albums', label: 'Albums', items: result.albums },
    { id: 'playlists', label: 'Playlists', items: result.playlists },
  ]
  return groups.filter((group) => group.items.length > 0)
}

/** Le contenu (album ou playlist) dans lequel jouer un morceau touché : celui de la page ouverte, sinon son album. */
export function contextFor(track: SpotifyTrack, page?: { kind: string; uri: string } | null): string | undefined {
  if (page && (page.kind === 'album' || page.kind === 'playlist')) return page.uri
  return track.album_id ? `spotify:album:${track.album_id}` : undefined
}

/** Mise en forme d'une recherche saisie : espaces superflus retirés, longueur bornée comme le fait l'agent. */
export function cleanQuery(text: string): string {
  return text.replace(/\s+/g, ' ').trim().slice(0, 120)
}
