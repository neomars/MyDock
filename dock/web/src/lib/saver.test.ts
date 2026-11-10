import { describe, expect, it } from 'vitest'
import {
  CONTENT_OPTIONS,
  aerialSummary,
  appleRootNote,
  cleanSourceUrl,
  sourceStatus,
  type AerialSource,
  type AerialStatus,
  SAVER_OPTIONS,
  SCENE_IDS,
  SCENE_TITLES,
  clockText,
  pickNext,
  playable,
  saverNeverShows,
  saverPhrase,
  videoTitle,
  type SaverItem,
  type SaverVideo,
} from './saver'

const video = (name: string): SaverVideo => ({ name, size: 1000, mime: 'video/mp4' })
const seq = (...values: number[]) => {
  let i = 0
  return () => values[i++ % values.length]
}

describe('les réglages proposés', () => {
  it('sont ceux que le dock accepte, « jamais » en premier', () => {
    expect(SAVER_OPTIONS.map((o) => o.id)).toEqual([0, 60, 120, 300, 600, 900, 1800])
    expect(SAVER_OPTIONS[0].label).toBe('Jamais')
    expect(CONTENT_OPTIONS.map((o) => o.id)).toEqual(['all', 'scenes', 'videos'])
  })
  it('chaque scène a un nom lisible', () => {
    for (const id of SCENE_IDS) expect(SCENE_TITLES[id].length).toBeGreaterThan(5)
  })
  it('se disent en toutes lettres', () => {
    expect(saverPhrase(60)).toBe('1 minute')
    expect(saverPhrase(300)).toBe('5 minutes')
    expect(saverPhrase(900)).toBe('15 minutes')
  })
  it('préviennent quand l’écran s’éteint avant l’économiseur', () => {
    expect(saverNeverShows(300, 600)).toBe(false)
    expect(saverNeverShows(600, 300)).toBe(true)
    expect(saverNeverShows(300, 300)).toBe(true)
    expect(saverNeverShows(300, 0)).toBe(false) // l'écran ne s'éteint jamais
    expect(saverNeverShows(0, 300)).toBe(false) // pas d'économiseur : rien à prévenir
  })
})

describe('légendes', () => {
  it('une vidéo se nomme sans son extension', () => {
    expect(videoTitle('Vue du matin.mp4')).toBe('Vue du matin')
    expect(videoTitle('mer_du_nord.webm')).toBe('mer du nord')
    expect(videoTitle('.mp4')).toBe('.mp4')
  })
  it('l’heure et la date sont en français', () => {
    const { time, date } = clockText(new Date(2026, 10, 10, 14, 5))
    expect(time).toMatch(/14[:h]05/)
    expect(date).toBe('mardi 10 novembre')
  })
})

describe('ce qui passe à l’écran', () => {
  const videos = [video('a.mp4'), video('b.mp4')]
  const none = new Set<string>()

  it('les vidéos refusées par le navigateur sont écartées', () => {
    expect(playable(videos, new Set(['a.mp4'])).map((v) => v.name)).toEqual(['b.mp4'])
  })
  it('« scènes » ne montre jamais de vidéo', () => {
    for (let i = 0; i < 30; i++) expect(pickNext('scenes', videos, none, null, () => (i % 10) / 10).kind).toBe('scene')
  })
  it('« mes vidéos » ne montre que des vidéos, et retombe sur les scènes s’il n’y en a pas de lisible', () => {
    for (let i = 0; i < 30; i++) expect(pickNext('videos', videos, none, null, () => (i % 10) / 10).kind).toBe('video')
    expect(pickNext('videos', [], none, null, () => 0.2).kind).toBe('scene')
    expect(pickNext('videos', videos, new Set(['a.mp4', 'b.mp4']), null, () => 0.2).kind).toBe('scene')
  })
  it('« tout » alterne : une vidéo quand le sort le veut, une scène sinon', () => {
    expect(pickNext('all', videos, none, null, seq(0.1, 0)).kind).toBe('video')
    expect(pickNext('all', videos, none, null, seq(0.9, 0)).kind).toBe('scene')
    expect(pickNext('all', [], none, null, seq(0.1, 0)).kind).toBe('scene') // pas de vidéo : que des scènes
  })
  it('ne rejoue jamais deux fois de suite la même chose quand il y a le choix', () => {
    const previous: SaverItem = { kind: 'scene', id: 'mountains' }
    for (let i = 0; i < 50; i++) {
      const item = pickNext('scenes', [], none, previous, () => i / 50)
      expect(item).not.toEqual(previous)
    }
    const only = [video('seule.mp4')]
    expect(pickNext('videos', only, none, { kind: 'video', name: 'seule.mp4' }, () => 0)).toEqual({ kind: 'video', name: 'seule.mp4' })
  })
  it('atteint chaque scène avec un tirage qui parcourt [0, 1[', () => {
    const seen = new Set<string>()
    for (let i = 0; i < 100; i++) {
      const item = pickNext('scenes', [], none, null, () => i / 100)
      if (item.kind === 'scene') seen.add(item.id)
    }
    expect([...seen].sort()).toEqual([...SCENE_IDS].sort())
  })
})

const source = (extra: Partial<AerialSource> = {}): AerialSource => ({ id: 'x', label: 'x.org/a.mp4', url: 'https://x.org/a.mp4', builtin: false, total: 1, done: 0, error: null, ...extra })
const status = (extra: Partial<AerialStatus> = {}): AerialStatus => ({ total: 10, done: 4, active: false, current: null, error: null, enabled: true, sources: [source()], ...extra })

