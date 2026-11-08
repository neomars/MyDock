// État de la page Spotify : onglet affiché, recherche, bibliothèque, pages ouvertes, lecture demandée.
//
// Tout vient de l'agent (actions `spotify.*`) : le dock et le navigateur ne parlent jamais à Spotify. Chaque liste se
// charge quand on l'ouvre ; la réponse tardive d'une demande abandonnée est ignorée (numéro de demande ou identité
// de la page ouverte). Les pochettes se demandent une à une, au fil du défilement (voir `spotifyArt`).

import { PictureCache } from './pictures.svelte'
import type { Ack } from './protocol'
import type { LibrarySection, SpotifyLibrary, SpotifySearch, SpotifyStatus, SpotifyView } from './protocol'
import { sock } from './socket.svelte'
import { cleanQuery } from './spotify'
import { notify } from './toast.svelte'

/** Les pochettes des listes : l'agent les télécharge chez Spotify, les réduit et les envoie une par une. */
export const spotifyArt = new PictureCache('spotify.image', (key) => ({ art: key }), 3)

export type Tab = 'player' | 'search' | 'library'

/** Une donnée demandée à l'agent : pas encore, en route, reçue, ou refusée (avec de quoi l'expliquer à l'écran). */
export type Load<T> =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ready'; data: T }
  | { state: 'error'; code: string; message: string }

/** Une page ouverte par-dessus l'onglet courant : un album, une playlist ou un artiste. */
export interface Opened {
  kind: SpotifyView['kind']
  id: string
  name: string
  view: Load<SpotifyView>
}

/** L'agent peut lancer Spotify, attendre qu'il se manifeste, puis jouer : jusqu'à une minute. */
const SLOW_MS = 65_000

function failure<T>(ack: Ack): Load<T> {
  return { state: 'error', code: ack.e?.code ?? 'failed', message: ack.e?.msg ?? 'La demande a échoué.' }
}

class SpotifyStore {
  status = $state.raw<SpotifyStatus | null>(null)
  /** Pourquoi l'état n'a pas pu être lu (agent du PC trop ancien, PC injoignable…). */
  statusError = $state<{ code: string; message: string } | null>(null)
  tab = $state<Tab>('player')
  /** Le texte de la dernière recherche, resté affiché dans le champ. */
  query = $state('')
  search = $state.raw<Load<SpotifySearch>>({ state: 'idle' })
  /** La rubrique ouverte de la bibliothèque ; `null` : la liste des rubriques. */
  section = $state<LibrarySection | null>(null)
  library = $state.raw<Load<SpotifyLibrary>>({ state: 'idle' })
  /** Les pages ouvertes l'une sur l'autre (un album, puis son artiste…). */
  trail = $state.raw<Opened[]>([])
  /** Éléments dont la lecture vient d'être demandée (la ligne dit « Lecture… » en attendant l'accusé). */
  starting = $state.raw<Record<string, true>>({})
  launching = $state(false)

  private seq = { status: 0, search: 0, library: 0 }

  /** La page tout en haut de la pile (l'album ouvert), s'il y en a une. */
  get opened(): Opened | null {
    return this.trail.at(-1) ?? null
  }

  async refreshStatus(): Promise<void> {
    const seq = ++this.seq.status
    const ack = await sock.act('spotify.status', {}, { timeoutMs: SLOW_MS })
    if (seq !== this.seq.status) return
    if (ack.ok && ack.r) {
      this.status = ack.r as SpotifyStatus
      this.statusError = null
    } else {
      const code = ack.e?.code ?? 'failed'
      this.statusError = {
        code,
        message:
          code === 'unknown_action'
            ? 'L’agent installé sur le PC est plus ancien que le dock et ne connaît pas Spotify : mets-le à jour (paquet mydock-agent).'
            : (ack.e?.msg ?? 'Impossible de savoir où en est Spotify.'),
      }
    }
  }

  /** Change d'onglet ; les pages ouvertes se ferment (comme dans Spotify, on repart de la rubrique). */
  go(tab: Tab): void {
    this.tab = tab
    this.trail = []
    if (tab !== 'player' && !this.status?.connected) void this.refreshStatus()
  }

