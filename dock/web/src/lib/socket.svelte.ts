// Client WebSocket du navigateur vers le serveur dock (127.0.0.1) : l'unique source d'état de l'interface.
//
//  - les thèmes locaux (link, dock, ui) arrivent d'office ;
//  - les thèmes du PC (host, system, music…) arrivent après abonnement, avec comptage des abonnés ;
//  - les actions renvoient une promesse résolue par l'« ack » ; les actions sensibles ouvrent
//    d'elles-mêmes la fenêtre de confirmation (voir call()).

import { reconnectDelay } from './backoff'
import { askConfirmation } from './confirm.svelte'
import { latencyStats, type LatencyStats } from './format'
import type { Ack, DockInfo, HostData, LinkStatus, UiState } from './protocol'

type ConnectionStatus = 'connecting' | 'open' | 'closed'
const LOCAL_TOPICS = new Set(['link', 'dock', 'ui'])
const ACT_TIMEOUT_MS = 10_000

function failure(id: string, code: string, msg: string): Ack {
  return { t: 'ack', id, ok: false, e: { code, msg } }
}

class DockSocket {
  /** État de la connexion navigateur -> dock (pas celle vers le PC). */
  status = $state<ConnectionStatus>('connecting')
  link = $state<LinkStatus | null>(null)
  /** Dernier diagnostic d'échec : l'écran hors ligne le garde pendant les nouvelles tentatives. */
  problem = $state<LinkStatus | null>(null)
  dock = $state<DockInfo | null>(null)
  ui = $state<UiState | null>(null)
  /** Derniers instantanés des thèmes du PC, par nom de thème. */
  topics = $state.raw<Record<string, unknown>>({})
  /** Dernier aller-retour mesuré avec le dock (ms), pour l'affichage de débogage. */
  lastPingMs = $state<number | null>(null)

  private ws: WebSocket | null = null
  private attempt = 0
  private seq = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined
  private wanted = new Map<string, number>()
  private pending = new Map<string, { resolve: (ack: Ack) => void; timer: ReturnType<typeof setTimeout> }>()
  private pingTimer: ReturnType<typeof setInterval> | undefined
  private pingSentAt = 0

  get connected(): boolean {
    return this.link?.state === 'connected'
  }

  get host(): HostData | null {
    return (this.topics.host as HostData | undefined) ?? null
  }

  // --- connexion ---------------------------------------------------------------------------

  connect(): void {
    clearTimeout(this.reconnectTimer)
    this.status = 'connecting'
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${location.host}/ws`)
    this.ws = ws

    ws.onopen = () => {
      this.attempt = 0
      this.status = 'open'
      const topics = [...this.wanted.keys()]
      if (topics.length) ws.send(JSON.stringify({ t: 'sub', topics }))
      this.pingTimer = setInterval(() => this.ping(), 15_000)
    }
    ws.onmessage = (event) => this.onMessage(event.data)
    ws.onclose = () => {
      if (this.ws !== ws) return
      this.ws = null
      clearInterval(this.pingTimer)
      this.status = 'closed'
      for (const [id, entry] of this.pending) {
        clearTimeout(entry.timer)
        entry.resolve(failure(id, 'dock_offline', 'Connexion au dock perdue.'))
      }
      this.pending.clear()
      this.attempt += 1
      this.reconnectTimer = setTimeout(() => this.connect(), reconnectDelay(this.attempt))
    }
    ws.onerror = () => ws.close()
  }

  private onMessage(raw: unknown): void {
    if (typeof raw !== 'string') return
    let message: { t?: string; [key: string]: unknown }
    try {
      message = JSON.parse(raw)
    } catch {
      return
    }
    switch (message.t) {
      case 'snap':
        this.onSnapshot(message.topic as string, message.data)
        break
      case 'ack': {
        const entry = this.pending.get(message.id as string)
        if (entry) {
          clearTimeout(entry.timer)
          this.pending.delete(message.id as string)
          entry.resolve(message as unknown as Ack)
        }
        break
      }
      case 'pong':
        this.lastPingMs = performance.now() - this.pingSentAt
        break
    }
  }

  private onSnapshot(topic: string, data: unknown): void {
    if (topic === 'link') {
      const next = data as LinkStatus
      if (next.state === 'connected') this.problem = null
      else if (next.state !== 'connecting') this.problem = next
      this.link = next
    } else if (topic === 'dock') this.dock = data as DockInfo
    else if (topic === 'ui') this.ui = data as UiState
    else this.topics = { ...this.topics, [topic]: data }
  }

  private ping(): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return
    this.pingSentAt = performance.now()
    this.ws.send(JSON.stringify({ t: 'ping', n: ++this.seq }))
  }

  // --- abonnements -------------------------------------------------------------------------

  /** S'abonne à des thèmes du PC ; retourne la fonction de désabonnement. */
  subscribe(topics: string[]): () => void {
    const fresh: string[] = []
    for (const topic of topics) {
      if (LOCAL_TOPICS.has(topic)) continue
      const count = this.wanted.get(topic) ?? 0
      this.wanted.set(topic, count + 1)
      if (count === 0) fresh.push(topic)
    }
    if (fresh.length && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ t: 'sub', topics: fresh }))
    }
    let released = false
    return () => {
      if (released) return
      released = true
      const gone: string[] = []
      for (const topic of topics) {
        const count = this.wanted.get(topic)
        if (count === undefined) continue
        if (count <= 1) {
          this.wanted.delete(topic)
          gone.push(topic)
        } else this.wanted.set(topic, count - 1)
      }
      if (gone.length && this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ t: 'unsub', topics: gone }))
      }
    }
  }

  // --- actions -----------------------------------------------------------------------------

  /** Envoie une action et attend son accusé. Ne lève jamais : une panne donne un ack `ok: false`. */
  act(
    name: string,
    params: Record<string, unknown> = {},
    options: { confirm?: string; timeoutMs?: number } = {},
  ): Promise<Ack> {
    const id = `b${++this.seq}`
    const ws = this.ws
    if (ws?.readyState !== WebSocket.OPEN) {
      return Promise.resolve(failure(id, 'dock_offline', 'Le dock ne répond pas.'))
    }
    const message: Record<string, unknown> = { t: 'act', id, a: name, p: params }
    if (options.confirm) message.confirm = options.confirm
    return new Promise<Ack>((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        resolve(failure(id, 'timeout', 'Pas de réponse.'))
      }, options.timeoutMs ?? ACT_TIMEOUT_MS)
      this.pending.set(id, { resolve, timer })
      ws.send(JSON.stringify(message))
    })
  }

  /** Comme act(), mais gère la confirmation à l'écran des actions sensibles. */
  async call(name: string, params: Record<string, unknown> = {}): Promise<Ack> {
    const first = await this.act(name, params)
    if (first.ok || first.e?.code !== 'confirm_required' || !first.confirm) return first
    const accepted = await askConfirmation(first.confirm.label, first.confirm.ttl)
    if (!accepted) return { ...first, cancelled: true }
    return this.act(name, params, { confirm: first.confirm.token })
  }

  /** Mesure l'aller-retour appui -> accusé : `dock.ping` (dock seul) ou `diag.ping` (jusqu'à l'agent). */
  async measure(action: 'dock.ping' | 'diag.ping', count = 20): Promise<LatencyStats | null> {
    const samples: number[] = []
    for (let i = 0; i < count; i++) {
      const started = performance.now()
      const ack = await this.act(action, {}, { timeoutMs: 3_000 })
      if (!ack.ok) return null
      samples.push(performance.now() - started)
    }
    return latencyStats(samples)
  }
}

export const sock = new DockSocket()
