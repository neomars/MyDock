// Commandes d'alimentation envoyées au PC : un seul ordre à la fois, et le bouton reste « en cours »
// un moment après l'envoi (le PC met quelques secondes à s'éteindre : pas de second ordre par mégarde).

import { controlById, outcome, type PowerControlId } from './power'
import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

const HOLD_MS = { lock: 2_000, cut: 8_000 }

class PowerStore {
  /** La commande en cours (envoyée, ou acceptée depuis moins de quelques secondes). */
  busy = $state<PowerControlId | null>(null)

  async run(id: PowerControlId): Promise<boolean> {
    if (this.busy) return false
    this.busy = id
    const started = Date.now()
    let sent = false
    try {
      const ack = await sock.call(`power.${id}`)
      const result = outcome(id, ack)
      if (result) notify(result.text, result.kind, result.kind === 'bad' ? 7_000 : 4_000)
      sent = ack.ok || (!ack.cancelled && result?.kind === 'info')
      return ack.ok
    } finally {
      const hold = sent ? (controlById(id).cuts ? HOLD_MS.cut : HOLD_MS.lock) : 0
      const wait = Math.max(0, hold - (Date.now() - started))
      setTimeout(() => (this.busy = null), wait)
    }
  }
}

export const power = new PowerStore()
