import { describe, expect, it } from 'vitest'
import {
  GPIO_CHOICES,
  PAD_HELP,
  batteryText,
  connectedPads,
  countdown,
  emptyText,
  filterGames,
  foundPads,
  gameActive,
  gameCount,
  gameStateText,
  gpioLabel,
  noPadWarning,
  padSummary,
  padsBlocker,
  pairedPads,
  pairingText,
  retroBlocker,
  scanRatio,
  scanSecondsLeft,
  statusLabel,
  stepGpio,
  uploadAddress,
  uploadResultText,
  uploadSecondsLeft,
} from './retro'
import type { PadDevice, PadsSnapshot, PlayerSnapshot, RetroGame, RetroSnapshot, UploadInfo } from './protocol'

const game = (id: string, title: string, year = '1980', maker = 'Namco'): RetroGame => ({ id, title, year, maker, status: 'good', snap: false })
const GAMES = [
  game('pacman', 'Pac-Man (Midway)', '1980', 'Namco'),
  game('galaga', 'Galaga', '1981', 'Namco'),
  game('dkong', 'Donkey Kong (US set 1)', '1981', 'Nintendo'),
  game('1942', '1942 (Revision B)', '1984', 'Capcom'),
  game('sfa', 'Street Fighter Alpha: Warriors’ Dreams', '1995', 'Capcom'),
]
const pad = (over: Partial<PadDevice> = {}): PadDevice => ({ id: 'AA:BB:CC:DD:EE:01', name: 'Xbox Wireless Controller', paired: true, connected: true, battery: 80, ...over })
const pads = (devices: PadDevice[], over: Partial<PadsSnapshot> = {}): PadsSnapshot => ({
  available: true, reason: null, powered: true, blocked: false, scanning: false, scan_until: null, pairing: null, devices, ...over,
}) // prettier-ignore
const player = (over: Partial<PlayerSnapshot> = {}): PlayerSnapshot => ({
  available: true, state: 'idle', kind: null, title: null, subtitle: null, live: false, position: null, length: null, volume: 70, front: 'dock', error: null, helper: null, ...over,
}) // prettier-ignore
const retro = (over: Partial<RetroSnapshot> = {}): RetroSnapshot => ({ mame: true, roms_dir: '/var/lib/mydock/roms', readable: true, state: 'idle', done: 0, total: 0, error: null, skipped: [], games: GAMES, ...over })

describe('la liste des jeux', () => {
  it('compte', () => {
    expect([0, 1, 2].map(gameCount)).toEqual(['aucun jeu', '1 jeu', '2 jeux'])
  })
  it('cherche sans accents ni majuscules, dans le nom, l’éditeur, l’année et le nom MAME', () => {
    expect(filterGames(GAMES, '').length).toBe(5)
    expect(filterGames(GAMES, 'pac').map((g) => g.id)).toEqual(['pacman'])
    expect(filterGames(GAMES, 'CAPCOM').map((g) => g.id)).toEqual(['1942', 'sfa'])
    expect(filterGames(GAMES, '1981').map((g) => g.id)).toEqual(['galaga', 'dkong'])
    expect(filterGames(GAMES, 'dkong').map((g) => g.id)).toEqual(['dkong'])
    expect(filterGames(GAMES, 'dreams warriors').map((g) => g.id)).toEqual(['sfa'])
    expect(filterGames(GAMES, 'zzz')).toEqual([])
  })
  it('exige tous les mots', () => {
    expect(filterGames(GAMES, 'namco galaga').map((g) => g.id)).toEqual(['galaga'])
    expect(filterGames(GAMES, 'nintendo galaga')).toEqual([])
  })
  it('ne modifie pas la liste d’origine', () => {
    const copy = [...GAMES]
    filterGames(GAMES, 'pac')
    expect(GAMES).toEqual(copy)
  })
  it('dit quand l’émulation est imparfaite', () => {
    expect(statusLabel('good')).toBeNull()
    expect(statusLabel('imperfect')).toBe('émulation imparfaite')
    expect(statusLabel('preliminary')).toBe('en développement')
    expect(statusLabel('')).toBeNull()
  })
  it('dit quoi afficher quand il n’y a rien', () => {
    expect(emptyText(retro(), 'zzz')).toBe('Aucun jeu ne correspond.')
    expect(emptyText(retro({ state: 'scanning' }), '')).toContain('Vérification')
    expect(emptyText(retro({ games: [] }), '')).toContain('/var/lib/mydock/roms')
    expect(emptyText(null, '')).toContain('le dossier des ROMs')
  })
  it('suit l’avancement de la vérification', () => {
    expect(scanRatio(retro())).toBeNull()
    expect(scanRatio(retro({ state: 'scanning', done: 5, total: 20 }))).toBe(0.25)
    expect(scanRatio(retro({ state: 'scanning', done: 30, total: 20 }))).toBe(1)
    expect(scanRatio(retro({ state: 'scanning', done: 0, total: 0 }))).toBeNull()
    expect(scanRatio(null)).toBeNull()
  })
})

