// Retrogame : les ordres envoyés au dock (liste des jeux, lancement, manettes Bluetooth, envoi de jeux depuis le PC).
// L'état (liste, manettes) arrive des thèmes « retro » et « pads » : voir socket.svelte.ts.

import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

class RetroStore {
  /** Ce que cherche la personne ; gardé quand on change de page. */
  query = $state('')
  /** Le jeu qu'on est en train de lancer (le temps de la réponse du dock). */
  starting = $state<string | null>(null)

  /** L'écran s'ouvre : le dock relit la liste en tâche de fond si le dossier des ROMs a changé. */
  async open(): Promise<void> {
    await sock.act('dock.retro.list', {})
  }

  async refresh(): Promise<void> {
    const ack = await sock.act('dock.retro.refresh', {})
    if (!ack.ok) notify(ack.e?.msg ?? 'La liste n’a pas pu être relue.', 'bad', 6_000)
  }

  /** Lance un jeu en plein écran. Vrai si le dock l'a accepté. */
  async play(id: string): Promise<boolean> {
    if (this.starting) return false
    this.starting = id
    try {
      const ack = await sock.act('dock.player.play', { kind: 'game', id }, { timeoutMs: 22_000 })
      if (!ack.ok) notify(ack.e?.msg ?? 'Le dock n’a pas répondu.', 'bad', 8_000)
      return ack.ok
    } finally {
      this.starting = null
    }
  }

  /** Quitte le jeu (la même demande que le toucher de l'écran). */
  async quit(): Promise<boolean> {
    const ack = await sock.act('dock.player.stop', {})
    if (!ack.ok) notify(ack.e?.msg ?? 'Le dock n’a pas répondu.', 'bad', 6_000)
    return ack.ok
  }

  async dismiss(): Promise<void> {
    await sock.act('dock.player.dismiss', {})
  }

  // --- les manettes ---------------------------------------------------------------------------------------------------

  async scan(): Promise<boolean> {
    const ack = await sock.act('dock.pads.scan', {}, { timeoutMs: 28_000 })
    if (!ack.ok) notify(ack.e?.msg ?? 'La recherche n’a pas pu démarrer.', 'bad', 8_000)
    return ack.ok
  }

  async stopScan(): Promise<void> {
    await sock.act('dock.pads.stop', {})
  }

  async pair(id: string): Promise<boolean> {
    const ack = await sock.act('dock.pads.pair', { id })
    if (!ack.ok) notify(ack.e?.msg ?? 'L’association n’a pas pu démarrer.', 'bad', 8_000)
    return ack.ok
  }

  async forget(id: string): Promise<boolean> {
    const ack = await sock.act('dock.pads.forget', { id }, { timeoutMs: 22_000 })
    if (!ack.ok) notify(ack.e?.msg ?? 'La manette n’a pas été oubliée.', 'bad', 8_000)
    return ack.ok
  }

  // --- l'envoi de jeux depuis le PC -------------------------------------------------------------------------------------

  async openUpload(): Promise<boolean> {
    const ack = await sock.act('dock.retro.upload.open', {})
    if (!ack.ok) notify(ack.e?.msg ?? 'L’envoi n’a pas pu s’ouvrir.', 'bad', 8_000)
    return ack.ok
  }

  async closeUpload(): Promise<void> {
    await sock.act('dock.retro.upload.close', {})
  }

  /** Réglages → Retrogame : le dossier des ROMs (vide : celui par défaut) et la broche du bouton. */
  async saveSettings(patch: { roms_dir?: string; button_gpio?: number }): Promise<boolean> {
    const ack = await sock.act('dock.retro.set', patch)
    if (!ack.ok) notify(ack.e?.msg ?? 'Réglage non enregistré.', 'bad', 8_000)
    return ack.ok
  }
}

export const retro = new RetroStore()
