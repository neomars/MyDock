// Aides pures de la page Son et du volume de la page Musique. Testées dans audio.test.ts.

import Bluetooth from '@lucide/svelte/icons/bluetooth'
import Cable from '@lucide/svelte/icons/cable'
import Headphones from '@lucide/svelte/icons/headphones'
import Mic from '@lucide/svelte/icons/mic'
import MonitorSpeaker from '@lucide/svelte/icons/monitor-speaker'
import Speaker from '@lucide/svelte/icons/speaker'
import Usb from '@lucide/svelte/icons/usb'
import Volume1 from '@lucide/svelte/icons/volume-1'
import Volume2 from '@lucide/svelte/icons/volume-2'
import VolumeX from '@lucide/svelte/icons/volume-x'
import type { Component } from 'svelte'
import { NBSP } from './format'
import type { AudioApp, AudioDevice, AudioKind, AudioRecorder } from './protocol'

export const DEFAULT_MAX = 100
/** Le curseur d'un doigt ne vaut pas mieux que le pourcent : pas de réglage plus fin. */
export const KEY_STEP = 5

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Icon = Component<any>

/** Volume entier entre 0 et `max`. */
export function clampVolume(value: number, max = DEFAULT_MAX): number {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(max, Math.round(value)))
}

/** Volume correspondant à un appui à `ratio` (0 à 1) de la piste. */
export const ratioToVolume = (ratio: number, max = DEFAULT_MAX): number => clampVolume(ratio * max, max)

/** Position (0 à 1) d'un volume sur la piste ; un volume amplifié au-delà de `max` reste en bout de course. */
export function volumeToRatio(volume: number, max = DEFAULT_MAX): number {
  if (!Number.isFinite(volume) || max <= 0) return 0
  return Math.max(0, Math.min(1, volume / max))
}

/** Volume après une touche du clavier : flèches ±5, PageHaut/PageBas ±10, début et fin. */
export function keyVolume(key: string, value: number, max = DEFAULT_MAX): number | null {
  switch (key) {
    case 'ArrowRight':
    case 'ArrowUp':
      return clampVolume(value + KEY_STEP, max)
    case 'ArrowLeft':
    case 'ArrowDown':
      return clampVolume(value - KEY_STEP, max)
    case 'PageUp':
      return clampVolume(value + 2 * KEY_STEP, max)
    case 'PageDown':
      return clampVolume(value - 2 * KEY_STEP, max)
    case 'Home':
      return 0
    case 'End':
      return max
    default:
      return null
  }
}

/** « 54 % », « Muet » quand le son est coupé, « — » quand le volume est inconnu. */
export function formatVolume(volume: number | null, muted: boolean): string {
  if (muted) return 'Muet'
  return volume === null ? '—' : `${Math.round(volume)}${NBSP}%`
}

/** Le périphérique que `id` désigne, ou null (aucun par défaut, ou périphérique débranché). */
export function deviceById(devices: AudioDevice[] | undefined, id: string | null | undefined): AudioDevice | null {
  if (!devices || !id) return null
  return devices.find((d) => d.id === id) ?? null
}

const KIND_ICONS: Record<AudioKind, Icon> = {
  speakers: Speaker,
  headphones: Headphones,
  bluetooth: Bluetooth,
  hdmi: MonitorSpeaker,
  usb: Usb,
  microphone: Mic,
  line: Cable,
  other: Volume2,
}

export const deviceIcon = (kind: AudioKind): Icon => KIND_ICONS[kind] ?? Volume2

/** L'icône du bouton muet : barrée si le son est coupé, un seul arc quand le volume est bas. */
export function speakerIcon(volume: number | null, muted: boolean): Icon {
  if (muted || volume === 0) return VolumeX
  return volume !== null && volume < 34 ? Volume1 : Volume2
}

/** Noms affichés des applications : deux « Brave » deviennent « Brave 1 » et « Brave 2 ». */
export function appNames(apps: AudioApp[]): Record<string, string> {
  const counts: Record<string, number> = {}
  for (const app of apps) counts[app.name] = (counts[app.name] ?? 0) + 1
  const seen: Record<string, number> = {}
  const names: Record<string, string> = {}
  for (const app of apps) {
    seen[app.name] = (seen[app.name] ?? 0) + 1
    names[app.id] = counts[app.name] > 1 ? `${app.name} ${seen[app.name]}` : app.name
  }
  return names
}

export const appInitial = (name: string): string => (Array.from(name.trim())[0] ?? '?').toUpperCase()

/** Teinte (0 à 359) propre à un nom : la pastille d'une application garde toujours la même couleur. */
export function appHue(name: string): number {
  let hash = 0
  for (const char of name.toLowerCase()) hash = (hash * 31 + char.codePointAt(0)!) % 360
  return hash
}

/** « Discord », « Discord et Brave », « Discord, Brave et 2 autres ». */
export function recordingSummary(recorders: AudioRecorder[]): string {
  const names = [...new Set(recorders.map((r) => r.name))]
  if (names.length <= 1) return names[0] ?? ''
  if (names.length === 2) return `${names[0]} et ${names[1]}`
  const others = names.length - 2
  return `${names[0]}, ${names[1]} et ${others}${NBSP}autre${others > 1 ? 's' : ''}`
}
