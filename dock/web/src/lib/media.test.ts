import { describe, expect, it } from 'vitest'
import {
  ADVANCE_MAX_MS,
  advanceText,
  audioLine,
  cornerText,
  fileSize,
  freetvActive,
  freetvBlocker,
  freetvStateText,
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
  stepAdvance,
  whereText,
} from './media'
import type { PlayerAudio, PlayerSnapshot } from './protocol'

const player = (extra: Partial<PlayerSnapshot> = {}): PlayerSnapshot => ({
  available: true,
  state: 'playing',
  kind: 'file',
  title: 'Un film',
  subtitle: 'Disque dur/Films',
  position: 65,
  length: 5400,
  volume: 70,
  front: 'dock',
  error: null,
  helper: { vlc: true, touch: true, demo: false },
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

describe('la recherche sans accents', () => {
  it('ignore les accents, les majuscules et les espaces autour', () => {
    expect(normalize('  Équidia Télé ')).toBe('equidia tele')
    expect(normalize('')).toBe('')
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
  it('donne l’avancement d’un fichier, tant que sa durée est connue', () => {
    expect(progressRatio(player({ position: 2700, length: 5400 }))).toBe(0.5)
    expect(progressRatio(player({ position: 9999, length: 5400 }))).toBe(1)
    expect(progressRatio(player({ position: null }))).toBeNull()
    expect(progressRatio(player({ length: null }))).toBeNull()
    expect(progressRatio(null)).toBeNull()
  })
  it('dit où elle en est', () => {
    expect(stateText(player())).toBe('1:05 / 1:30:00')
    expect(stateText(player({ state: 'paused' }))).toBe('En pause')
    expect(stateText(player({ state: 'starting' }))).toBe('Ouverture du fichier…')
    expect(stateText(player({ state: 'error', error: 'Le fichier ne répond pas.' }))).toBe('Le fichier ne répond pas.')
  })
  it('titre la tuile de l’accueil seulement pendant la lecture', () => {
    expect(nowPlayingTitle(player())).toBe('Un film')
    expect(nowPlayingTitle(player({ state: 'idle' }))).toBeNull()
    expect(nowPlayingTitle(null)).toBeNull()
  })
  it('dit « En jeu » pendant une partie', () => {
    expect(nowPlayingTitle(player({ engine: 'mame', title: 'Pac-Man' }))).toBe('En jeu : Pac-Man')
    expect(nowPlayingTitle(player({ engine: 'mame', title: 'Pac-Man', state: 'idle' }))).toBeNull()
    expect(nowPlayingTitle(player({ engine: 'vlc' }))).toBe('Un film')
  })
})

describe('ce qui empêche de lire', () => {
  it('ne dit rien quand tout est là (ou d’un dock plus ancien)', () => {
    expect(playerBlocker(player())).toBeNull()
    expect(playerBlocker(null)).toBeNull()
  })
  it('dit ce qui manque', () => {
    expect(playerBlocker(player({ available: false, helper: null }))).toContain('assistant de lecture')
    expect(playerBlocker(player({ helper: { vlc: false, touch: true, demo: false } }))).toContain('VLC est absent')
    expect(playerBlocker(player({ helper: { vlc: true, touch: false, demo: false } }))).toContain('swayidle')
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

describe('le son sur le PC', () => {
  const audio = (state: PlayerAudio['state'], detail = '', mode: PlayerAudio['mode'] = 'pc'): PlayerAudio => ({ mode, state, detail })

  it('dit où sort le son quand il joue', () => {
    expect(audioLine(audio('streaming'))).toEqual({ text: 'Son : sur le PC', level: 'good' })
    expect(audioLine(audio('local', '', 'local'))).toEqual({ text: 'Son : sur le Pi', level: 'dim' })
  })

  it('dit pourquoi on n’entend rien', () => {
    expect(audioLine(audio('offline'))).toEqual({ text: 'PC hors ligne : pas de son', level: 'warn' })
    expect(audioLine(audio('old'))?.level).toBe('warn')
    expect(audioLine(audio('old'))?.text).toContain('trop ancien')
    expect(audioLine(audio('error', 'pacat est introuvable sur le PC.'))).toEqual({ text: 'Pas de son : pacat est introuvable sur le PC.', level: 'warn' })
    expect(audioLine(audio('error'))?.text).toBe('Pas de son')
  })

  it('attend sans alarmer pendant la connexion', () => {
    expect(audioLine(audio('connecting'))?.level).toBe('dim')
    expect(audioLine(audio('idle'))?.level).toBe('dim')
  })

  it('ne dit rien d’un dock plus ancien', () => {
    expect(audioLine(undefined)).toBeNull()
    expect(audioLine(null)).toBeNull()
  })

  it('règle l’avance du son de 50 ms en 50 ms, de 0 à 1 s', () => {
    expect(stepAdvance(150, 1)).toBe(200)
    expect(stepAdvance(150, -1)).toBe(100)
    expect(stepAdvance(0, -1)).toBe(0)
    expect(stepAdvance(ADVANCE_MAX_MS, 1)).toBe(ADVANCE_MAX_MS)
    expect(stepAdvance(130, 1)).toBe(200) // une valeur hors pas est ramenée au pas le plus proche, puis avancée
  })

  it('écrit l’avance lisiblement', () => {
    expect(advanceText(150)).toBe('150 ms')
    expect(advanceText(0)).toBe('0 ms')
    expect(advanceText(1000)).toBe('1,0 s')
  })
})

describe('Free TV', () => {
  it('est actif quand Chromium l’ouvre ou le montre, jamais pour une vidéo ou un jeu', () => {
    expect(freetvActive(player({ engine: 'freetv', kind: 'freetv', title: 'Free TV' }))).toBe(true)
    expect(freetvActive(player({ engine: 'freetv', state: 'starting' }))).toBe(true)
    expect(freetvActive(player({ engine: 'vlc' }))).toBe(false)
    expect(freetvActive(player({ engine: 'mame' }))).toBe(false)
    expect(freetvActive(player({ engine: 'freetv', state: 'idle' }))).toBe(false)
    expect(freetvActive(player({ engine: 'freetv', state: 'error' }))).toBe(false)
    expect(freetvActive(null)).toBe(false)
  })
  it('dit ce qui empêche de l’ouvrir, avec la commande à taper', () => {
    const helper = (extra: Record<string, boolean>) => player({ helper: { vlc: true, touch: true, demo: false, chromium: true, widevine: true, keyboard: true, ...extra } })
    expect(freetvBlocker(helper({}))).toBeNull()
    expect(freetvBlocker(null)).toBeNull() // un dock plus ancien : on essaie, le dock répondra
    expect(freetvBlocker(player({ available: false, helper: null }))).toContain('assistant de lecture')
    expect(freetvBlocker(helper({ chromium: false }))).toContain('sudo apt install chromium')
    expect(freetvBlocker(helper({ widevine: false }))).toContain('sudo apt install libwidevinecdm0')
    expect(freetvBlocker(helper({ keyboard: false }))).toBeNull() // sans clavier à l’écran, Free TV s’ouvre quand même
  })
  it('dit où il en est', () => {
    expect(freetvStateText(player({ state: 'starting' }))).toBe('Ouverture de Free TV…')
    expect(freetvStateText(player({ state: 'playing', front: 'video' }))).toBe('Free TV est à l’écran')
    expect(freetvStateText(player({ state: 'playing', front: 'dock' }))).toBe('Free TV reste ouvert derrière')
  })
  it('nomme le coin du retour au dock', () => {
    expect(cornerText('top-left')).toBe('en haut à gauche')
    expect(cornerText('bottom-right')).toBe('en bas à droite')
    expect(cornerText(undefined)).toBe('en haut à gauche') // un dock plus ancien : le coin par défaut
    expect(cornerText('milieu')).toBe('en haut à gauche')
  })
})
