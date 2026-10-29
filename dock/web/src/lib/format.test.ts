import { describe, expect, it } from 'vitest'
import {
  formatCountdown,
  formatDate,
  formatDuration,
  formatMs,
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
