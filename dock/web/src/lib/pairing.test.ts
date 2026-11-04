import { describe, expect, it } from 'vitest'
import {
  attemptsNote,
  FRESH_PAIRING_MS,
  isPaired,
  justPaired,
  lastNotice,
  MAX_WRONG,
  portalReady,
  remainingMs,
  shownUrls,
} from './pairing'
import type { DockInfo, PairingInfo, PortalInfo } from './protocol'

const portal = (extra: Partial<PortalInfo> = {}): PortalInfo => ({
  enabled: true,
  port: 80,
  urls: ['http://192.168.1.60', 'http://raspberrypi.local'],
  error: null,
  client: { file: 'mydock-agent_1.1.0_all.deb', version: '1.1.0' },
  ...extra,
})

const open = (extra: Partial<Extract<PairingInfo, { open: true }>> = {}): PairingInfo => ({
  open: true,
  code: 'K7QM-2XPD',
  expires_at: 1_000_000 + 600_000,
  wrong_left: MAX_WRONG,
  last: null,
  ...extra,
})

const dock = (pc: Partial<DockInfo['pc']>): DockInfo =>
  ({ pc: { host: '', port: 8765, token_set: false, name: null, ...pc } }) as DockInfo

describe('isPaired', () => {
  it("demande une adresse ET un jeton : l'un sans l'autre ne connecte à rien", () => {
    expect(isPaired(dock({ host: '192.168.1.3', token_set: true }))).toBe(true)
    expect(isPaired(dock({ host: '192.168.1.3', token_set: false }))).toBe(false)
    expect(isPaired(dock({ host: '', token_set: true }))).toBe(false)
    expect(isPaired(null)).toBe(false)
  })
})

describe('portalReady', () => {
  it('exige un portail activé, qui tourne et sans erreur', () => {
    expect(portalReady(portal())).toBe(true)
    expect(portalReady(portal({ enabled: false }))).toBe(false)
    expect(portalReady(portal({ port: null }))).toBe(false)
    expect(portalReady(portal({ error: 'Le port 80 est déjà utilisé.' }))).toBe(false)
    expect(portalReady(undefined)).toBe(false) // un dock plus ancien n'en parle pas
  })
})

describe('shownUrls', () => {
  const urls = ['http://192.168.1.60', 'http://10.0.0.7', 'http://raspberrypi.local']
  it('met les adresses IP avant le nom .local', () => {
    expect(shownUrls(urls)).toEqual(['http://192.168.1.60', 'http://10.0.0.7', 'http://raspberrypi.local'])
  })
  it('garde toujours le nom .local quand il faut tronquer', () => {
    expect(shownUrls(urls, 2)).toEqual(['http://192.168.1.60', 'http://raspberrypi.local'])
    expect(shownUrls(urls, 1)).toEqual(['http://192.168.1.60'])
  })
  it('reconnaît un port dans le nom .local', () => {
    expect(shownUrls(['http://192.168.1.60:8081', 'http://pi.local:8081'], 2)).toEqual([
      'http://192.168.1.60:8081',
      'http://pi.local:8081',
    ])
  })
  it("ne montre que le nom .local quand le Pi n'a pas d'adresse", () => {
    expect(shownUrls(['http://raspberrypi.local'])).toEqual(['http://raspberrypi.local'])
    expect(shownUrls([])).toEqual([])
  })
})

describe('remainingMs', () => {
  it("compte jusqu'à l'expiration, jamais en négatif", () => {
    expect(remainingMs(open(), 1_000_000)).toBe(600_000)
    expect(remainingMs(open(), 1_000_000 + 599_000)).toBe(1_000)
    expect(remainingMs(open(), 9_999_999)).toBe(0)
  })
  it("n'existe pas sans code", () => {
    expect(remainingMs({ open: false, last: null }, 0)).toBeNull()
    expect(remainingMs(undefined, 0)).toBeNull()
  })
})

describe('attemptsNote', () => {
  it('se tait tant que le code est intact', () => {
    expect(attemptsNote(open({ wrong_left: MAX_WRONG }))).toBeNull()
    expect(attemptsNote({ open: false, last: null })).toBeNull()
  })
  it('compte les essais restants, au singulier comme au pluriel', () => {
    expect(attemptsNote(open({ wrong_left: 3 }))).toBe('3 essais restants sur ce code')
    expect(attemptsNote(open({ wrong_left: 1 }))).toBe('1 essai restant sur ce code')
  })
  it('annonce le nouveau code quand les essais sont épuisés', () => {
    expect(attemptsNote(open({ wrong_left: 0 }))).toContain('nouveau code')
  })
})

describe('lastNotice', () => {
  it('dit ce qui est arrivé au dernier code', () => {
    expect(lastNotice({ result: 'paired', name: 'portable', host: '192.168.1.3', at: 1 })).toEqual({
      tone: 'good',
      text: 'PC « portable » associé.',
    })
    expect(lastNotice({ result: 'expired', at: 1 })?.tone).toBe('warn')
    expect(lastNotice({ result: 'locked', at: 1 })?.tone).toBe('bad')
  })
  it("ne dit rien d'une fermeture voulue", () => {
    expect(lastNotice({ result: 'closed', at: 1 })).toBeNull()
    expect(lastNotice(null)).toBeNull()
  })
})

describe('justPaired', () => {
  const paired = { result: 'paired' as const, name: 'portable', host: '192.168.1.3', at: 5_000_000 }
  it('annonce un PC qui vient de s’associer, une seule fois', () => {
    expect(justPaired(paired, 5_000_000 + 2_000, 0)).toBe("PC « portable » associé : le dock s'y connecte.")
    expect(justPaired(paired, 5_000_000 + 2_000, paired.at)).toBeNull() // déjà annoncé
  })
  it('ne ressort pas une association ancienne après un rechargement de la page', () => {
    expect(justPaired(paired, paired.at + FRESH_PAIRING_MS + 1, 0)).toBeNull()
  })
  it("ignore les autres issues d'une session", () => {
    expect(justPaired({ result: 'expired', at: 5_000_000 }, 5_000_001, 0)).toBeNull()
    expect(justPaired(null, 5_000_001, 0)).toBeNull()
  })
})
