// Images demandées au PC une par une (icônes d'application, jaquettes Steam) et gardées en mémoire.
//
// Chaque image se demande par un identifiant que l'agent a lui-même publié : le dock ne donne jamais
// un nom de fichier. Quelques demandes à la fois seulement, pour ne pas étouffer le Pi ni le réseau.

import { sock } from './socket.svelte'

/** Pannes passagères : on redemandera l'image la prochaine fois qu'on la voudra. */
const TRANSIENT = new Set(['dock_offline', 'timeout', 'pc_offline'])

/** Adresse `data:` d'une image reçue de l'agent ; null si la réponse n'en est pas une (jamais d'image cassée). */
export function pictureUrl(reply: unknown): string | null {
  const { mime, data } = (reply ?? {}) as { mime?: unknown; data?: unknown }
  if (typeof mime !== 'string' || typeof data !== 'string' || !/^image\/[a-z0-9.+-]+$/i.test(mime)) return null
  return `data:${mime};base64,${data}`
}

export class PictureCache {
  /** Adresses `data:` prêtes pour <img>, par identifiant. */
  urls = $state.raw<Record<string, string>>({})
  private asked = new Set<string>()
  private queue: string[] = []
  private active = 0

  constructor(
    private readonly action: string,
    private readonly params: (key: string) => Record<string, unknown>,
    private readonly parallel = 4,
  ) {}

  /** Demande l'image si on ne l'a pas (ou si on l'a déjà demandée). */
  want(key: string | null | undefined): void {
    if (!key || key in this.urls || this.asked.has(key)) return
    this.asked.add(key)
    this.queue.push(key)
    this.pump()
  }

  private pump(): void {
    while (this.active < this.parallel && this.queue.length > 0) {
      const key = this.queue.shift()!
      this.active += 1
      void this.fetch(key).finally(() => {
        this.active -= 1
        this.pump()
      })
    }
  }

  private async fetch(key: string): Promise<void> {
    const ack = await sock.act(this.action, this.params(key), { timeoutMs: 20_000 })
    const url = ack.ok ? pictureUrl(ack.r) : null
    if (url) {
      this.urls = { ...this.urls, [key]: url }
    } else if (ack.e && TRANSIENT.has(ack.e.code)) {
      this.asked.delete(key)
    } // sinon (pas d'icône, pas de jaquette) on n'insiste pas : le nom suffit
  }
}

export const appIcons = new PictureCache('apps.icon', (key) => ({ icon: key }))
export const steamCovers = new PictureCache('steam.cover', (key) => ({ appid: Number(key) }), 3)
