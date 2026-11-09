// L'économiseur d'écran, côté interface : les vidéos que le Pi garde dans son dossier (relues à chaque départ de
// l'économiseur et à chaque ouverture des Réglages) et le choix de ce qui passe ensuite.

import { SCENE_IDS, pickNext, type SaverContent, type SaverItem, type SaverVideo, type SceneId } from './saver'
import { sock } from './socket.svelte'

class SaverStore {
  /** Les vidéos du dossier du Pi (vide s'il n'y en a pas, ou si la liste n'a pas pu être lue). */
  videos = $state.raw<SaverVideo[]>([])
  loaded = $state(false)
  /** Celles que ce navigateur n'a pas su lire (format, codec) : on ne les remontre pas. */
  private bad = new Set<string>()

  content = $derived<SaverContent>(sock.ui?.saver_content ?? 'all')

  /** Combien de vidéos on peut encore montrer. */
  get usable(): number {
    return this.videos.filter((v) => !this.bad.has(v.name)).length
  }

  async refresh(): Promise<void> {
    try {
      const reply = await fetch('/api/screensaver', { cache: 'no-store' })
      if (!reply.ok) throw new Error(String(reply.status))
      const data = (await reply.json()) as { videos?: SaverVideo[] }
      this.videos = Array.isArray(data.videos) ? data.videos : []
    } catch {
      this.videos = []
    }
    this.loaded = true
  }

  markBad(name: string): void {
    this.bad.add(name)
  }

  /** Ce qui passe ensuite. `?scene=ocean` dans l'adresse impose cette scène (pour les essais et les captures). */
  next(previous: SaverItem | null): SaverItem {
    const forced = new URLSearchParams(location.search).get('scene')
    if (forced && (SCENE_IDS as readonly string[]).includes(forced)) return { kind: 'scene', id: forced as SceneId }
    return pickNext(this.content, this.videos, this.bad, previous, Math.random)
  }
}

export const saver = new SaverStore()
