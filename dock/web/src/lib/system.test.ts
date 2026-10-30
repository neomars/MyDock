import { describe, expect, it } from 'vitest'
import type { GpuInfo } from './protocol'
import { chartRuns, diskLabel, HISTORY_SECONDS, niceMax, shortGpuName, summarizeGpus, toneFor } from './system'

const gpu = (extra: Partial<GpuInfo>): GpuInfo => ({
  id: '0000:00:02.0',
  vendor: 'intel',
  name: 'Intel UHD Graphics 770',
  state: 'active',
  util: null,
  mem_used: null,
  mem_total: null,
  temp_c: null,
  power_w: null,
  freq_mhz: null,
  ...extra,
})

const nvidia = (extra: Partial<GpuInfo> = {}): GpuInfo =>
  gpu({ id: '0000:01:00.0', vendor: 'nvidia', name: 'NVIDIA RTX 4000 Ada Generation Laptop GPU', ...extra })

describe('shortGpuName', () => {
  it.each([
    ['NVIDIA RTX 4000 Ada Generation Laptop GPU', 'RTX 4000 Ada'],
    ['Intel UHD Graphics 770', 'UHD 770'],
    ['AMD Radeon RX 7900 XT', 'Radeon RX 7900 XT'],
    ['Carte inconnue', 'Carte inconnue'],
  ])('%s -> %s', (name, expected) => {
    expect(shortGpuName(name)).toBe(expected)
  })
})

describe('summarizeGpus', () => {
  it('portable hybride avec la NVIDIA qui dort : on le dit, et on montre l’Intel', () => {
    const summary = summarizeGpus([gpu({ freq_mhz: 450 }), nvidia({ state: 'suspended' })])!
    expect(summary.asleep).toBe(true)
    expect(summary.value).toBe('En veille')
    expect(summary.sub).toBe('RTX 4000 Ada dort')
  })

  it('une carte qui travaille montre sa charge et sa température', () => {
    const summary = summarizeGpus([gpu({ freq_mhz: 300 }), nvidia({ util: 35, temp_c: 58 })])!
    expect(summary.asleep).toBe(false)
    expect(summary.value.replace(/ /g, ' ')).toBe('35 %')
    expect(summary.sub.replace(/ /g, ' ')).toBe('RTX 4000 Ada · 58 °C')
  })

  it('sans carte dédiée : la fréquence de l’Intel', () => {
    const summary = summarizeGpus([gpu({ freq_mhz: 1100 })])!
    expect(summary.value.replace(/ /g, ' ')).toBe('1,1 GHz')
  })

  it('une charge de 0 % est une vraie mesure, pas une absence', () => {
    expect(summarizeGpus([nvidia({ util: 0 })])!.value.replace(/ /g, ' ')).toBe('0 %')
  })

  it('NVML absente : la tuile garde le nom et explique', () => {
    const summary = summarizeGpus([nvidia({ note: 'Bibliothèque NVML absente' })])!
    expect(summary.value).toBe('—')
    expect(summary.sub).toBe('Bibliothèque NVML absente')
  })

  it('aucune carte : rien à afficher', () => {
    expect(summarizeGpus([])).toBeNull()
  })
})

describe('diskLabel et toneFor', () => {
  it.each([
    ['/', 'Système'],
    ['/home/mars/Jeux', 'Jeux'],
    ['/media/mars/CLE USB', 'CLE USB'],
    ['/mnt/nas/', 'nas'],
  ])('%s -> %s', (mount, expected) => {
    expect(diskLabel({ mount })).toBe(expected)
  })

  it.each([
    [0, 'good'],
    [69.9, 'good'],
    [70, 'warn'],
    [89.9, 'warn'],
    [90, 'bad'],
    [100, 'bad'],
  ])('%d %% -> %s', (percent, tone) => {
    expect(toneFor(percent)).toBe(tone)
  })
})

describe('chartRuns', () => {
  it('aligne les valeurs à droite : la plus récente touche le bord droit', () => {
    const [run] = chartRuns([0, 50, 100])
    expect(run.points).toBe(`${HISTORY_SECONDS - 3},100 ${HISTORY_SECONDS - 2},50 ${HISTORY_SECONDS - 1},0`)
  })

  it('une série pleine couvre toute la largeur', () => {
    const [run] = chartRuns(Array.from({ length: HISTORY_SECONDS }, () => 10))
    const xs = run.points.split(' ').map((p) => Number(p.split(',')[0]))
    expect(xs[0]).toBe(0)
    expect(xs[xs.length - 1]).toBe(HISTORY_SECONDS - 1)
  })

  it('une mesure absente coupe la courbe (carte endormie ≠ 0 %)', () => {
    const runs = chartRuns([10, 20, null, null, 30])
    expect(runs).toHaveLength(2)
    expect(runs[0].points.split(' ')).toHaveLength(2)
    expect(runs[1].points.split(' ')).toHaveLength(1)
  })

  it('borne les valeurs hors échelle', () => {
    const [run] = chartRuns([-20, 250])
    expect(run.points.split(' ').map((p) => p.split(',')[1])).toEqual(['100', '0'])
  })

  it('la surface se ferme sur l’axe du bas', () => {
    const [run] = chartRuns([40, 60])
    expect(run.area.startsWith(`${HISTORY_SECONDS - 2},100 `)).toBe(true)
    expect(run.area.endsWith(`${HISTORY_SECONDS - 1},100`)).toBe(true)
  })

  it('série vide ou entièrement absente : aucun tracé', () => {
    expect(chartRuns([])).toEqual([])
    expect(chartRuns([null, null])).toEqual([])
  })

  it('utilise l’échelle donnée (débits)', () => {
    const [run] = chartRuns([500_000], 1_000_000)
    expect(run.points.endsWith(',50')).toBe(true)
  })
})

describe('niceMax', () => {
  it.each([
    [[0, 10], 100_000],
    [[250_000], 300_000],
    [[1_200_000, null, 800_000], 2_000_000],
    [[9_000_000], 9_000_000],
    [[], 100_000],
  ])('%j -> %d', (values, expected) => {
    expect(niceMax(values as (number | null)[])).toBe(expected)
  })
})
