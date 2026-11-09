// Veille de l'écran : après un temps sans toucher l'économiseur d'écran démarre, puis (si on l'a demandé) l'écran passe
// au noir. Le premier toucher ne fait que réveiller : il n'atteint jamais ce qui se trouve dessous (on ne déclenche rien
// en réveillant).
//
//   awake  -> saver    après `saverDelay` sans toucher (ou sur demande)
//   awake / saver -> asleep   après `delay` sans toucher (ou sur demande) : l'économiseur laisse la place au noir
//   saver / asleep -> waking  le doigt touche l'écran : l'image revient aussitôt
//   waking -> awake    un instant après que le doigt s'est levé : le voile, devenu invisible, aura
//                      avalé tout le geste (relâchement, clic synthétique du navigateur)
//
// Aucune interrogation : un seul minuteur, réarmé pour le temps restant quand il sonne trop tôt, et
// chaque toucher ne fait que noter l'heure.

export type ScreenPhase = 'awake' | 'saver' | 'asleep' | 'waking'

/** Le voile reste en place (invisible) ce temps-là après la fin du geste qui réveille. */
export const SETTLE_MS = 450
/** Et au plus ce temps-là si le geste ne se termine jamais (le doigt ne se relâche pas, geste annulé). */
export const FAILSAFE_MS = 4_000

class ScreenSleep {
  phase = $state<ScreenPhase>('awake')

  private delayMs = 0
  private saverMs = 0
  private last = Date.now()
  private timer: ReturnType<typeof setTimeout> | undefined
  private settleTimer: ReturnType<typeof setTimeout> | undefined

  /** Durée choisie, en secondes (0 : jamais). Changer le réglage compte comme un toucher. */
  setDelay(seconds: number): void {
    this.delayMs = Math.max(0, seconds) * 1_000
    this.last = Date.now()
    if (this.delayMs === 0 && this.phase !== 'awake') this.settle()
    this.arm()
  }

  /** Durée de l'économiseur d'écran, en secondes (0 : jamais). Changer le réglage compte comme un toucher. */
  setSaverDelay(seconds: number): void {
    this.saverMs = Math.max(0, seconds) * 1_000
    this.last = Date.now()
    if (this.saverMs === 0 && this.phase === 'saver') this.settle()
    this.arm()
  }

  /** Un toucher, un geste, une touche : l'écran est utilisé. */
  activity(): void {
    if (this.phase === 'awake') this.last = Date.now()
  }

  /** Éteint tout de suite (bouton « Éteindre maintenant »). */
  sleepNow(): void {
    clearTimeout(this.timer)
    this.timer = undefined
    this.phase = 'asleep'
  }

  /** Lance l'économiseur tout de suite (bouton « Essayer maintenant »). */
  saverNow(): void {
    clearTimeout(this.timer)
    this.timer = undefined
    this.phase = 'saver'
    this.arm()
  }

  /** Le doigt touche l'écran noir, ou l'économiseur. */
  wake(): void {
    if (this.phase !== 'asleep' && this.phase !== 'saver') return
    this.phase = 'waking'
    clearTimeout(this.settleTimer)
    this.settleTimer = setTimeout(() => this.settle(), FAILSAFE_MS)
  }

  /** Le doigt se lève (ou le geste est annulé). */
  released(): void {
    if (this.phase !== 'waking') return
    clearTimeout(this.settleTimer)
    this.settleTimer = setTimeout(() => this.settle(), SETTLE_MS)
  }

  private settle(): void {
    clearTimeout(this.settleTimer)
    this.settleTimer = undefined
    this.phase = 'awake'
    this.last = Date.now()
    this.arm()
  }

  private arm(): void {
    clearTimeout(this.timer)
    this.timer = undefined
    if (this.phase !== 'awake' && this.phase !== 'saver') return
    const idle = Date.now() - this.last
    const waits: number[] = []
    if (this.delayMs > 0) waits.push(this.delayMs - idle)
    if (this.phase === 'awake' && this.saverMs > 0) waits.push(this.saverMs - idle)
    if (waits.length > 0) this.timer = setTimeout(this.check, Math.max(0, Math.min(...waits)))
  }

  private check = (): void => {
    this.timer = undefined
    if (this.phase !== 'awake' && this.phase !== 'saver') return
    const idle = Date.now() - this.last
    if (this.delayMs > 0 && idle >= this.delayMs) {
      this.phase = 'asleep' // l'écran noir passe avant l'économiseur
      return
    }
    if (this.phase === 'awake' && this.saverMs > 0 && idle >= this.saverMs) this.phase = 'saver'
    this.arm()
  }
}

export const screenSleep = new ScreenSleep()
