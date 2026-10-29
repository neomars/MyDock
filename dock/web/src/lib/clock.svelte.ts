// Horloge : un seul réveil par minute (pas de minuteur à la seconde qui occupe le Pi pour rien).

class MinuteClock {
  now = $state(new Date())

  constructor() {
    this.schedule()
  }

  private schedule(): void {
    const untilNextMinute = 60_000 - (Date.now() % 60_000)
    setTimeout(() => {
      this.now = new Date()
      this.schedule()
    }, untilNextMinute + 50)
  }
}

export const clock = new MinuteClock()