describe('les manettes', () => {
  it('résume l’en-tête', () => {
    expect(padSummary(null)).toEqual({ label: 'Aucune manette', detail: '', text: 'Aucune manette', connected: false })
    expect(padSummary(pads([]))).toEqual({ label: 'Aucune manette', detail: '', text: 'Aucune manette', connected: false })
    expect(padSummary(pads([pad({ connected: false })])).connected).toBe(false)
    expect(padSummary(pads([pad()]))).toEqual({
      label: 'Manette connectée',
      detail: 'Xbox Wireless Controller · 80 %',
      text: 'Manette connectée : Xbox Wireless Controller · 80 %',
      connected: true,
    })
    expect(padSummary(pads([pad({ battery: null })])).detail).toBe('Xbox Wireless Controller')
    const two = padSummary(pads([pad(), pad({ id: 'AA:BB:CC:DD:EE:02', name: 'DualSense' })]))
    expect(two.label).toBe('2 manettes connectées')
    expect(two.detail).toBe('Xbox Wireless Controller, DualSense')
  })
  it('écrit la charge', () => {
    expect([80, 0, 100, 99.6, 140, -3, null, undefined, Number.NaN].map(batteryText)).toEqual(['80 %', '0 %', '100 %', '100 %', '100 %', '0 %', '', '', ''])
  })
  it('avertit sans manette, sans bloquer', () => {
    expect(noPadWarning(pads([]))).toContain('Aucune manette connectée')
    expect(noPadWarning(pads([pad({ connected: false })]))).toContain('Manettes')
    expect(noPadWarning(pads([pad()]))).toBeNull()
    expect(noPadWarning(null)).toBeNull() // un dock plus ancien : on ne dit rien
  })
  it('sépare les manettes associées de celles qu’on vient de trouver', () => {
    const list = pads([pad(), pad({ id: 'AA:BB:CC:DD:EE:02', paired: false, connected: false, name: '8BitDo Pro 2' })])
    expect(pairedPads(list).map((d) => d.name)).toEqual(['Xbox Wireless Controller'])
    expect(foundPads(list).map((d) => d.name)).toEqual(['8BitDo Pro 2'])
    expect(connectedPads(list).length).toBe(1)
    expect(connectedPads(null)).toEqual([])
  })
  it('compte les secondes de la recherche', () => {
    expect(scanSecondsLeft(pads([], { scanning: true, scan_until: 31_000 }), 1_000)).toBe(30)
    expect(scanSecondsLeft(pads([], { scanning: true, scan_until: 31_000 }), 30_200)).toBe(1)
    expect(scanSecondsLeft(pads([], { scanning: true, scan_until: 31_000 }), 99_000)).toBe(0)
    expect(scanSecondsLeft(pads([]), 0)).toBe(0)
    expect(scanSecondsLeft(null, 0)).toBe(0)
  })
  it('dit où en est l’association', () => {
    const list = pads([pad({ paired: false, connected: false, name: 'DualSense' })])
    expect(pairingText(null, list)).toBeNull()
    expect(pairingText({ id: 'AA:BB:CC:DD:EE:01', state: 'pairing', message: 'Association en cours…' }, list)).toBe('DualSense : Association en cours…')
    expect(pairingText({ id: 'ZZ', state: 'failed', message: 'Échec.' }, list)).toBe('Échec.')
  })
  it('dit pourquoi on ne peut pas chercher', () => {
    expect(padsBlocker(null)).toBeNull()
    expect(padsBlocker(pads([]))).toBeNull()
    expect(padsBlocker(pads([], { available: false, blocked: true }))).toContain('rfkill unblock')
    expect(padsBlocker(pads([], { available: false, reason: 'BlueZ ne répond pas.' }))).toBe('BlueZ ne répond pas.')
    expect(padsBlocker(pads([], { available: false }))).toContain('adaptateur')
  })
  it('explique l’association pour les marques courantes', () => {
    const names = PAD_HELP.map((h) => h.name).join(' ')
    for (const brand of ['PS4', 'Xbox', '8BitDo', 'Switch']) expect(names).toContain(brand)
    expect(PAD_HELP.every((h) => h.steps.length > 20)).toBe(true)
  })
})

