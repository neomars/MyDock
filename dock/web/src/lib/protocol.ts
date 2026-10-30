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
}

/** Thème « music » (agent) : les lecteurs MPRIS du PC. */
export interface MusicData {
  players: PlayerInfo[]
  active: string | null
  unavailable?: string
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

export interface WolInfo {
  mac: string | null
  source: 'auto' | 'manual' | null
  iface: string | null
  kind: 'ethernet' | 'wifi' | null
  interfaces: { name: string; mac: string; kind: string }[]
}

/** Thème « dock » : ce que le Pi sait de lui et du PC. Jamais le jeton. */
export interface DockInfo {
  version: string
  proto: number
  hostname: string
  /** Adresses IPv4 de ce Pi : celles que le PC voit, à mettre dans allowed_clients. */
  ips: string[]
  pc: { host: string; port: number; token_set: boolean; name: string | null }
  wol: WolInfo
}

/** Thème « ui » : préférences d'affichage persistées sur le Pi. */
export interface UiState {
  last_page: PageId
  order: PageId[]
  hidden: PageId[]
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
