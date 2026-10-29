// Messages brefs affichés en haut de l'écran (confirmation d'envoi, erreur d'une action…).

export type ToastKind = 'info' | 'good' | 'bad'

interface ToastState {
  text: string
  kind: ToastKind
  visible: boolean
  seq: number
}

export const toast = $state<ToastState>({ text: '', kind: 'info', visible: false, seq: 0 })

let hideTimer: ReturnType<typeof setTimeout> | undefined

export function notify(text: string, kind: ToastKind = 'info', durationMs = 4_000): void {
  clearTimeout(hideTimer)
  toast.text = text
  toast.kind = kind
  toast.visible = true
  toast.seq += 1
  hideTimer = setTimeout(() => (toast.visible = false), durationMs)
}
