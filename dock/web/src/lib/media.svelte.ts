// Divertissement : l'état des listes (partages, dossier ouvert) et les ordres envoyés au dock.
// Les listes viennent du dock à la demande (le partage peut être lent : on dit « chargement »,
// on garde ce qu'on avait pendant un nouvel essai, et une réponse tardive d'une demande dépassée n'écrase rien).

import { joinPath, parentPath } from './media'
import type { MediaEntry, MediaTestPart } from './protocol'
import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

type Loading = 'idle' | 'loading' | 'ready' | 'error'
const SLOW = { timeoutMs: 28_000 }

class MediaStore {
  shares = $state.raw<string[]>([])
  /** Le partage ouvert (`null` : on est sur la liste des partages) et le dossier dedans. */
  share = $state<string | null>(null)
  path = $state('')
  entries = $state.raw<MediaEntry[]>([])
  files = $state<Loading>('idle')
  filesError = $state<string | null>(null)

  private filesSeq = 0

  async loadShares(): Promise<void> {
    const seq = ++this.filesSeq
    this.share = null
    this.path = ''
    this.files = 'loading'
    const ack = await sock.act('dock.media.shares', {}, SLOW)
    if (seq !== this.filesSeq) return
    if (ack.ok) {
      this.shares = Array.isArray(ack.r?.shares) ? (ack.r.shares as string[]) : []
      this.filesError = null
      this.files = 'ready'
    } else {
      this.shares = []
      this.filesError = ack.e?.msg ?? 'Le serveur de fichiers est injoignable.'
      this.files = 'error'
    }
  }

  /** Ouvre un dossier d'un partage. */
  async open(share: string, path: string): Promise<void> {
    const seq = ++this.filesSeq
    this.files = 'loading'
    const ack = await sock.act('dock.media.browse', { share, path }, SLOW)
    if (seq !== this.filesSeq) return
    if (ack.ok) {
      this.share = (ack.r?.share as string | undefined) ?? share
      this.path = (ack.r?.path as string | undefined) ?? path
      this.entries = Array.isArray(ack.r?.entries) ? (ack.r.entries as MediaEntry[]) : []
      this.filesError = null
      this.files = 'ready'
    } else {
      this.filesError = ack.e?.msg ?? 'Ce dossier est illisible.'
      this.files = 'error'
    }
  }

  async enter(name: string): Promise<void> {
    if (this.share !== null) await this.open(this.share, joinPath(this.path, name))
  }

  /** Remonte d'un dossier, puis à la liste des partages. */
  async up(): Promise<void> {
    if (this.share === null) return
    const parent = parentPath(this.path)
    if (parent === null) await this.loadShares()
    else await this.open(this.share, parent)
  }

  async retryFiles(): Promise<void> {
    if (this.share === null) await this.loadShares()
    else await this.open(this.share, this.path)
  }

  // --- la lecture ---------------------------------------------------------------------------------------------------------

  /** Ouvre Free TV (tv.free.fr, dans Chromium) : le dock ne dit que « ouvre Free TV », jamais une adresse. */
  async openFreeTv(): Promise<boolean> {
    return this.run('dock.player.play', { kind: 'freetv' })
  }

  /** Montre ou cache le clavier à l'écran de Free TV (il s'affiche dans la fenêtre de Free TV, devant). */
  async keyboard(on: boolean): Promise<boolean> {
    return this.run('dock.player.keyboard', { on })
  }

  async playFile(name: string): Promise<boolean> {
    if (this.share === null) return false
    return this.run('dock.player.play', { kind: 'file', share: this.share, path: joinPath(this.path, name) })
  }

  async control(name: 'pause' | 'stop' | 'dismiss'): Promise<boolean> {
    return this.run(`dock.player.${name}`, {})
  }

  async seek(delta: number): Promise<boolean> {
    return this.run('dock.player.seek', { delta })
  }

  async volume(value: number): Promise<boolean> {
    return this.run('dock.player.volume', { value: Math.round(value) })
  }

  async front(to: 'video' | 'dock'): Promise<boolean> {
    return this.run('dock.player.front', { to })
  }

  private async run(name: string, params: Record<string, unknown>): Promise<boolean> {
    const ack = await sock.act(name, params, { timeoutMs: 22_000 })
    if (!ack.ok) notify(ack.e?.msg ?? 'Le dock n’a pas répondu.', 'bad', 8_000)
    return ack.ok
  }

  /** Réglages → Divertissement : enregistre un ou plusieurs champs (un champ absent ne change pas). */
  async saveSettings(patch: { smb_host?: string; smb_user?: string; smb_password?: string }): Promise<boolean> {
    const ack = await sock.act('dock.media.set', patch)
    if (!ack.ok) notify(ack.e?.msg ?? 'Réglage non enregistré.', 'bad', 8_000)
    return ack.ok
  }

  /** Réglages → Divertissement → Son : sur le PC ou sur le Pi, et l'avance du son des vidéos (prise en compte à la lecture suivante). */
  async saveSound(patch: { pc_audio?: boolean; audio_advance_ms?: number }): Promise<boolean> {
    const ack = await sock.act('dock.sound.set', patch)
    if (!ack.ok) notify(ack.e?.msg ?? 'Réglage du son non enregistré.', 'bad', 6_000)
    return ack.ok
  }

  /** Réglages → Divertissement → Free TV : la taille de la page (prise en compte à la prochaine ouverture de Free TV). */
  async saveFreeTvScale(scale: number): Promise<boolean> {
    const ack = await sock.act('dock.freetv.set', { scale })
    if (!ack.ok) notify(ack.e?.msg ?? 'Taille de Free TV non enregistrée.', 'bad', 6_000)
    return ack.ok
  }

  /** Essaie le serveur de fichiers et le lecteur ; dit lequel ne va pas. */
  async test(): Promise<{ files: MediaTestPart; player: MediaTestPart } | null> {
    const ack = await sock.act('dock.media.test', {}, { timeoutMs: 50_000 })
    if (!ack.ok) {
      notify(ack.e?.msg ?? 'Le test n’a pas abouti.', 'bad', 6_000)
      return null
    }
    return ack.r
  }
}

export const media = new MediaStore()