  /** Retour d'un cran : ferme la page ouverte, sinon la rubrique de la bibliothèque. */
  back(): void {
    if (this.trail.length > 0) {
      this.trail = this.trail.slice(0, -1)
    } else if (this.tab === 'library' && this.section !== null) {
      this.closeSection()
    }
  }

  // --- recherche ---

  async runSearch(text: string): Promise<void> {
    const query = cleanQuery(text)
    this.query = query
    this.trail = []
    const seq = ++this.seq.search
    if (!query) {
      this.search = { state: 'idle' }
      return
    }
    this.search = { state: 'loading' }
    const ack = await sock.act('spotify.search', { query }, { timeoutMs: SLOW_MS })
    if (seq !== this.seq.search) return
    this.search = ack.ok && ack.r ? { state: 'ready', data: ack.r as SpotifySearch } : failure(ack)
    if (!ack.ok && ack.e?.code === 'not_connected') void this.refreshStatus()
  }

  // --- bibliothèque ---

  async openSection(section: LibrarySection): Promise<void> {
    this.section = section
    this.trail = []
    const seq = ++this.seq.library
    this.library = { state: 'loading' }
    const ack = await sock.act('spotify.library', { section }, { timeoutMs: SLOW_MS })
    if (seq !== this.seq.library) return
    this.library = ack.ok && ack.r ? { state: 'ready', data: ack.r as SpotifyLibrary } : failure(ack)
    if (!ack.ok && ack.e?.code === 'not_connected') void this.refreshStatus()
  }

  closeSection(): void {
    this.seq.library += 1
    this.section = null
    this.library = { state: 'idle' }
  }

  retryLibrary(): void {
    if (this.section) void this.openSection(this.section)
  }

  // --- pages (album, playlist, artiste) ---

  async open(kind: SpotifyView['kind'], id: string, name: string): Promise<void> {
    const entry: Opened = { kind, id, name, view: { state: 'loading' } }
    this.trail = [...this.trail, entry]
    await this.fill(entry)
  }

  retryOpened(): void {
    const entry = this.opened
    if (!entry) return
    const again: Opened = { ...entry, view: { state: 'loading' } }
    this.trail = [...this.trail.slice(0, -1), again]
    void this.fill(again)
  }

  private async fill(entry: Opened): Promise<void> {
    const ack = await sock.act('spotify.view', { kind: entry.kind, id: entry.id }, { timeoutMs: SLOW_MS })
    const view: Load<SpotifyView> = ack.ok && ack.r ? { state: 'ready', data: ack.r as SpotifyView } : failure(ack)
    // La page a pu être fermée entre-temps : alors rien n'est à mettre à jour.
    this.trail = this.trail.map((e) => (e === entry ? { ...entry, view } : e))
    if (!ack.ok && ack.e?.code === 'not_connected') void this.refreshStatus()
  }

  // --- lecture ---

  isStarting(uri: string): boolean {
    return uri in this.starting
  }

  /** Joue un morceau (dans son album ou sa playlist), un album, une playlist ou un artiste. */
  async play(uri: string, context: string | undefined, label: string): Promise<boolean> {
    if (this.isStarting(uri)) return false // un second appui pendant le démarrage ne relance rien
    this.starting = { ...this.starting, [uri]: true }
    const ack = await sock.act('spotify.play', context ? { uri, context } : { uri }, { timeoutMs: SLOW_MS })
    const { [uri]: _done, ...rest } = this.starting
    this.starting = rest
    if (ack.ok) {
      notify(`Lecture : ${label}`, 'good', 2_500)
    } else {
      notify(ack.e?.msg ?? `Impossible de jouer « ${label} ».`, 'bad', 7_000)
      if (ack.e?.code === 'not_connected') void this.refreshStatus()
    }
    return ack.ok
  }

  /** Ouvre l'application Spotify du PC (le lecteur apparaît dès qu'elle s'est annoncée). */
  async launchApp(): Promise<boolean> {
    if (this.launching) return false
    this.launching = true
    const ack = await sock.act('spotify.launch', {}, { timeoutMs: SLOW_MS })
    this.launching = false
    if (!ack.ok) notify(ack.e?.msg ?? 'Spotify ne s’est pas lancé.', 'bad', 7_000)
    void this.refreshStatus()
    return ack.ok
  }
}

export const spotify = new SpotifyStore()
