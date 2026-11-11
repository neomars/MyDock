import { describe, expect, it } from 'vitest'
import {
  channelCount,
  fileSize,
  filterChannels,
  formatPlayTime,
  joinPath,
  normalize,
  nowPlayingTitle,
  parentPath,
  pathParts,
  playerActive,
  playerBlocker,
  progressRatio,
  splitFailure,
  stateText,
  whereText,
} from './media'
import type { PlayerSnapshot, TvChannel } from './protocol'

const channel = (id: string, number: number | null, name: string): TvChannel => ({ id, number, name, qualities: ['sd'] })
const CHANNELS = [channel('a', 1, 'TF1'), channel('b', 2, 'France 2'), channel('c', 6, 'M6'), channel('d', 61, 'Télé Paris'), channel('e', null, 'Radio Classique'), channel('f', 16, 'Équidia')]

const player = (extra: Partial<PlayerSnapshot> = {}): PlayerSnapshot => ({
  available: true,
  state: 'playing',
  kind: 'file',
  title: 'Un film',
  subtitle: 'Disque dur/Films',
  live: false,
  position: 65,
  length: 5400,
  volume: 70,
  front: 'dock',
  error: null,
  helper: { vlc: true, live555: false, ffmpeg: true, touch: true, demo: false },
  ...extra,
})

describe('les durées et les tailles', () => {
  it('écrivent une position de lecture', () => {
    expect(formatPlayTime(0)).toBe('0:00')
    expect(formatPlayTime(65)).toBe('1:05')
    expect(formatPlayTime(3723)).toBe('1:02:03')
    expect(formatPlayTime(null)).toBe('–:––')
    expect(formatPlayTime(-4)).toBe('–:––')
    expect(formatPlayTime(Number.NaN)).toBe('–:––')
  })
  it('écrivent la taille d’un fichier', () => {
    expect(fileSize(512)).toBe('512 o')
    expect(fileSize(2048)).toBe('2 Ko')
    expect(fileSize(350 * 1024 ** 2)).toBe('350 Mo')
    expect(fileSize(1.4 * 1024 ** 3)).toBe('1,4 Go')
    expect(fileSize(24_555_840)).toBe('23,4 Mo')
    expect(fileSize(-1)).toBe('')
  })
})

describe('la recherche d’une chaîne', () => {
  it('sans recherche rend toute la liste', () => {
    expect(filterChannels(CHANNELS, '')).toHaveLength(6)
    expect(filterChannels(CHANNELS, '   ')).toHaveLength(6)
  })
  it('ignore les accents et les majuscules', () => {
    expect(filterChannels(CHANNELS, 'tele').map((c) => c.id)).toEqual(['d'])
    expect(filterChannels(CHANNELS, 'EQUIDIA').map((c) => c.id)).toEqual(['f'])
    expect(normalize('  Équidia ')).toBe('equidia')
  })
  it('cherche des mots dans n’importe quel ordre', () => {
    expect(filterChannels(CHANNELS, 'classique radio').map((c) => c.id)).toEqual(['e'])
    expect(filterChannels(CHANNELS, 'tf')).toHaveLength(1)
    expect(filterChannels(CHANNELS, 'zzz')).toEqual([])
  })
  it('un numéro donne la chaîne exacte d’abord, puis celles dont le numéro commence ainsi', () => {
    expect(filterChannels(CHANNELS, '6').map((c) => c.id)).toEqual(['c', 'd'])
    expect(filterChannels(CHANNELS, '1').map((c) => c.id)).toEqual(['a', 'f'])
    expect(filterChannels(CHANNELS, '99')).toEqual([])
  })
  it('compte les chaînes', () => {
    expect(channelCount(0)).toBe('aucune chaîne')
    expect(channelCount(1)).toBe('1 chaîne')
    expect(channelCount(12)).toBe('12 chaînes')
  })
})

