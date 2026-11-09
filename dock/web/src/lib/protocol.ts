// Types du protocole MyDock v1 (voir common/mydock_common/protocol.py).

export const PROTOCOL_VERSION = 1

export type PageId =
  | 'music'
  | 'sound'
  | 'apps'
  | 'steam'
  | 'office'
  | 'markets'
  | 'system'
  | 'power'
  | 'settings'

export type LinkState =
  | 'not_configured'
  | 'connecting'
  | 'connected'
  | 'auth_failed'
  | 'host_up_agent_down'
  | 'unreachable'
  | 'protocol_mismatch'

export interface AgentInfo {
  version: string
  proto?: number
  demo: boolean
}

/** Thème « link » : l'état de la liaison dock -> agent. */
export interface LinkStatus {
  state: LinkState
  detail: string
  since: number // epoch ms
  attempt: number
  next_retry_at: number | null // epoch ms
  resolved: string | null
  agent: AgentInfo | null
  host: string
  port: number
}

export interface Iface {
  name: string
  mac: string
  kind: 'ethernet' | 'wifi'
  up: boolean
  ipv4: string[]
  default: boolean
}

/** Thème « host » (agent) : identité du PC. */
export interface HostData {
  hostname: string
  os: string
  kernel: string
  arch: string
  python: string
  agent: AgentInfo
  boot_time: number // epoch s
  session: { type: string; desktop: string | null }
  interfaces: Iface[]
}

/** Une carte graphique. `state: 'suspended'` : la carte dort, on n'y touche pas (portable hybride). */
export interface GpuInfo {
  id: string
  vendor: 'nvidia' | 'intel' | 'amd' | 'other'
  name: string
  state: 'active' | 'suspended'
  util: number | null
  mem_used: number | null
  mem_total: number | null
  temp_c: number | null
  power_w: number | null
  freq_mhz: number | null
  note?: string
}

export interface DiskInfo {
  mount: string
  device: string
  fs: string
  total: number
  used: number
  percent: number
}

/** Thème « system » (agent) : mesures du PC et historique des 60 dernières secondes. */
export interface SystemData {
  cpu: { percent: number; cores: number[]; freq_mhz: number | null; temp_c: number | null; load: number[] }
  mem: { total: number; used: number; percent: number; swap_total: number; swap_used: number }
  net: { down: number; up: number }
  disk_io: { read: number; write: number }
  disks: DiskInfo[]
  temps: { cpu?: number; disk?: number; gpu?: number }
  battery: { percent: number; plugged: boolean | null; secs_left: number | null } | null
  gpus: GpuInfo[]
  history: Record<'cpu' | 'mem' | 'gpu' | 'down' | 'up', (number | null)[]>
  unavailable?: string
}

export interface ProcInfo {
  pid: number
  name: string
  /** % de la capacité totale du processeur. */
  cpu: number
  /** Octets de mémoire résidente. */
  mem: number
  user: string
  /** Heure de démarrage : l'agent la revérifie avant d'arrêter (un PID peut être réattribué). */
  started: number
  killable: boolean
}

/** Thème « procs » (agent) : les processus les plus gourmands. */
export interface ProcsData {
  count: number
  cores: number | null
  procs: ProcInfo[]
}

export interface PlayerInfo {
  id: string
  name: string
  status: 'playing' | 'paused' | 'stopped'
  title: string | null
  artist: string | null
  album: string | null
  length_ms: number | null
  /** Position au moment de l'instantané ; l'interface la prolonge tant que le morceau joue. */
  position_ms: number | null
  rate: number
  /** Identifiant opaque de la pochette, à demander par l'action `music.art`. */
  art: string | null
  can: { play: boolean; pause: boolean; next: boolean; previous: boolean; seek: boolean }
  /** Lecture aléatoire ; `null` quand le lecteur ne sait pas la régler (le bouton n'est alors pas proposé). */
  shuffle: boolean | null
  /** Répétition (rien, le morceau, la liste) ; `null` quand le lecteur ne sait pas la régler. */
  loop: LoopMode | null
}

export type LoopMode = 'none' | 'track' | 'playlist'

/** Thème « music » (agent) : les lecteurs MPRIS du PC. */
export interface MusicData {
  players: PlayerInfo[]
  active: string | null
  unavailable?: string
}

// --- Spotify : réponses des actions `spotify.*` (voir agent/mydock_agent/spotify) -----------------------------

