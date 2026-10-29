// Reconnexion du navigateur au serveur dock (local) : rapide au début, jamais au-delà de 4 s.

const STEPS_MS = [300, 600, 1_200, 2_500, 4_000]

export function reconnectDelay(attempt: number): number {
  return STEPS_MS[Math.min(Math.max(attempt, 1), STEPS_MS.length) - 1]
}