describe('les chemins d’un partage', () => {
  it('coupent, joignent et remontent', () => {
    expect(pathParts('Films/Dessins/')).toEqual(['Films', 'Dessins'])
    expect(pathParts('')).toEqual([])
    expect(joinPath('', 'Films')).toBe('Films')
    expect(joinPath('Films', "Un film d'été.mkv")).toBe("Films/Un film d'été.mkv")
    expect(parentPath('Films/Dessins')).toBe('Films')
    expect(parentPath('Films')).toBe('')
    expect(parentPath('')).toBeNull()
  })
  it('disent où l’on est', () => {
    expect(whereText(null, '')).toBe('Partages')
    expect(whereText('Disque dur', '')).toBe('Disque dur')
    expect(whereText('Disque dur', 'Films/Dessins')).toBe('Disque dur › Films › Dessins')
  })
})

describe('la lecture en cours', () => {
  it('est active quand elle démarre, joue ou est en pause', () => {
    expect(playerActive(player({ state: 'starting' }))).toBe(true)
    expect(playerActive(player({ state: 'paused' }))).toBe(true)
    expect(playerActive(player({ state: 'idle' }))).toBe(false)
    expect(playerActive(player({ state: 'error' }))).toBe(false)
    expect(playerActive(null)).toBe(false)
  })
  it('donne l’avancement d’un fichier, jamais d’un direct', () => {
    expect(progressRatio(player({ position: 2700, length: 5400 }))).toBe(0.5)
    expect(progressRatio(player({ position: 9999, length: 5400 }))).toBe(1)
    expect(progressRatio(player({ live: true, position: null, length: null }))).toBeNull()
    expect(progressRatio(player({ length: null }))).toBeNull()
    expect(progressRatio(null)).toBeNull()
  })
  it('dit où elle en est', () => {
    expect(stateText(player())).toBe('1:05 / 1:30:00')
    expect(stateText(player({ live: true, position: null, length: null }))).toBe('En direct')
    expect(stateText(player({ state: 'paused' }))).toBe('En pause')
    expect(stateText(player({ state: 'starting', live: true }))).toBe('Connexion à la chaîne…')
    expect(stateText(player({ state: 'starting' }))).toBe('Ouverture du fichier…')
    expect(stateText(player({ state: 'error', error: 'La chaîne ne répond pas.' }))).toBe('La chaîne ne répond pas.')
  })
  it('titre la tuile de l’accueil seulement pendant la lecture', () => {
    expect(nowPlayingTitle(player())).toBe('Un film')
    expect(nowPlayingTitle(player({ state: 'idle' }))).toBeNull()
    expect(nowPlayingTitle(null)).toBeNull()
  })
})

describe('ce qui empêche de lire', () => {
  it('ne dit rien quand tout est là (ou d’un dock plus ancien)', () => {
    expect(playerBlocker(player())).toBeNull()
    expect(playerBlocker(null)).toBeNull()
  })
  it('dit ce qui manque', () => {
    expect(playerBlocker(player({ available: false, helper: null }))).toContain('assistant de lecture')
    expect(playerBlocker(player({ helper: { vlc: false, live555: false, ffmpeg: true, touch: true, demo: false } }))).toContain('VLC est absent')
    expect(playerBlocker(player({ helper: { vlc: true, live555: false, ffmpeg: true, touch: false, demo: false } }))).toContain('swayidle')
  })
})

describe('l’échec d’une lecture', () => {
  it('sépare la phrase du détail de VLC', () => {
    expect(splitFailure("Accès refusé au partage : vérifie l'identifiant. Détail : « smb stream error: open failed (Permission denied) ».")).toEqual({
      main: "Accès refusé au partage : vérifie l'identifiant.",
      detail: 'smb stream error: open failed (Permission denied)',
    })
  })
  it('garde un message sans détail tel quel', () => {
    expect(splitFailure('La lecture n’a pas démarré.')).toEqual({ main: 'La lecture n’a pas démarré.', detail: null })
    expect(splitFailure('')).toEqual({ main: '', detail: null })
  })
  it('supporte un détail tronqué par le dock (sans la fin « »)', () => {
    expect(splitFailure('Phrase. Détail : « début de ligne').detail).toBeNull()
  })
  it('ne coupe pas un message où « Détail » apparaît ailleurs', () => {
    expect(splitFailure('Le détail est dans Détail : le journal').detail).toBeNull()
  })
})
