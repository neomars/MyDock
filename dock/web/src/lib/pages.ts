// Les neuf pages du dock, dans l'ordre par défaut de la barre d'onglets.

import Cpu from '@lucide/svelte/icons/cpu'
import FileText from '@lucide/svelte/icons/file-text'
import Gamepad2 from '@lucide/svelte/icons/gamepad-2'
import LayoutGrid from '@lucide/svelte/icons/layout-grid'
import Music from '@lucide/svelte/icons/music'
import Power from '@lucide/svelte/icons/power'
import Settings from '@lucide/svelte/icons/settings'
import SlidersVertical from '@lucide/svelte/icons/sliders-vertical'
import TrendingUp from '@lucide/svelte/icons/trending-up'
import type { Component } from 'svelte'
import type { PageId } from './protocol'

export interface PageInfo {
  id: PageId
  /** Libellé court de l'onglet. */
  tab: string
  /** Titre de la page. */
  title: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: Component<any>
  /** Vrai : la page n'a de sens que PC allumé et connecté (sinon l'écran « PC hors ligne »). */
  needsPc: boolean
}

export const PAGES: readonly PageInfo[] = [
  { id: 'music', tab: 'Musique', title: 'Musique', icon: Music, needsPc: true },
  { id: 'sound', tab: 'Son', title: 'Son', icon: SlidersVertical, needsPc: true },
  { id: 'apps', tab: 'Applis', title: "Lanceur d'applications", icon: LayoutGrid, needsPc: true },
  { id: 'steam', tab: 'Steam', title: 'Steam', icon: Gamepad2, needsPc: true },
  { id: 'office', tab: 'Office', title: 'LibreOffice', icon: FileText, needsPc: true },
  { id: 'markets', tab: 'Bourse', title: 'Bourse', icon: TrendingUp, needsPc: false },
  { id: 'system', tab: 'Système', title: 'Système', icon: Cpu, needsPc: true },
  { id: 'power', tab: 'Alimentation', title: 'Alimentation', icon: Power, needsPc: false },
  { id: 'settings', tab: 'Réglages', title: 'Réglages', icon: Settings, needsPc: false },
]

export const pageById = (id: PageId): PageInfo => PAGES.find((p) => p.id === id) ?? PAGES[0]

export interface PageRow {
  page: PageInfo
  visible: boolean
}

/** Toutes les pages dans l'ordre choisi, cachées comprises (les pages absentes de `order` suivent l'ordre par défaut). */
export function pageRows(order: PageId[], hidden: PageId[]): PageRow[] {
  const rank = (id: PageId) => {
    const i = order.indexOf(id)
    return i === -1 ? order.length + PAGES.findIndex((p) => p.id === id) : i
  }
  return [...PAGES]
    .sort((a, b) => rank(a.id) - rank(b.id))
    .map((page) => ({ page, visible: page.id === 'settings' || !hidden.includes(page.id) }))
}

/** Pages visibles dans l'ordre choisi : ce que montre la barre d'onglets. */
export function visiblePages(order: PageId[], hidden: PageId[]): PageInfo[] {
  return pageRows(order, hidden)
    .filter((row) => row.visible)
    .map((row) => row.page)
}

/** L'ordre complet des neuf pages après avoir déplacé `id` d'un rang (inchangé au bord de la liste). */
export function movePage(order: PageId[], hidden: PageId[], id: PageId, delta: -1 | 1): PageId[] {
  const ids = pageRows(order, hidden).map((row) => row.page.id)
  const from = ids.indexOf(id)
  const to = from + delta
  if (from < 0 || to < 0 || to >= ids.length) return ids
  ;[ids[from], ids[to]] = [ids[to], ids[from]]
  return ids
}

/** La liste des pages cachées une fois `id` caché ou remontré. Réglages ne se cache jamais : sans lui, plus de retour possible. */
export function toggleHidden(hidden: PageId[], id: PageId): PageId[] {
  if (id === 'settings') return hidden.filter((h) => h !== 'settings')
  return hidden.includes(id) ? hidden.filter((h) => h !== id) : [...hidden, id]
}

/** La page à montrer : celle demandée si elle est visible, sinon la première visible (une page cachée ne reste pas ouverte au démarrage). */
export function shownPage(wanted: PageId, visible: PageInfo[]): PageId {
  return visible.some((p) => p.id === wanted) || visible.length === 0 ? wanted : visible[0].id
}

/** « 7 pages visibles sur 9 (cachées : Steam, Office). » */
export function pagesSummary(rows: PageRow[]): string {
  const shown = rows.filter((r) => r.visible).length
  const gone = rows.filter((r) => !r.visible).map((r) => r.page.tab)
  const base = `${shown} page${shown > 1 ? 's' : ''} visible${shown > 1 ? 's' : ''} sur ${rows.length}`
  if (gone.length === 0) return `${base}.`
  return `${base} (${gone.length > 1 ? 'cachées' : 'cachée'} : ${gone.join(', ')}).`
}
