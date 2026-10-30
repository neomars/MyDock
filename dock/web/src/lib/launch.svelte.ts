// Lancements demandés au PC (application, jeu, document) : retour immédiat sur la tuile touchée,
// message si le PC refuse. Le PC ne lance que ce que l'agent a lui-même listé.

import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

const HOLD_MS = 4_000

class LaunchStore {
  /** Éléments dont le lancement vient d'être demandé : leur tuile dit « Lancement… » un instant. */
  busy = $state.raw<Record<string, true>>({})

  isBusy(key: string): boolean {
    return key in this.busy
  }

  private mark(key: string, holdMs: number): void {
    this.busy = { ...this.busy, [key]: true }
    setTimeout(() => this.unmark(key), holdMs)
  }

  private unmark(key: string): void {
    if (!(key in this.busy)) return
    const { [key]: _gone, ...rest } = this.busy
    this.busy = rest
  }

  /** Lance quelque chose ; `key` identifie la tuile à marquer, `label` nomme l'élément dans les messages. */
  async run(
    key: string,
    action: string,
    params: Record<string, unknown>,
    label: string,
    holdMs = HOLD_MS,
  ): Promise<boolean> {
    if (this.isBusy(key)) return false // un second appui pendant le lancement ne lance pas deux fois
    this.mark(key, holdMs)
    const ack = await sock.act(action, params, { timeoutMs: 12_000 })
    if (ack.ok) {
      notify(`« ${ack.r?.name ?? label} » se lance…`, 'info', 2_500)
    } else {
      this.unmark(key)
      notify(ack.e?.msg ?? `Impossible de lancer « ${label} ».`, 'bad', 6_000)
    }
    return ack.ok
  }

  /** Efface le marquage (le jeu est maintenant « en cours », l'application a ouvert sa fenêtre…). */
  done(key: string): void {
    this.unmark(key)
  }
}

export const launch = new LaunchStore()
