// Wake-on-LAN : un seul état partagé par l'écran « PC hors ligne » et la page Alimentation.

import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

export const wake = $state<{ sending: boolean; sentAt: number | null }>({
  sending: false,
  sentAt: null,
})

/** Demande au dock d'envoyer le paquet de réveil ; prévient l'utilisateur du résultat. */
export async function wakePc(): Promise<boolean> {
  if (wake.sending) return false
  wake.sending = true
  const ack = await sock.act('dock.wol.send', {}, { timeoutMs: 12_000 })
  wake.sending = false
  if (ack.ok) {
    wake.sentAt = Date.now()
    notify('Paquet de réveil envoyé.', 'good')
    return true
  }
  notify(ack.e?.msg ?? "Le paquet de réveil n'a pas pu partir.", 'bad', 8_000)
  return false
}

/** Le PC a répondu : le réveil n'est plus « en cours ». */
export function wakeDone(): void {
  wake.sentAt = null
}
