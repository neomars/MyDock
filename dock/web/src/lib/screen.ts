// Veille de l'écran du dock : les durées proposées dans les Réglages.

export interface SleepOption {
  /** Secondes d'inactivité avant l'extinction ; 0 : jamais. Mêmes valeurs que côté dock (store.py). */
  id: number
  label: string
}

export const SLEEP_OPTIONS: readonly SleepOption[] = [
  { id: 0, label: 'Jamais' },
  { id: 60, label: '1 min' },
  { id: 300, label: '5 min' },
  { id: 600, label: '10 min' },
  { id: 1800, label: '30 min' },
]

/** « jamais », « 1 minute », « 5 minutes » : pour les phrases. */
export function sleepPhrase(seconds: number): string {
  if (seconds <= 0) return 'jamais'
  const minutes = Math.round(seconds / 60)
  return `${minutes} minute${minutes > 1 ? 's' : ''}`
}
