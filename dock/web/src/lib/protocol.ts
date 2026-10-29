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
