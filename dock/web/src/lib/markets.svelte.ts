// État de l'interface de la Bourse : fenêtre de modification, valeur ouverte en détail, historiques demandés.

import type { MarketHistory, MarketRange } from './protocol'
import { sock } from './socket.svelte'

class MarketsUi {
  /** La fenêtre de modification de la liste est ouverte. */
  editor = $state(false)
  /** Identifiant de la valeur ouverte en détail (null : la liste). */
  selected = $state<string | null>(null)

  edit(): void {
    this.editor = true
  }

  done(): void {
    this.editor = false
  }

  open(id: string): void {
    this.selected = id
  }

  close(): void {
    this.selected = null
  }
}

export const marketsUi = new MarketsUi()

/** Heure courante en secondes, rafraîchie toutes les 5 s tant que la page l'utilise (âge des cotations). */
class Ticker {
  now = $state(Date.now() / 1_000)

  /** À appeler à l'ouverture de la page ; retourne l'arrêt. */
  start(): () => void {
    this.now = Date.now() / 1_000
    const timer = setInterval(() => (this.now = Date.now() / 1_000), 5_000)
    return () => clearInterval(timer)
  }
}

export const ticker = new Ticker()

export interface HistoryEntry {
  state: 'loading' | 'ready' | 'error'
  /** Le dernier historique reçu (gardé pendant un rechargement : le graphique ne clignote pas). */
  data?: MarketHistory
  message?: string
  at: number
}

const FRESH_MS = 30_000
const MAX_ENTRIES = 24

/** Les historiques demandés au dock, gardés 30 s : changer de période puis revenir ne redemande rien. */
class HistoryStore {
  entries = $state.raw<Record<string, HistoryEntry>>({})

  key(id: string, range: MarketRange): string {
    return `${id}|${range}`
  }

  get(id: string, range: MarketRange): HistoryEntry | undefined {
    return this.entries[this.key(id, range)]
  }

  async load(id: string, range: MarketRange, force = false): Promise<void> {
    const key = this.key(id, range)
    const known = this.entries[key]
    if (!force && known) {
      if (known.state === 'loading') return
      if (known.state === 'ready' && Date.now() - known.at < FRESH_MS) return
    }
    this.put(key, { state: 'loading', data: known?.data, at: Date.now() })
    const ack = await sock.act('dock.markets.history', { id, range }, { timeoutMs: 45_000 })
    if (ack.ok && Array.isArray((ack.r as MarketHistory | undefined)?.points)) {
      this.put(key, { state: 'ready', data: ack.r as MarketHistory, at: Date.now() })
    } else {
      this.put(key, {
        state: 'error',
        data: known?.data,
        message: (ack.ok ? undefined : ack.e?.msg) ?? 'Historique indisponible.',
        at: Date.now(),
      })
    }
  }

  private put(key: string, entry: HistoryEntry): void {
    const next = { ...this.entries, [key]: entry }
    const keys = Object.keys(next)
    if (keys.length > MAX_ENTRIES) delete next[keys[0]]
    this.entries = next
  }
}

export const history = new HistoryStore()
