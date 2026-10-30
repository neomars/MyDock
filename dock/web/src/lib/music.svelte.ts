// État de la musique pour toute l'interface : mini-lecteur de la barre du haut et page Musique.
//
// L'agent publie l'état des lecteurs seulement quand il change ; la position dans le morceau,
// elle, avance toute seule : on la prolonge ici à partir de l'heure de réception de l'instantané.
// Les pochettes sont demandées une fois par morceau (`music.art`) et gardées en mémoire.

import { activePlayer, positionNow } from './music'
import { pictureUrl } from './pictures.svelte'
import type { MusicData, PlayerInfo } from './protocol'
import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

const RESYNC_MS = 10_000

class MusicStore {
  data = $derived(sock.topics.music as MusicData | undefined)
  active = $derived(activePlayer(this.data))
  stamp = $derived(sock.stamps.music ?? 0)
  /** Dernier relevé frais de la position (action `music.position`) : plus récent que l'instantané. */
  private fresh = $state<{ id: string; positionMs: number | null; at: number } | null>(null)
  /** Pochettes déjà reçues, par identifiant : adresse `data:` prête pour <img>. */
  arts = $state.raw<Record<string, string>>({})
  private asking = new Set<string>()
  private failed = new Set<string>()

  get unavailable(): string | null {
    return this.data?.unavailable ?? null
  }

  /** Position à l'instant `now` (`performance.now()`), la plus fraîche des deux sources. */
  position(player: PlayerInfo, now: number): number | null {
    const fresh = this.fresh
    if (fresh && fresh.id === player.id && fresh.at > this.stamp) {
      return positionNow({ ...player, position_ms: fresh.positionMs }, fresh.at, now)
    }
    return positionNow(player, this.stamp, now)
  }

  /** Recale la position sur le lecteur (un instantané en cache peut dater). */
  async resync(): Promise<void> {
    const ack = await sock.act('music.position', {}, { timeoutMs: 4_000 })
    if (ack.ok && ack.r) this.fresh = { id: ack.r.id, positionMs: ack.r.position_ms, at: performance.now() }
  }

  /** Demande la pochette si on ne l'a pas encore (une seule requête à la fois par pochette). */
  async ensureArt(art: string | null): Promise<void> {
    if (!art || this.arts[art] || this.asking.has(art) || this.failed.has(art)) return
    this.asking.add(art)
    const ack = await sock.act('music.art', { art }, { timeoutMs: 20_000 })
    this.asking.delete(art)
    const url = ack.ok ? pictureUrl(ack.r) : null
    if (url) {
      this.arts = { ...this.arts, [art]: url }
    } else {
      this.failed.add(art) // pas de nouvel essai à chaque instantané : le titre suffit
    }
  }

  private async run(name: string, params: Record<string, unknown> = {}): Promise<boolean> {
    const ack = await sock.act(name, params)
    if (!ack.ok) notify(ack.e?.msg ?? 'Le lecteur n’a pas répondu.', 'bad', 5_000)
    return ack.ok
  }

  playPause = () => this.run('music.play_pause')
  next = () => this.run('music.next')
  previous = () => this.run('music.previous')
  select = (id: string) => this.run('music.select', { player: id })

  async seek(positionMs: number): Promise<void> {
    if (await this.run('music.seek', { position_ms: positionMs })) await this.resync()
  }

  readonly resyncMs = RESYNC_MS
}

export const music = new MusicStore()
