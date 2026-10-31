// Veille de l'écran : après un temps sans toucher, l'écran passe au noir. Le premier toucher ne fait
// que le rallumer : il n'atteint jamais ce qui se trouve dessous (on ne déclenche rien en réveillant).
//
//   awake  -> asleep   après `delay` sans toucher (ou sur demande)
//   asleep -> waking   le doigt touche l'écran noir : l'image revient aussitôt
//   waking -> awake    un instant après que le doigt s'est levé : le voile, devenu invisible, aura
//                      avalé tout le geste (relâchement, clic synthétique du navigateur)
//
// Aucune interrogation : un seul minuteur, réarmé pour le temps restant quand il sonne trop tôt, et
// chaque toucher ne fait que noter l'heure.

export type ScreenPhase = 'awake' | 'asleep' | 'waking'

/** Le voile reste en place (invisible) ce temps-là après la fin du geste qui réveille. */
export const SETTLE_MS = 450
/** Et au plus ce temps-là si le geste ne se termine jamais (le doigt ne se relâche pas, geste annulé). */
export const FAILSAFE_MS = 4_000

class ScreenSleep {
  phase = $state<ScreenPhase>('awake')

  private delayMs = 0
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

  /** Le doigt touche l'écran noir. */
  wake(): void {
    if (this.phase !== 'asleep') return
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
    if (this.delayMs > 0 && this.phase === 'awake') {
      this.timer = setTimeout(this.check, Math.max(0, this.delayMs - (Date.now() - this.last)))
    }
  }

  private check = (): void => {
    this.timer = undefined
    if (this.delayMs <= 0 || this.phase !== 'awake') return
    const idle = Date.now() - this.last
    if (idle >= this.delayMs) this.phase = 'asleep'
    else this.timer = setTimeout(this.check, this.delayMs - idle)
  }
}

export const screenSleep = new ScreenSleep()