/** Où en est Spotify sur le PC : installé, lancé, compte connecté. */
export interface SpotifyStatus {
  installed: boolean
  running: boolean
  /** Un jeton est enregistré sur le PC (`mydock-agent spotify login`). */
  configured: boolean
  /** Spotify a répondu avec ce jeton. */
  connected: boolean
  user: string | null
  premium: boolean | null
  message: string | null
}

/** `art` : identifiant opaque de pochette, à demander par l'action `spotify.image` (jamais une adresse web). */
export interface SpotifyTrack {
  kind: 'track'
  id: string
  uri: string
  name: string
  artists: string | null
  album: string | null
  album_id: string | null
  duration_ms: number | null
  number: number | null
  explicit: boolean
  playable: boolean
  art: string | null
}

export interface SpotifyAlbum {
  kind: 'album'
  id: string
  uri: string
  name: string
  artists: string | null
  year: string | null
  type: 'album' | 'single' | 'compilation' | null
  tracks: number | null
  art: string | null
}

export interface SpotifyArtist {
  kind: 'artist'
  id: string
  uri: string
  name: string
  genres: string | null
  art: string | null
}

export interface SpotifyPlaylist {
  kind: 'playlist'
  id: string
  uri: string
  name: string
  owner: string | null
  tracks: number | null
  art: string | null
}

export type SpotifyItem = SpotifyTrack | SpotifyAlbum | SpotifyArtist | SpotifyPlaylist

export interface SpotifySearch {
  query: string
  tracks: SpotifyTrack[]
  albums: SpotifyAlbum[]
  artists: SpotifyArtist[]
  playlists: SpotifyPlaylist[]
}

/** La page d'un album, d'une playlist ou d'un artiste (`albums` seulement pour un artiste). */
export interface SpotifyView {
  kind: 'album' | 'playlist' | 'artist'
  item: SpotifyAlbum | SpotifyPlaylist | SpotifyArtist
  tracks: SpotifyTrack[]
  albums?: SpotifyAlbum[]
}

export type LibrarySection = 'playlists' | 'liked' | 'albums' | 'artists' | 'recent'

export interface SpotifyLibrary {
  section: LibrarySection
  kind: SpotifyItem['kind']
  items: SpotifyItem[]
}

/** Les types d'appareils que Spotify Connect annonce. */
export type SpotifyDeviceType =
  | 'Computer'
  | 'Tablet'
  | 'Smartphone'
  | 'Speaker'
  | 'CastAudio'
  | 'CastVideo'
  | 'TV'
  | 'AVR'
  | 'STB'
  | 'AudioDongle'
  | 'GameConsole'
  | 'Automobile'
  | 'Unknown'

/** Un appareil Spotify Connect du compte : ce PC, une enceinte en Wi-Fi, un téléphone, une télé… */
export interface SpotifyDevice {
  id: string
  name: string
  type: SpotifyDeviceType
  /** La musique sort sur cet appareil en ce moment. */
  active: boolean
  /** Il n'accepte aucune commande à distance. */
  restricted: boolean
  volume: number | null
  can_volume: boolean
  /** C'est ce PC (l'application Spotify installée sur la machine que le dock pilote). */
  this_pc: boolean
}

export interface SpotifyDevices {
  devices: SpotifyDevice[]
}

/** Famille d'un périphérique audio, pour choisir son icône. */
export type AudioKind =
  | 'speakers'
  | 'headphones'
  | 'bluetooth'
  | 'hdmi'
  | 'usb'
  | 'microphone'
  | 'line'
  | 'other'

/** Une sortie (haut-parleurs, casque, HDMI…) ou une entrée (micro). `id` est son nom stable côté PC. */
export interface AudioDevice {
  id: string
  /** Nom français affiché ; `detail` garde le nom que donne l'appareil quand il diffère. */
  label: string
  detail: string | null
  kind: AudioKind
  /** % du canal le plus fort ; peut dépasser 100 si le volume a été amplifié ailleurs. */
  volume: number | null
  muted: boolean
}

/** Une application qui joue du son (un flux de lecture). */
export interface AudioApp {
  id: string
  name: string
  volume: number
  muted: boolean
  /** Faux : le flux existe mais est en pause. */
  playing: boolean
  /** Identifiant de la sortie sur laquelle elle joue. */
  sink: string | null
}