describe('le résumé du téléchargement', () => {
  it('dit combien de films sont prêts, et lequel est en cours', () => {
    expect(aerialSummary(status())).toBe('4 sur 10 prêtes.')
    expect(aerialSummary(status({ active: true, current: 'Vue aérienne nuit 2' }))).toBe('4 sur 10 prêtes · en cours : Vue aérienne nuit 2')
  })
  it('dit pourquoi ça n’avance pas', () => {
    expect(aerialSummary(status({ error: '1 film(s) en échec' }))).toBe('4 sur 10 prêtes · 1 film(s) en échec')
  })
  it('ne répète pas, dans la fenêtre, le détail que la ligne de la source donne déjà', () => {
    const failed = status({ error: '10 film(s) en échec, nouvel essai plus tard : Vue aérienne nuit 5 : Certificat de sylvan.apple.com refusé par le Pi (x).' })
    expect(aerialSummary(failed)).toContain('Certificat de sylvan.apple.com refusé') // Réglages : une seule ligne, tout y est
    expect(aerialSummary(failed, { detail: false })).toBe('4 sur 10 prêtes · 10 film(s) en échec, nouvel essai plus tard.')
    expect(aerialSummary(status({ error: 'Pas assez de place libre sur la carte du Pi.' }), { detail: false })).toBe(
      '4 sur 10 prêtes · Pas assez de place libre sur la carte du Pi..', // un échec sans détail à couper garde son texte
    )
  })
  it('dit quand tout est prêt, au singulier comme au pluriel', () => {
    expect(aerialSummary(status({ total: 1, done: 1 }))).toBe('1 film prêts.')
    expect(aerialSummary(status({ total: 10, done: 10 }))).toBe('10 films prêts.')
  })
  it('dit quand c’est suspendu ou sans aucune source', () => {
    expect(aerialSummary(status({ enabled: false }))).toBe('Téléchargement suspendu · 4 sur 10 prêtes.')
    expect(aerialSummary(status({ sources: [], total: 0, done: 0 }))).toBe('Aucune source de vidéos : ajoute une adresse.')
  })
})

describe('l’état d’une source', () => {
  it('compte les films prêts', () => {
    expect(sourceStatus(source({ total: 10, done: 3 }))).toBe('3 sur 10 prêtes')
    expect(sourceStatus(source({ total: 1, done: 1 }))).toBe('1 film prêts')
    expect(sourceStatus(source({ total: 0, done: 0 }))).toBe('aucun film')
  })
  it('ajoute l’erreur, ou dit qu’une liste n’est pas encore lue', () => {
    expect(sourceStatus(source({ total: 3, done: 2, error: 'absent : introuvable' }))).toBe('2 sur 3 prêtes · absent : introuvable')
    expect(sourceStatus(source({ total: null }))).toBe('Liste à lire…')
    expect(sourceStatus(source({ total: null, error: 'Liste injoignable : timeout.' }))).toBe('Liste injoignable : timeout.')
  })
})

describe('l’autorité d’Apple ajoutée par le dock', () => {
  it('ne dit rien tant qu’elle n’a pas servi', () => {
    expect(appleRootNote(null)).toBeNull()
    expect(appleRootNote(undefined)).toBeNull()
    expect(aerialSummary(status({ apple_root: null }))).toBe('4 sur 10 prêtes.') // elle n’encombre pas le résumé
  })
  it('dit d’où elle vient, pour quel serveur, et montre l’empreinte à comparer', () => {
    const fresh = appleRootNote({ sha256: 'B0B1730E…1001F024', origin: 'apple.com', known: 'yes' })
    expect(fresh).toContain('pour le serveur des films seulement (téléchargée sur apple.com)')
    expect(fresh).toContain('Empreinte B0B1730E…1001F024 (celle de l’autorité d’Apple)')
    expect(appleRootNote({ sha256: 'B0B1730E…1001F024', origin: 'cache', known: 'yes' })).toContain('(gardée par le dock)')
  })
  it('n’affirme rien quand l’empreinte n’est pas celle qu’on connaît : elle invite à la comparer', () => {
    const unknown = appleRootNote({ sha256: '0123ABCD…89EF4567', origin: 'apple.com', known: 'no' })
    expect(unknown).toContain('Empreinte 0123ABCD…89EF4567 (à comparer avec apple.com/certificateauthority)')
    expect(unknown).not.toContain('celle de l’autorité d’Apple')
  })
})

describe('l’adresse saisie au clavier tactile', () => {
  it('garde une adresse https complète, nettoyée des espaces autour', () => {
    expect(cleanSourceUrl('  https://exemple.org/films/mer.mp4 ')).toBe('https://exemple.org/films/mer.mp4')
    expect(cleanSourceUrl('HTTPS://exemple.org/l.txt?a=1&b=2')).toBe('HTTPS://exemple.org/l.txt?a=1&b=2')
  })
  it.each(['', 'https://', 'http://exemple.org/a.mp4', 'exemple.org/a.mp4', 'https://exemple.org/a b.mp4', 'https:///a.mp4', 'https://' + 'a'.repeat(600)])('refuse « %s »', (text) => {
    expect(cleanSourceUrl(text)).toBeNull()
  })
})
