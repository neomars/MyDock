// État du son pour toute l'interface : page Son et volume de la page Musique.
//
// L'agent publie l'état du son seulement quand il change (volume réglé au clavier du PC, casque
// branché, application qui démarre). Les réglages partent sans attendre de réponse :
//  - un curseur de volume envoie au plus une valeur toutes les 100 ms, toujours la plus récente,
//    et la dernière au relâchement ;
//  - le bouton muet et le choix de la sortie se mettent à jour tout de suite (« en attente »),
//    puis l'agent confirme une centaine de millisecondes plus tard ; un refus annule l'affichage.

import { untrack } from 'svelte'
import { DEFAULT_MAX, deviceById } from './audio'
import type { Ack, AudioData } from './protocol'
import { sock } from './socket.svelte'
import { notify } from './toast.svelte'

export type AudioTarget = 'sink' | 'source' | 'app'

const SEND_GAP_MS = 100
const HOLD_MS = 2_000
const ERROR_GAP_MS = 4_000

interface Lane {
  busy: boolean
  next: number | null
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

class AudioStore {
  data = $derived(sock.topics.audio as AudioData | undefined)
  max = $derived(this.data?.max ?? DEFAULT_MAX)
  /** Réglages envoyés et pas encore confirmés par l'agent : clé -> valeur attendue. */
  private waiting = $state.raw<Record<string, boolean | string>>({})
  /** La sortie et le micro par défaut (le choix en attente compte déjà). */
  output = $derived(deviceById(this.data?.sinks, this.defaultId('sink')))
  input = $derived(deviceById(this.data?.sources, this.defaultId('source')))
  private lanes = new Map<string, Lane>()
  private lastErrorAt = -Infinity

  constructor() {
    // Dès que l'agent confirme un réglage en attente, on lâche l'affichage provisoire.
    $effect.root(() => {
      $effect(() => this.reconcile(this.data))
    })
  }

  get unavailable(): string | null {
    return this.data?.unavailable ?? null
  }

  private defaultId(kind: 'sink' | 'source'): string | null {
    const pending = this.waiting[`default:${kind}`]
    if (typeof pending === 'string') return pending
    return (kind === 'sink' ? this.data?.default_sink : this.data?.default_source) ?? null
  }

  /** Vrai si le son de cet élément est coupé (le réglage en attente compte déjà). */
  muted(kind: AudioTarget, id: string, reported: boolean): boolean {
    const pending = this.waiting[`mute:${kind}:${id}`]
    return typeof pending === 'boolean' ? pending : reported
  }

  // --- réglages en attente ----------------------------------------------------------------

  private hold(key: string, value: boolean | string): void {
    this.waiting = { ...this.waiting, [key]: value }
    setTimeout(() => this.release(key, value), HOLD_MS)
  }

  private release(key: string, value: boolean | string): void {
    if (this.waiting[key] !== value) return
    const { [key]: _gone, ...rest } = this.waiting
    this.waiting = rest
  }

  private reconcile(data: AudioData | undefined): void {
    untrack(() => {
      for (const [key, value] of Object.entries(this.waiting)) {
        const [what, kind, ...rest] = key.split(':')
        const id = rest.join(':')
        let actual: boolean | string | null | undefined
        if (what === 'default') actual = kind === 'sink' ? data?.default_sink : data?.default_source
        else {
          const pool = kind === 'sink' ? data?.sinks : kind === 'source' ? data?.sources : data?.apps
          actual = (pool as { id: string; muted: boolean }[] | undefined)?.find((d) => d.id === id)?.muted
        }
        if (actual === value) this.release(key, value)
      }
    })
  }

  private fail(ack: Ack): void {
    const now = performance.now()
    if (now - this.lastErrorAt < ERROR_GAP_MS) return // un curseur en rafale ne doit pas empiler les messages
    this.lastErrorAt = now
    notify(ack.e?.msg ?? 'Le PC n’a pas répondu.', 'bad', 5_000)
  }

  // --- actions ----------------------------------------------------------------------------

  async setMuted(kind: AudioTarget, id: string, muted: boolean): Promise<void> {
    const key = `mute:${kind}:${id}`
    this.hold(key, muted)
    const ack = await sock.act('audio.set_mute', { kind, id, muted })
    if (!ack.ok) {
      this.release(key, muted)
      this.fail(ack)
    }
  }

  async setDefault(kind: 'sink' | 'source', id: string): Promise<void> {
    const key = `default:${kind}`
    this.hold(key, id)
    const ack = await sock.act('audio.set_default', { kind, id })
    if (!ack.ok) {
      this.release(key, id)
      this.fail(ack)
    }
  }

  /** Règle un volume. Appelée à chaque mouvement du doigt : une requête à la fois, la plus récente d'abord. */
  setVolume(kind: AudioTarget, id: string, percent: number): void {
    const key = `${kind}:${id}`
    let lane = this.lanes.get(key)
    if (!lane) this.lanes.set(key, (lane = { busy: false, next: null }))
    if (lane.busy) {
      lane.next = percent
      return
    }
    void this.drain(kind, id, percent, lane)
  }

  private async drain(kind: AudioTarget, id: string, first: number, lane: Lane): Promise<void> {
    lane.busy = true
    let value: number | null = first
    try {
      while (value !== null) {
        const started = performance.now()
        const ack = await sock.act('audio.set_volume', { kind, id, percent: value }, { timeoutMs: 5_000 })
        if (!ack.ok) this.fail(ack)
        await sleep(Math.max(0, SEND_GAP_MS - (performance.now() - started)))
        value = lane.next
        lane.next = null
      }
    } finally {
      lane.busy = false
    }
  }
}

export const audio = new AudioStore()
