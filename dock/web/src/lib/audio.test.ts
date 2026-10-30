import { describe, expect, it } from 'vitest'
import {
  appHue,
  appInitial,
  appNames,
  clampVolume,
  deviceById,
  deviceIcon,
  formatVolume,
  keyVolume,
  ratioToVolume,
  recordingSummary,
  speakerIcon,
  volumeToRatio,
} from './audio'
import type { AudioApp, AudioDevice } from './protocol'

const nb = (text: string) => text.replaceAll(' ', ' ')

const app = (id: string, name: string): AudioApp => ({ id, name, volume: 50, muted: false, playing: true, sink: null })
const device = (id: string): AudioDevice => ({ id, label: id, detail: null, kind: 'speakers', volume: 50, muted: false })

describe('volume', () => {
  it('reste un entier entre 0 et le maximum', () => {
    expect(clampVolume(42.6)).toBe(43)
    expect(clampVolume(-5)).toBe(0)
    expect(clampVolume(250)).toBe(100)
    expect(clampVolume(Number.NaN)).toBe(0)
    expect(clampVolume(Number.POSITIVE_INFINITY)).toBe(0)
    expect(clampVolume(150, 150)).toBe(150)
  })

  it('passe de la position du doigt au volume et inversement', () => {
    expect(ratioToVolume(0)).toBe(0)
    expect(ratioToVolume(0.5)).toBe(50)
    expect(ratioToVolume(0.337)).toBe(34)
    expect(ratioToVolume(1.4)).toBe(100)
    expect(ratioToVolume(-0.2)).toBe(0)
    expect(volumeToRatio(25)).toBe(0.25)
    expect(volumeToRatio(120)).toBe(1) // amplifié ailleurs : le curseur reste en bout de course
    expect(volumeToRatio(10, 0)).toBe(0)
  })

  it('se règle aussi au clavier : flèches ±5, pages ±10, début et fin', () => {
    expect(keyVolume('ArrowRight', 50)).toBe(55)
    expect(keyVolume('ArrowUp', 98)).toBe(100)
    expect(keyVolume('ArrowLeft', 3)).toBe(0)
    expect(keyVolume('ArrowDown', 50)).toBe(45)
    expect(keyVolume('PageUp', 50)).toBe(60)
    expect(keyVolume('PageDown', 5)).toBe(0)
    expect(keyVolume('Home', 50)).toBe(0)
    expect(keyVolume('End', 50)).toBe(100)
    expect(keyVolume('a', 50)).toBeNull()
  })

  it('s’écrit à la française', () => {
    expect(formatVolume(54, false)).toBe(nb('54 %'))
    expect(formatVolume(54, true)).toBe('Muet')
    expect(formatVolume(0, false)).toBe(nb('0 %'))
    expect(formatVolume(null, false)).toBe('—')
    expect(formatVolume(120, false)).toBe(nb('120 %'))
  })
})

describe('périphériques', () => {
  it('retrouvent le périphérique par défaut', () => {
    const list = [device('a'), device('b')]
    expect(deviceById(list, 'b')?.id).toBe('b')
    expect(deviceById(list, 'zzz')).toBeNull()
    expect(deviceById(list, null)).toBeNull()
    expect(deviceById(undefined, 'a')).toBeNull()
  })

  it('ont une icône par famille, et une de secours', () => {
    const kinds = ['speakers', 'headphones', 'bluetooth', 'hdmi', 'usb', 'microphone', 'line', 'other'] as const
    const icons = new Set(kinds.map((k) => deviceIcon(k)))
    expect(icons.size).toBe(kinds.length) // une icône distincte par famille
    expect(deviceIcon('inconnu' as never)).toBe(deviceIcon('other'))
  })

  it('montrent un haut-parleur barré quand le son est coupé ou à zéro', () => {
    expect(speakerIcon(50, true)).toBe(speakerIcon(0, false))
    expect(speakerIcon(50, false)).not.toBe(speakerIcon(0, false))
    expect(speakerIcon(10, false)).not.toBe(speakerIcon(80, false))
    expect(speakerIcon(null, false)).toBe(speakerIcon(80, false))
  })
})

describe('applications', () => {
  it('numérotent les homonymes, pas les autres', () => {
    const names = appNames([app('1', 'Brave'), app('2', 'Spotify'), app('3', 'Brave')])
    expect(names).toEqual({ '1': 'Brave 1', '2': 'Spotify', '3': 'Brave 2' })
  })

  it('ont une initiale et une couleur stables', () => {
    expect(appInitial('spotify')).toBe('S')
    expect(appInitial('  émile')).toBe('É')
    expect(appInitial('')).toBe('?')
    expect(appHue('Spotify')).toBe(appHue('spotify'))
    expect(appHue('Spotify')).not.toBe(appHue('Discord'))
    for (const name of ['', 'a', 'Discord', 'x'.repeat(500)]) {
      expect(appHue(name)).toBeGreaterThanOrEqual(0)
      expect(appHue(name)).toBeLessThan(360)
    }
  })
})

describe('micro en cours d’utilisation', () => {
  const rec = (name: string) => ({ id: name, name, source: 'micro' })
  it('nomme les applications qui écoutent', () => {
    expect(recordingSummary([])).toBe('')
    expect(recordingSummary([rec('Discord')])).toBe('Discord')
    expect(recordingSummary([rec('Discord'), rec('Discord')])).toBe('Discord') // deux flux, une application
    expect(recordingSummary([rec('Discord'), rec('Brave')])).toBe('Discord et Brave')
    expect(recordingSummary([rec('A'), rec('B'), rec('C')])).toBe('A, B et 1\u00a0autre')
    expect(recordingSummary([rec('A'), rec('B'), rec('C'), rec('D')])).toBe('A, B et 2\u00a0autres')
  })
})
