import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SLEEP_OPTIONS, sleepPhrase } from './screen'
import { FAILSAFE_MS, screenSleep, SETTLE_MS } from './screen.svelte'

describe('les durées proposées', () => {
  it('sont celles que le dock accepte, « jamais » en premier', () => {
    expect(SLEEP_OPTIONS.map((o) => o.id)).toEqual([0, 60, 300, 600, 1800])
    expect(SLEEP_OPTIONS[0].label).toBe('Jamais')
  })

  it('se disent en toutes lettres', () => {
    expect(sleepPhrase(0)).toBe('jamais')
    expect(sleepPhrase(60)).toBe('1 minute')
    expect(sleepPhrase(300)).toBe('5 minutes')
    expect(sleepPhrase(1800)).toBe('30 minutes')
  })
})

describe('la veille de l’écran', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    screenSleep.setDelay(0)
  })
  afterEach(() => {
    screenSleep.setDelay(0)
    vi.useRealTimers()
  })

  it('ne s’éteint jamais quand le réglage est « jamais »', () => {
    vi.advanceTimersByTime(24 * 3_600_000)
    expect(screenSleep.phase).toBe('awake')
  })

  it('s’éteint après la durée choisie sans toucher', () => {
    screenSleep.setDelay(60)
    vi.advanceTimersByTime(59_000)
    expect(screenSleep.phase).toBe('awake')
    vi.advanceTimersByTime(1_500)
    expect(screenSleep.phase).toBe('asleep')
  })

  it('un toucher repousse l’extinction, sans minuteur par toucher', () => {
    screenSleep.setDelay(60)
    vi.advanceTimersByTime(50_000)
    screenSleep.activity()
    vi.advanceTimersByTime(50_000) // 100 s depuis le début, mais 50 s depuis le toucher
    expect(screenSleep.phase).toBe('awake')
    vi.advanceTimersByTime(11_000)
    expect(screenSleep.phase).toBe('asleep')
  })

  it('changer le réglage compte comme un toucher et repart de zéro', () => {
    screenSleep.setDelay(60)
    vi.advanceTimersByTime(55_000)
    screenSleep.setDelay(300)
    vi.advanceTimersByTime(200_000)
    expect(screenSleep.phase).toBe('awake')
    vi.advanceTimersByTime(101_000)
    expect(screenSleep.phase).toBe('asleep')
  })

  it('repasser à « jamais » rallume l’écran', () => {
    screenSleep.setDelay(60)
    vi.advanceTimersByTime(61_000)
    expect(screenSleep.phase).toBe('asleep')
    screenSleep.setDelay(0)
    expect(screenSleep.phase).toBe('awake')
  })

  it('« éteindre maintenant » noircit tout de suite', () => {
    screenSleep.sleepNow()
    expect(screenSleep.phase).toBe('asleep')
  })

  it('le premier toucher réveille, puis le voile garde le reste du geste un instant', () => {
    screenSleep.sleepNow()
    screenSleep.wake()
    expect(screenSleep.phase).toBe('waking') // l'image est déjà revenue, le voile est invisible
    vi.advanceTimersByTime(2_000)
    expect(screenSleep.phase).toBe('waking') // le doigt est encore posé
    screenSleep.released()
    vi.advanceTimersByTime(SETTLE_MS - 50)
    expect(screenSleep.phase).toBe('waking') // le clic synthétique du navigateur arrive ici : avalé
    vi.advanceTimersByTime(100)
    expect(screenSleep.phase).toBe('awake')
  })

  it('un geste qui ne se termine jamais ne bloque pas l’écran', () => {
    screenSleep.sleepNow()
    screenSleep.wake()
    vi.advanceTimersByTime(FAILSAFE_MS + 100)
    expect(screenSleep.phase).toBe('awake')
  })

  it('une fois réveillé, le compte à rebours repart de zéro', () => {
    screenSleep.setDelay(60)
    vi.advanceTimersByTime(61_000)
    screenSleep.wake()
    screenSleep.released()
    vi.advanceTimersByTime(SETTLE_MS + 10)
    expect(screenSleep.phase).toBe('awake')
    vi.advanceTimersByTime(50_000)
    expect(screenSleep.phase).toBe('awake')
    vi.advanceTimersByTime(11_000)
    expect(screenSleep.phase).toBe('asleep')
  })

  it('les toucher ne réveillent rien quand l’écran est déjà éveillé', () => {
    screenSleep.wake()
    screenSleep.released()
    expect(screenSleep.phase).toBe('awake')
  })
})
