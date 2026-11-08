import { describe, expect, it } from 'vitest'
import type { PlayerInfo, SpotifyAlbum, SpotifyArtist, SpotifyPlaylist, SpotifySearch, SpotifyStatus, SpotifyTrack } from './protocol'
import {
  SECTIONS,
  accountView,
  cleanQuery,
  contextFor,
  countText,
  durationText,
  isNowPlaying,
  itemSubtitle,
  loopLabel,
  nextLoop,
  searchGroups,
  sectionLabel,
} from './spotify'

const track = (extra: Partial<SpotifyTrack> = {}): SpotifyTrack => ({
  kind: 'track',
  id: 'a'.repeat(22),
  uri: `spotify:track:${'a'.repeat(22)}`,
  name: 'Nuit blanche',
  artists: 'Les Fantômes',
  album: 'Néons',
  album_id: 'b'.repeat(22),
  duration_ms: 222_000,
  number: 1,
  explicit: false,
  playable: true,
  art: null,
  ...extra,
})

const player = (extra: Partial<PlayerInfo> = {}): PlayerInfo => ({
  id: 'spotify',
  name: 'Spotify',
  status: 'playing',
  title: 'Nuit blanche',
  artist: 'Les Fantômes',
  album: 'Néons',
  length_ms: 222_000,
  position_ms: 0,
  rate: 1,
  art: null,
  can: { play: true, pause: true, next: true, previous: true, seek: true },
  shuffle: false,
  loop: 'none',
  ...extra,
})

const status = (extra: Partial<SpotifyStatus> = {}): SpotifyStatus => ({
  installed: true,
  running: true,
  configured: true,
  connected: true,
  user: 'Marie',
  premium: true,
  message: null,
  ...extra,
})

describe('répéter', () => {
  it('tourne dans l’ordre de Spotify : rien, la liste, le morceau, rien', () => {
    expect(nextLoop('none')).toBe('playlist')
    expect(nextLoop('playlist')).toBe('track')
    expect(nextLoop('track')).toBe('none')
    expect(nextLoop(null)).toBe('playlist')
  })
  it('dit où on en est et ce que fera la touche', () => {
    expect(loopLabel('none')).toMatch(/désactivé/)
    expect(loopLabel('playlist')).toMatch(/la liste/)
    expect(loopLabel('track')).toMatch(/le morceau/)
    expect(loopLabel(null)).toMatch(/désactivé/)
  })
})

describe('textes', () => {
  it('accorde les titres', () => {
    expect(countText(0, 'titre')).toBe('0 titre')
    expect(countText(1, 'titre')).toBe('1 titre')
    expect(countText(12, 'titre')).toBe('12 titres')
    expect(countText(null, 'titre')).toBeNull()
  })
  it('écrit la durée', () => {
    expect(durationText(222_000)).toBe('3:42')
    expect(durationText(3_735_000)).toBe('1:02:15')
    expect(durationText(null)).toBe('')
  })
  it('compose la ligne sous le nom sans trou', () => {
    expect(itemSubtitle(track())).toBe('Les Fantômes · Néons')
    expect(itemSubtitle(track({ album: null }))).toBe('Les Fantômes')
    expect(itemSubtitle(track({ artists: null, album: null }))).toBe('')
    const album: SpotifyAlbum = { kind: 'album', id: 'x', uri: 'u', name: 'N', artists: 'Mizu', year: '2021', type: 'album', tracks: 4, art: null }
    expect(itemSubtitle(album)).toBe('2021 · Mizu')
    expect(itemSubtitle({ ...album, year: null })).toBe('Mizu')
    const artist: SpotifyArtist = { kind: 'artist', id: 'x', uri: 'u', name: 'Mizu', genres: 'pop, électro', art: null }
    expect(itemSubtitle(artist)).toBe('pop, électro')
    expect(itemSubtitle({ ...artist, genres: null })).toBe('Artiste')
    const playlist: SpotifyPlaylist = { kind: 'playlist', id: 'x', uri: 'u', name: 'P', owner: 'Spotify', tracks: 20, art: null }
    expect(itemSubtitle(playlist)).toBe('Spotify · 20 titres')
    expect(itemSubtitle({ ...playlist, owner: null, tracks: 1 })).toBe('1 titre')
  })
  it('nettoie une recherche tapée', () => {
    expect(cleanQuery('  les   fantômes \n')).toBe('les fantômes')
    expect(cleanQuery('x'.repeat(300))).toHaveLength(120)
    expect(cleanQuery('   ')).toBe('')
  })
})

describe('morceau en cours', () => {
  it('reconnaît le morceau joué par son titre et son artiste', () => {
    expect(isNowPlaying(track(), player())).toBe(true)
    expect(isNowPlaying(track({ name: 'NUIT BLANCHE ' }), player())).toBe(true)
  })
  it('refuse un autre morceau, ou le même titre d’un autre artiste', () => {
    expect(isNowPlaying(track({ name: 'Écho' }), player())).toBe(false)
    expect(isNowPlaying(track({ artists: 'Mizu' }), player())).toBe(false)
  })
  it('se contente du titre quand l’un des deux ignore l’artiste', () => {
    expect(isNowPlaying(track(), player({ artist: null }))).toBe(true)
    expect(isNowPlaying(track({ artists: null }), player())).toBe(true)
  })
  it('rien ne joue : aucun morceau n’est « en cours »', () => {
    expect(isNowPlaying(track(), null)).toBe(false)
    expect(isNowPlaying(track(), player({ title: null }))).toBe(false)
  })
})

describe('compte', () => {
  it('choisit ce que montrent la recherche et la bibliothèque', () => {
    expect(accountView(null)).toBe('loading')
    expect(accountView(status())).toBe('ready')
    expect(accountView(status({ configured: false, connected: false }))).toBe('unconfigured')
    expect(accountView(status({ connected: false }))).toBe('refused')
  })
})

describe('bibliothèque et recherche', () => {
  it('a les cinq rubriques de Spotify, chacune avec un nom', () => {
    expect(SECTIONS.map((s) => s.id)).toEqual(['playlists', 'liked', 'albums', 'artists', 'recent'])
    for (const section of SECTIONS) {
      expect(section.label.length).toBeGreaterThan(2)
      expect(sectionLabel(section.id)).toBe(section.label)
    }
  })
  it('range les résultats : morceaux, artistes, albums, playlists, sans les groupes vides', () => {
    const result: SpotifySearch = { query: 'x', tracks: [track()], albums: [], artists: [{ kind: 'artist', id: 'a', uri: 'u', name: 'A', genres: null, art: null }], playlists: [] }
    expect(searchGroups(result).map((g) => g.id)).toEqual(['tracks', 'artists'])
    expect(searchGroups(null)).toEqual([])
    expect(searchGroups({ query: 'x', tracks: [], albums: [], artists: [], playlists: [] })).toEqual([])
  })
  it('joue un morceau dans la page ouverte (album, playlist), sinon dans son album', () => {
    const album = { kind: 'album', uri: `spotify:album:${'c'.repeat(22)}` }
    const playlist = { kind: 'playlist', uri: `spotify:playlist:${'d'.repeat(22)}` }
    expect(contextFor(track(), album)).toBe(album.uri)
    expect(contextFor(track(), playlist)).toBe(playlist.uri)
    expect(contextFor(track(), { kind: 'artist', uri: `spotify:artist:${'e'.repeat(22)}` })).toBe(`spotify:album:${'b'.repeat(22)}`)
    expect(contextFor(track(), null)).toBe(`spotify:album:${'b'.repeat(22)}`)
    expect(contextFor(track({ album_id: null }), null)).toBeUndefined()
  })
})