/** Une application qui écoute un micro en ce moment. */
export interface AudioRecorder {
  id: string
  name: string
  source: string
}

/** Thème « audio » (agent) : le son du PC. */
export interface AudioData {
  /** Volume maximal que le dock accorde (en %). */
  max: number
  default_sink: string | null
  default_source: string | null
  sinks: AudioDevice[]
  sources: AudioDevice[]
  apps: AudioApp[]
  recorders: AudioRecorder[]
  unavailable?: string
}

/** Une application installée sur le PC (thème « apps »). `icon` : identifiant à demander par `apps.icon`. */
export interface AppInfo {
  id: string
  name: string
  generic: string | null
  icon: string | null
}

export interface AppsData {
  count: number
  apps: AppInfo[]
  unavailable?: string
}

/** Un jeu Steam installé (thème « steam »). La jaquette se demande par `steam.cover`. */
export interface GameInfo {
  appid: number
  name: string
  size: number
  /** Dernière partie (ou dernière mise à jour), en secondes epoch. */
  played: number | null
}

export interface SteamData {
  /** Le client Steam tourne. */
  client: boolean
  /** Le jeu en cours, s'il y en a un. */
  running: number | null
  games: GameInfo[]
  unavailable?: string
}

export type OfficeKind = 'writer' | 'calc' | 'impress' | 'draw' | 'other'

export interface RecentDocument {
  id: string
  name: string
  kind: OfficeKind
  /** Dossier du document, raccourci (« ~/Documents/Travail »). */
  where: string
  modified: number | null
}

export interface OfficeData {
  kinds: ('writer' | 'calc' | 'impress')[]
  recent: RecentDocument[]
  unavailable?: string
}

/** Ce que le système autorise pour une commande d'alimentation (thème « power »). */
export interface PowerControlState {
  ok: boolean
  /** Pourquoi la commande est grisée (court : il tient sous le bouton). */
  why: string | null
}

/** Un programme qui s'oppose à l'extinction (`shutdown`) ou à la mise en veille (`sleep`). */
export interface PowerBlocker {
  who: string
  why: string
  scope: ('shutdown' | 'sleep')[]
}

export interface PowerData {
  hostname?: string
  /** Absent quand l'agent est trop ancien pour connaître le thème (seul `unavailable` est alors donné). */
  controls?: Partial<Record<'lock' | 'suspend' | 'reboot' | 'poweroff', PowerControlState>>
  blockers?: PowerBlocker[]
  unavailable?: string
}

export interface WolInfo {
  mac: string | null
  source: 'auto' | 'manual' | null
  iface: string | null
  kind: 'ethernet' | 'wifi' | null
  interfaces: { name: string; mac: string; kind: string }[]
}

/** Le portail du Pi : la page web d'où le PC télécharge son paquet et s'associe au dock. */
export interface PortalInfo {
  enabled: boolean
  /** Port d'écoute (80 : pas écrit dans l'adresse) ; null tant que le portail ne tourne pas. */
  port: number | null
  /** Adresses à taper dans le navigateur du PC : les IPv4 du Pi d'abord, puis « nom.local ». */
  urls: string[]
  /** Pourquoi il ne tourne pas (port pris, droit refusé…) ; null quand tout va bien. */
  error: string | null
  /** Le paquet que le PC y télécharge ; null si aucun n'est installé sur le Pi. */
  client: { file: string; version: string } | null
}

/** Comment s'est terminée la dernière session d'association. */
export type PairingLast =
  | { result: 'paired'; name: string; host: string; at: number }
  | { result: 'expired' | 'locked' | 'closed'; at: number }

/** L'association d'un PC : un code affiché sur l'écran du Pi, tapé sur le PC. */
export type PairingInfo =
  | {
      open: true
      /** « K7QM-2XPD ». N'existe que dans ce thème, envoyé à l'écran du Pi seulement. */
      code: string
      /** Heure d'expiration (epoch ms). */
      expires_at: number
      /** Essais incorrects encore permis avant que le code soit annulé. */
      wrong_left: number
      last: null
    }
  | { open: false; last: PairingLast | null }

