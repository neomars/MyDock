// Association d'un PC : les deux actions du dock (afficher un code, le retirer), avec leurs messages d'erreur.

import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

class PairingUi {
  /** Une demande est en cours : le bouton se grise le temps de la réponse. */
  busy = $state(false)

  /** Affiche un nouveau code sur l'écran du dock (l'ancien cesse de valoir). */
  async open(): Promise<boolean> {
    return this.run('dock.pairing.open', "Impossible d'afficher un code.")
  }

  /** Retire le code : plus aucun PC ne peut s'associer jusqu'au prochain « Associer un PC ». */
  async close(): Promise<boolean> {
    return this.run('dock.pairing.close', 'Impossible de fermer l’association.')
  }

  private async run(action: string, failure: string): Promise<boolean> {
    if (this.busy) return false
    this.busy = true
    try {
      const ack = await sock.act(action)
      if (!ack.ok) notify(ack.e?.msg ?? failure, 'bad', 8_000)
      return ack.ok
    } finally {
      this.busy = false
    }
  }
}

export const pairingUi = new PairingUi()