describe('le bouton physique', () => {
  it('connaît la broche du connecteur', () => {
    expect(gpioLabel(17)).toBe('GPIO17 (broche 11)')
    expect(gpioLabel(27)).toBe('GPIO27 (broche 13)')
    expect(gpioLabel(0)).toBe('GPIO0')
  })
  it('propose des GPIO du connecteur, rangés', () => {
    expect(GPIO_CHOICES[0]).toBe(2)
    expect(GPIO_CHOICES).toContain(17)
    expect(GPIO_CHOICES).not.toContain(0)
    expect(GPIO_CHOICES).not.toContain(1)
    expect([...GPIO_CHOICES].sort((a, b) => a - b)).toEqual([...GPIO_CHOICES])
  })
  it('passe d’une broche à l’autre sans sortir de la liste', () => {
    expect(stepGpio(17, 1)).toBe(18)
    expect(stepGpio(17, -1)).toBe(16)
    expect(stepGpio(2, -1)).toBe(2)
    expect(stepGpio(27, 1)).toBe(27)
    expect(stepGpio(99, 1)).toBe(2)
  })
})

describe('le lancement', () => {
  it('reconnaît un jeu en cours', () => {
    expect(gameActive(player({ state: 'playing', engine: 'mame' }))).toBe(true)
    expect(gameActive(player({ state: 'starting', engine: 'mame' }))).toBe(true)
    expect(gameActive(player({ state: 'playing', engine: 'vlc' }))).toBe(false)
    expect(gameActive(player({ state: 'idle', engine: 'mame' }))).toBe(false)
    expect(gameActive(player({ state: 'error', engine: 'mame' }))).toBe(false)
    expect(gameActive(player({ state: 'playing' }))).toBe(false) // un dock plus ancien : une vidéo
    expect(gameActive(null)).toBe(false)
  })
  it('dit pourquoi on ne peut pas jouer', () => {
    expect(retroBlocker(player(), retro())).toBeNull()
    expect(retroBlocker(null, null)).toBeNull()
    expect(retroBlocker(player(), retro({ mame: false }))).toContain('sudo apt install mame')
    expect(retroBlocker(player({ available: false }), retro())).toContain('assistant')
    const helper = { vlc: true, live555: false, ffmpeg: true, touch: true, demo: false, mame: false }
    expect(retroBlocker(player({ helper }), retro())).toContain('sudo apt install mame')
    expect(retroBlocker(player({ helper: { ...helper, mame: true } }), retro())).toBeNull()
  })
  it('écrit l’état de l’écran « en jeu »', () => {
    expect(gameStateText(player({ state: 'starting' }))).toBe('Lancement de MAME…')
    expect(gameStateText(player({ state: 'playing' }))).toBe('En jeu')
  })
})

describe('l’envoi de jeux', () => {
  const open: UploadInfo = { open: true, code: 'ABCD-EFGH', expires_at: 61_000, files: 2, wrong_left: 5, last: null, urls: ['http://192.168.1.50/jeux'] }
  it('écrit un compte à rebours', () => {
    expect([0, 5, 65, 872, -4].map(countdown)).toEqual(['0:00', '0:05', '1:05', '14:32', '0:00'])
  })
  it('compte ce qu’il reste', () => {
    expect(uploadSecondsLeft(open, 1_000)).toBe(60)
    expect(uploadSecondsLeft(open, 99_000)).toBe(0)
    expect(uploadSecondsLeft({ open: false, last: null }, 0)).toBe(0)
    expect(uploadSecondsLeft(null, 0)).toBe(0)
  })
  it('dit comment la dernière session s’est finie', () => {
    expect(uploadResultText(open)).toBeNull()
    expect(uploadResultText({ open: false, last: null })).toBeNull()
    expect(uploadResultText({ open: false, last: { result: 'closed', files: 3, at: 1 } })).toBe('Envoi terminé : 3 jeux reçus.')
    expect(uploadResultText({ open: false, last: { result: 'closed', files: 1, at: 1 } })).toBe('Envoi terminé : 1 jeu reçu.')
    expect(uploadResultText({ open: false, last: { result: 'expired', files: 0, at: 1 } })).toContain('expiré')
    expect(uploadResultText({ open: false, last: { result: 'locked', files: 0, at: 1 } })).toContain('annulé')
  })
  it('donne l’adresse de la page', () => {
    expect(uploadAddress(open, 'mydock')).toBe('http://192.168.1.50/jeux')
    expect(uploadAddress({ ...open, urls: [] }, 'mydock')).toBe('http://mydock.local/jeux')
    expect(uploadAddress({ open: false, last: null }, 'mydock')).toBe('http://mydock.local/jeux')
    expect(uploadAddress(null, undefined)).toBe('http://adresse-du-pi/jeux')
  })
})