/** Thème « dock » : ce que le Pi sait de lui et du PC. Jamais le jeton. */
export interface DockInfo {
  version: string
  proto: number
  hostname: string
  /** Adresses IPv4 de ce Pi : celles que le PC voit, à mettre dans allowed_clients. */
  ips: string[]
  pc: { host: string; port: number; token_set: boolean; name: string | null }
  wol: WolInfo
  /** Résumé de la bourse (sans abonnement aux cotations) ; absent d'un dock plus ancien. */
  markets?: { count: number; currency: 'EUR' | 'USD'; refresh: MarketRefresh; finnhub: boolean; demo: boolean }
  /** Page d'installation du PC ; absent d'un dock plus ancien. */
  portal?: PortalInfo
  /** Association d'un PC par code ; absent d'un dock plus ancien. */
  pairing?: PairingInfo
}

export type MarketKind = 'crypto' | 'stock'
/** Secondes entre deux mises à jour d'une crypto ; les actions suivent au double. */
export type MarketRefresh = 15 | 30 | 60 | 300
export type MarketRange = '1d' | '5d' | '1mo' | '1y'
export type MarketSession = 'open' | 'closed' | 'pre' | 'post'

/** Une valeur suivie : une crypto (« BTC ») ou une valeur de Yahoo Finance (« MSTR », « ^FCHI »). */
export interface Watched {
  kind: MarketKind
  symbol: string
}

export interface MarketItem {
  id: string
  kind: MarketKind
  symbol: string
  name: string | null
  /** Null tant que la première cotation n'est pas arrivée. */
  price: number | null
  currency: string | null
  /** Variation sur 24 h (crypto) ou depuis la clôture de la veille (valeur). */
  change: number | null
  change_pct: number | null
  high: number | null
  low: number | null
  state: MarketSession | null
  traded_at: number | null
  spark: number[]
  /** Identifiant du fournisseur : « binance », « yahoo », « finnhub » ou « demo ». */
  source: string | null
  /** Quand le dock a obtenu cette cotation (secondes Unix). */
  fetched: number | null
  /** Pourquoi la dernière mise à jour a échoué (la cotation affichée est alors la dernière connue). */
  error: string | null
}

export interface MarketProvider {
  id: string
  label: string
  state: 'idle' | 'ok' | 'error' | 'paused'
  message: string | null
  /** Quand le dock réessaiera (secondes Unix), pendant une pause. */
  retry_at: number | null
}

export interface MarketsData {
  currency: 'EUR' | 'USD'
  refresh: MarketRefresh
  demo: boolean
  items: MarketItem[]
  fetched_at: number | null
  providers: MarketProvider[]
}

export interface MarketHistory {
  id: string
  range: MarketRange
  currency: string
  source: string
  /** [heure Unix, cours], du plus ancien au plus récent. */
  points: [number, number][]
  fetched: number
}

export interface MarketCandidate {
  kind: MarketKind
  symbol: string
  name: string
  exchange: string | null
  /** « Action », « ETF », « Indice », « Crypto »… */
  label: string | null
}

export interface MarketSearch {
  query: string
  kind: MarketKind
  /** Le fournisseur qui a répondu, ou « local » pour les cryptos que le dock connaît d'avance. */
  source: string | null
  results: MarketCandidate[]
}

export interface MarketCheck {
  id: string
  kind: MarketKind
  symbol: string
  name: string | null
  price: number
  currency: string | null
  source: string
}

/** Thème « ui » : préférences d'affichage persistées sur le Pi. */
export interface UiState {
  last_page: PageId
  order: PageId[]
  hidden: PageId[]
  /** Identifiants .desktop des applications du lanceur, dans l'ordre choisi. */
  pinned_apps: string[]
  /** Secondes d'inactivité avant l'extinction de l'écran du dock ; 0 : jamais. */
  screen_sleep: number
  /** Secondes sans toucher avant l'économiseur d'écran ; 0 : jamais. */
  saver_after: number
  /** Ce que montre l'économiseur : ses scènes animées, les vidéos du Pi, ou les deux. */
  saver_content: 'all' | 'scenes' | 'videos'
}

export interface Ack {
  t: 'ack'
  id: string
  ok: boolean
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  r?: any
  e?: { code: string; msg: string }
  ms?: number
  hop?: { link_ms?: number }
  confirm?: { token: string; ttl: number; label: string }
  /** Ajouté côté interface : l'utilisateur a refusé la confirmation. */
  cancelled?: boolean
}

export interface Snapshot<T = unknown> {
  t: 'snap'
  topic: string
  rev: number
  data: T
}
