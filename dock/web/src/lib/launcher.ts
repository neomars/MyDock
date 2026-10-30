// Aides pures du lanceur d'applications, de Steam et de LibreOffice. Testées dans launcher.test.ts.

import File from '@lucide/svelte/icons/file'
import FileSpreadsheet from '@lucide/svelte/icons/file-spreadsheet'
import FileText from '@lucide/svelte/icons/file-text'
import Presentation from '@lucide/svelte/icons/presentation'
import Shapes from '@lucide/svelte/icons/shapes'
import type { Component } from 'svelte'
import type { AppInfo, GameInfo, OfficeKind } from './protocol'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Icon = Component<any>

export const MAX_PINNED = 24

export interface Tile {
  id: string
  /** L'application dans le catalogue du PC ; null si elle n'est plus installée (ou pas encore reçue). */
  app: AppInfo | null
  name: string
}

/** « org.mozilla.firefox.desktop » -> « org.mozilla.firefox » : de quoi nommer une application introuvable. */
export const idLabel = (id: string): string => id.replace(/\.desktop$/, '')

/** Les applications choisies, dans l'ordre choisi, avec le nom que le PC leur donne. */
export function pinnedTiles(pinned: string[], catalog: AppInfo[] | undefined): Tile[] {
  const byId = new Map((catalog ?? []).map((app) => [app.id, app]))
  return pinned.map((id) => {
    const app = byId.get(id) ?? null
    return { id, app, name: app?.name ?? idLabel(id) }
  })
}

const plain = (text: string): string =>
  text
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()

/** Applications dont le nom ou la catégorie contient tous les mots tapés (sans tenir compte des accents). */
export function filterApps(apps: AppInfo[], query: string): AppInfo[] {
  const words = plain(query).split(/\s+/).filter(Boolean)
  if (words.length === 0) return apps
  return apps.filter((app) => {
    const haystack = plain(`${app.name} ${app.generic ?? ''}`)
    return words.every((word) => haystack.includes(word))
  })
}

/** La liste après avoir ajouté ou retiré `id` ; null si elle est pleine. */
export function toggled(pinned: string[], id: string, max = MAX_PINNED): string[] | null {
  if (pinned.includes(id)) return pinned.filter((p) => p !== id)
  return pinned.length >= max ? null : [...pinned, id]
}

/** La liste après avoir déplacé l'élément `index` d'un cran (-1 vers le haut, 1 vers le bas). */
export function moved(list: string[], index: number, delta: -1 | 1): string[] {
  const target = index + delta
  if (index < 0 || index >= list.length || target < 0 || target >= list.length) return list
  const next = [...list]
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

/** Le jeu en cours d'abord, puis le plus récemment joué, puis l'ordre alphabétique. */
export function sortGames(games: GameInfo[], running: number | null): GameInfo[] {
  return [...games].sort((a, b) => {
    if ((a.appid === running) !== (b.appid === running)) return a.appid === running ? -1 : 1
    if ((a.played ?? 0) !== (b.played ?? 0)) return (b.played ?? 0) - (a.played ?? 0)
    return a.name.localeCompare(b.name, 'fr')
  })
}

export const KIND_LABELS: Record<OfficeKind, string> = {
  writer: 'Texte',
  calc: 'Tableur',
  impress: 'Présentation',
  draw: 'Dessin',
  other: 'Document',
}

export const kindLabel = (kind: OfficeKind): string => KIND_LABELS[kind] ?? KIND_LABELS.other

const KIND_ICONS: Record<OfficeKind, Icon> = {
  writer: FileText,
  calc: FileSpreadsheet,
  impress: Presentation,
  draw: Shapes,
  other: File,
}

export const kindIcon = (kind: OfficeKind): Icon => KIND_ICONS[kind] ?? File
