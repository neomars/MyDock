import { describe, expect, it } from 'vitest'
import { activePlayer, positionNow, progressRatio, ratioToMs, subtitle } from './music'
import type { MusicData, PlayerInfo } from './protocol'

const player = (extra: Partial<PlayerInfo> = {}): PlayerInfo => ({
  id: 'spotify',
  name: 'Spotify',
  status: 'playing',
  title: 'Morceau',
  artist: 'Artiste',
  album: 'Album',
  length_ms: 200_000,
  position_ms: 30_000,
  rate: 1,
  art: null,
  can: { play: true, pause: true, next: true, previous: true, seek: true },
  shuffle: null,
  loop: null,
  ...extra,
})

describe('activePlayer', () => {
  it('suit le choix de l’agent', () => {
    const data: MusicData = { players: [player({ id: 'a' }), player({ id: 'b' })], active: 'b' }
    expect(activePlayer(data)?.id).toBe('b')
  })

  it('se replie sur le premier lecteur si le choix est inconnu', () => {
    const data: MusicData = { players: [player({ id: 'a' })], active: 'absent' }
    expect(activePlayer(data)?.id).toBe('a')
  })

  it('aucun lecteur, ou thème pas encore reçu : rien', () => {
    expect(activePlayer({ players: [], active: null })).toBeNull()
    expect(activePlayer(undefined)).toBeNull()
    expect(activePlayer({ unavailable: 'bus' } as unknown as MusicData)).toBeNull()
  })
})

describe('positionNow', () => {
  it('avance avec le temps tant que le morceau joue', () => {
    expect(positionNow(player(), 1_000, 1_000)).toBe(30_000)
    expect(positionNow(player(), 1_000, 6_000)).toBe(35_000)
  })

  it('respecte la vitesse de lecture', () => {
    expect(positionNow(player({ rate: 2 }), 0, 5_000)).toBe(40_000)
  })

  it('ne bouge pas en pause', () => {
    expect(positionNow(player({ status: 'paused' }), 0, 60_000)).toBe(30_000)
    expect(positionNow(player({ status: 'stopped' }), 0, 60_000)).toBe(30_000)
  })

  it('ne dépasse jamais la durée', () => {
    expect(positionNow(player(), 0, 10_000_000)).toBe(200_000)
  })

  it('sans position connue (navigateur) : rien à afficher', () => {
    expect(positionNow(player({ position_ms: null }), 0, 5_000)).toBeNull()
  })

  it('une horloge qui recule (nouvel instantané) ne fait jamais reculer la position', () => {
    expect(positionNow(player(), 5_000, 1_000)).toBe(30_000)
  })

  it('durée inconnue (direct) : pas de plafond', () => {
    expect(positionNow(player({ length_ms: null }), 0, 5_000)).toBe(35_000)
  })
})

describe('barre de progression', () => {
  it.each([
    [0, 200_000, 0],
    [0.5, 200_000, 100_000],
    [1, 200_000, 200_000],
    [-0.3, 200_000, 0],
    [1.7, 200_000, 200_000],
    [0.3333, 3_000, 1_000],
  ])('ratio %f de %d ms -> %d ms', (ratio, length, expected) => {
    expect(ratioToMs(ratio, length)).toBe(expected)
  })

  it('part jouée', () => {
    expect(progressRatio(50_000, 200_000)).toBe(0.25)
    expect(progressRatio(null, 200_000)).toBe(0)
    expect(progressRatio(10, null)).toBe(0)
    expect(progressRatio(900_000, 200_000)).toBe(1)
  })
})

describe('subtitle', () => {
  it('assemble artiste et album sans trou', () => {
    expect(subtitle({ artist: 'A', album: 'B' })).toBe('A · B')
    expect(subtitle({ artist: 'A', album: null })).toBe('A')
    expect(subtitle({ artist: null, album: 'B' })).toBe('B')
    expect(subtitle({ artist: null, album: null })).toBe('')
  })
})
