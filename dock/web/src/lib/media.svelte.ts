// Divertissement : l'état des listes (chaînes, partages, dossier ouvert) et les ordres envoyés au dock.
// Les listes viennent du dock à la demande (la Freebox ou le partage peuvent être lents : on dit « chargement »,
// on garde ce qu'on avait pendant un nouvel essai, et une réponse tardive d'une demande dépassée n'écrase rien).

import { joinPath, parentPath } from './media'
import type { MediaEntry, MediaTestPart, TvChannel } from './protocol'
import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

type Loading = 'idle' | 'loading' | 'ready' | 'error'
const SLOW = { timeoutMs: 28_000 }

class MediaStore {
  channels = $state.raw<TvChannel[]>([])
  tv = $state<Loading>('idle')
  tvError = $state<string | null>(null)
  /** La liste affichée est celle de la dernière lecture réussie (la Freebox ne répond plus). */
  tvWarning = $state<string | null>(null)
  /** Ce que cherche la personne (nom ou numéro) ; gardé quand on change de page. */
  query = $state('')

  shares = $state.raw<string[]>([])
  /** Le partage ouvert (`null` : on est sur la liste des partages) et le dossier dedans. */
  share = $state<string | null>(null)
  path = $state('')
  entries = $state.raw<MediaEntry[]>([])
  files = $state<Loading>('idle')
  filesError = $state<string | null>(null)

  private tvSeq = 0
  private filesSeq = 0

  async loadChannels(refresh = false): Promise<void> {
    const seq = ++this.tvSeq
    this.tv = 'loading'
    const ack = await sock.act('dock.tv.list', { refresh }, SLOW)
    if (seq !== this.tvSeq) return
    if (ack.ok) {
      this.channels = Array.isArray(ack.r?.channels) ? (ack.r.channels as TvChannel[]) : []
      this.tvWarning = (ack.r?.warning as string | null | undefined) ?? null
      this.tvError = null
      this.tv = 'ready'
    } else {
      this.tvError = ack.e?.msg ?? 'La liste des chaînes est introuvable.'
      this.tv = this.channels.length ? 'ready' : 'error' // on garde l'ancienne liste plutôt qu'un écran vide
      if (this.channels.length) this.tvWarning = this.tvError
    }
  }

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

  async playTv(id: string): Promise<boolean> {
    return this.run('dock.player.play', { kind: 'tv', id })
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

  async setHd(on: boolean): Promise<void> {
    const ack = await sock.act('dock.ui.set', { tv_hd: on })
    if (!ack.ok) notify(ack.e?.msg ?? 'Réglage non enregistré.', 'bad', 6_000)
  }

  /** Réglages → Divertissement : enregistre un ou plusieurs champs (un champ absent ne change pas). */
  async saveSettings(patch: { playlist_url?: string; smb_host?: string; smb_user?: string; smb_password?: string }): Promise<boolean> {
    const ack = await sock.act('dock.media.set', patch)
    if (!ack.ok) notify(ack.e?.msg ?? 'Réglage non enregistré.', 'bad', 8_000)
    return ack.ok
  }

  /** Essaie la liste des chaînes, le serveur de fichiers et le lecteur ; dit lequel ne va pas. */
  async test(): Promise<{ tv: MediaTestPart; files: MediaTestPart; player: MediaTestPart } | null> {
    const ack = await sock.act('dock.media.test', {}, { timeoutMs: 50_000 })
    if (!ack.ok) {
      notify(ack.e?.msg ?? 'Le test n’a pas abouti.', 'bad', 6_000)
      return null
    }
    return ack.r
  }
}

export const media = new MediaStore()
