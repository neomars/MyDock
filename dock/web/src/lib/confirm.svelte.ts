// Fenêtre de confirmation des actions sensibles (tuer un processus, redémarrer, éteindre…).

interface ConfirmState {
  open: boolean
  label: string
  ttlMs: number
  openedAt: number
}

export const confirmState = $state<ConfirmState>({ open: false, label: '', ttlMs: 0, openedAt: 0 })

let resolver: ((accepted: boolean) => void) | null = null
let expiry: ReturnType<typeof setTimeout> | undefined

/** Ouvre la fenêtre ; se résout à `true` si l'utilisateur confirme, `false` s'il annule ou laisse expirer. */
export function askConfirmation(label: string, ttlSeconds: number): Promise<boolean> {
  settle(false) // une seule confirmation à la fois
  confirmState.label = label
  confirmState.ttlMs = ttlSeconds * 1000
  confirmState.openedAt = Date.now()
  confirmState.open = true
  return new Promise<boolean>((resolve) => {
    resolver = resolve
    // Le jeton côté agent expire : inutile de laisser la fenêtre ouverte au-delà.
    expiry = setTimeout(() => settle(false), confirmState.ttlMs)
  })
}

export function settle(accepted: boolean): void {
  clearTimeout(expiry)
  confirmState.open = false
  const resolve = resolver
  resolver = null
  resolve?.(accepted)
}
