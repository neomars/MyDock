import { describe, expect, it } from 'vitest'
import {
  formatBytes,
  formatCountdown,
  formatDate,
  formatDuration,
  formatFreq,
  formatMs,
  formatPercent,
  formatRate,
  formatTemp,
  formatTrackTime,
  formatUsage,
  formatWatts,
  ifaceKindLabel,
  latencyStats,
  sessionLabel,
} from './format'

describe('formatDuration', () => {
  it.each([
    [0, "moins d'une minute"],
    [59, "moins d'une minute"],
    [60, '1 min'],
    [3_599, '59 min'],
    [3_600, '1 h'],
    [3 * 3_600 + 17 * 60, '3 h 17 min'],
    [3 * 3_600 + 5 * 60, '3 h 05 min'],
    [86_400, '1 j'],
    [2 * 86_400 + 4 * 3_600 + 59, '2 j 4 h'],
    [-5, "moins d'une minute"],
  ])('%i s -> %s', (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected)
  })
})

describe('formatCountdown', () => {
  it.each([
    [0, '0 s'],
    [100, '1 s'],
    [4_000, '4 s'],
    [4_001, '5 s'],
    [59_000, '59 s'],
    [65_000, '1 min 05 s'],
    [-10, '0 s'],
  ])('%i ms -> %s', (ms, expected) => {
    expect(formatCountdown(ms)).toBe(expected)
  })
})

describe('formatDate', () => {
  it('met une majuscule et écrit en français', () => {
    expect(formatDate(new Date(2026, 9, 29))).toBe('Jeudi 29 octobre')
  })
})

describe('latencyStats', () => {
  it('retourne null sans mesure', () => {
    expect(latencyStats([])).toBeNull()
  })

  it('calcule médiane et p95 sans modifier la série', () => {
    const samples = [5, 1, 3, 2, 4]
    expect(latencyStats(samples)).toEqual({ n: 5, min: 1, median: 3, p95: 5, max: 5 })
    expect(samples).toEqual([5, 1, 3, 2, 4])
  })

  it('médiane d’une série paire', () => {
    expect(latencyStats([1, 2, 3, 4])?.median).toBe(2.5)
  })

  it('p95 sur 20 mesures = la 19e', () => {
    const samples = Array.from({ length: 20 }, (_, i) => i + 1)
    expect(latencyStats(samples)?.p95).toBe(19)
  })
})

describe('petits libellés', () => {
  it('formatMs', () => {
    expect(formatMs(0.42)).toBe('0,4 ms')
    expect(formatMs(4.26)).toBe('4,3 ms')
    expect(formatMs(12.4)).toBe('12 ms')
  })
  it('ifaceKindLabel', () => {
    expect(ifaceKindLabel('wifi')).toBe('Wi-Fi')
    expect(ifaceKindLabel('ethernet')).toBe('Ethernet')
  })
  it('sessionLabel', () => {
    expect(sessionLabel('wayland')).toBe('Wayland')
    expect(sessionLabel('x11')).toBe('X11')
    expect(sessionLabel('n_importe_quoi')).toBe('Session inconnue')
  })
})

const nb = (text: string) => text.replace(/ /g, '\u00a0')

describe('formatBytes et formatRate', () => {
  it.each([
    [0, '0 o'],
    [512, '512 o'],
    [1_024, '1 Ko'],
    [12 * 1_024, '12 Ko'],
    [1_536, '1,5 Ko'],
    [612 * 1_048_576, '612 Mo'],
    [1.4 * 1_073_741_824, '1,4 Go'],
    [31.2 * 1_073_741_824, '31,2 Go'],
    [2 * 1_099_511_627_776, '2 To'],
    [-5, '0 o'],
  ])('%d octets -> %s', (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(nb(expected))
  })

  it('formate les débits', () => {
    expect(formatRate(0)).toBe(nb('0 o/s'))
    expect(formatRate(1.8 * 1_048_576)).toBe(nb('1,8 Mo/s'))
    expect(formatRate(120 * 1_024)).toBe(nb('120 Ko/s'))
  })
})

describe('autres mesures', () => {
  it('pourcentage, température, puissance', () => {
    expect(formatPercent(12.4)).toBe(nb('12 %'))
    expect(formatPercent(99.6)).toBe(nb('100 %'))
    expect(formatTemp(54.4)).toBe(nb('54 °C'))
    expect(formatWatts(34.5)).toBe(nb('34,5 W'))
  })

  it.each([
    [900, '900 MHz'],
    [999.6, '1000 MHz'],
    [1_000, '1,0 GHz'],
    [3_200, '3,2 GHz'],
    [4_650, '4,7 GHz'],
  ])('%d MHz -> %s', (mhz, expected) => {
    expect(formatFreq(mhz)).toBe(nb(expected))
  })

  it.each([
    [0, '0:00'],
    [999, '0:00'],
    [5_000, '0:05'],
    [65_000, '1:05'],
    [222_000, '3:42'],
    [3_600_000, '1:00:00'],
    [3_735_000, '1:02:15'],
    [-1, '0:00'],
  ])('%d ms -> %s', (ms, expected) => {
    expect(formatTrackTime(ms)).toBe(expected)
  })
})

describe('formatUsage', () => {
  it.each([
    [11.4 * 1_073_741_824, 31.2 * 1_073_741_824, '11,4 / 31,2 Go'],
    [412 * 1_073_741_824, 1000 * 1_073_741_824, '412 / 1000 Go'],
    [1.2 * 1_099_511_627_776, 2 * 1_099_511_627_776, '1,2 / 2 To'],
    [300 * 1_048_576, 900 * 1_048_576, '300 / 900 Mo'],
    [0, 8 * 1_073_741_824, '0 / 8 Go'],
  ])('%d sur %d -> %s', (used, total, expected) => {
    expect(formatUsage(used, total)).toBe(nb(expected))
  })
})
