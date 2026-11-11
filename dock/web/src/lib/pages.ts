// Les pages du dock. Neuf ont un gros bouton à l'accueil (dans l'ordre par défaut) ; « Alimentation » n'en a plus : c'est
// un petit bouton à droite de l'heure, dans la barre du haut.

import Cpu from '@lucide/svelte/icons/cpu'
import FileText from '@lucide/svelte/icons/file-text'
import Clapperboard from '@lucide/svelte/icons/clapperboard'
import Gamepad2 from '@lucide/svelte/icons/gamepad-2'
import LayoutGrid from '@lucide/svelte/icons/layout-grid'
import Power from '@lucide/svelte/icons/power'
import Settings from '@lucide/svelte/icons/settings'
import SlidersVertical from '@lucide/svelte/icons/sliders-vertical'
import TrendingUp from '@lucide/svelte/icons/trending-up'
import type { Component } from 'svelte'
import SpotifyIcon from '../components/SpotifyIcon.svelte'
import type { PageId } from './protocol'

export interface PageInfo {
  id: PageId
  /** Libellé court du bouton de l'accueil. */
  tab: string
  /** Titre de la page. */
  title: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: Component<any>
  /** Vrai : la page n'a de sens que PC allumé et connecté (sinon l'écran « PC hors ligne »). */
  needsPc: boolean
  /** Faux : pas de gros bouton à l'accueil (la page s'ouvre autrement : l'alimentation, par la barre du haut). */
  home?: boolean
  /** Les deux couleurs du bouton (du haut vers le bas) : une teinte par page, comme les touches d'un Stream Deck.
   *  Le bas, où se lit le libellé en blanc, offre un contraste d'au moins 4,5 : 1 ; le haut, où se dessine l'icône, d'au moins 3 : 1. */
  tone: { from: string; to: string }
}

export const PAGES: readonly PageInfo[] = [
  { id: 'music', tab: 'Spotify', title: 'Spotify', icon: SpotifyIcon, needsPc: true, tone: { from: '#16a34a', to: '#0b6b2e' } },
  { id: 'sound', tab: 'Son', title: 'Son', icon: SlidersVertical, needsPc: true, tone: { from: '#3b82f6', to: '#1e40af' } },
  { id: 'apps', tab: 'Applis', title: "Lanceur d'applications", icon: LayoutGrid, needsPc: true, tone: { from: '#ec4899', to: '#9d174d' } },
  { id: 'steam', tab: 'Steam', title: 'Steam', icon: Gamepad2, needsPc: true, tone: { from: '#6366f1', to: '#312e81' } },
  { id: 'office', tab: 'Office', title: 'LibreOffice', icon: FileText, needsPc: true, tone: { from: '#059669', to: '#065f46' } },
  { id: 'markets', tab: 'Bourse', title: 'Bourse', icon: TrendingUp, needsPc: false, tone: { from: '#d97706', to: '#92400e' } },
  { id: 'system', tab: 'Système', title: 'Système', icon: Cpu, needsPc: true, tone: { from: '#0891b2', to: '#155e75' } },
  { id: 'media', tab: 'Divertissement', title: 'Divertissement', icon: Clapperboard, needsPc: false, tone: { from: '#a855f7', to: '#6b21a8' } },
  { id: 'power', tab: 'Alimentation', title: 'Alimentation', icon: Power, needsPc: false, home: false, tone: { from: '#ef4444', to: '#991b1b' } },
  { id: 'settings', tab: 'Réglages', title: 'Réglages', icon: Settings, needsPc: false, tone: { from: '#64748b', to: '#334155' } },
]

export const pageById = (id: PageId): PageInfo => PAGES.find((p) => p.id === id) ?? PAGES[0]

export interface PageRow {
  page: PageInfo
  visible: boolean
}

/** Les pages qui ont un gros bouton à l'accueil. */
export const HOME_PAGES: readonly PageInfo[] = PAGES.filter((p) => p.home !== false)

/** Toutes les pages de l'accueil dans l'ordre choisi, cachées comprises (celles absentes de `order` suivent l'ordre par défaut). */
export function pageRows(order: PageId[], hidden: PageId[]): PageRow[] {
  const rank = (id: PageId) => {
    const i = order.indexOf(id)
    return i === -1 ? order.length + HOME_PAGES.findIndex((p) => p.id === id) : i
  }
  return [...HOME_PAGES]
    .sort((a, b) => rank(a.id) - rank(b.id))
    .map((page) => ({ page, visible: page.id === 'settings' || !hidden.includes(page.id) }))
}

/** Pages visibles dans l'ordre choisi : un gros bouton de l'accueil chacune. */
export function visiblePages(order: PageId[], hidden: PageId[]): PageInfo[] {
  return pageRows(order, hidden)
    .filter((row) => row.visible)
    .map((row) => row.page)
}

/** L'ordre complet des pages de l'accueil après avoir déplacé `id` d'un rang (inchangé au bord de la liste). */
export function movePage(order: PageId[], hidden: PageId[], id: PageId, delta: -1 | 1): PageId[] {
  const ids = pageRows(order, hidden).map((row) => row.page.id)
  const from = ids.indexOf(id)
  const to = from + delta
  if (from < 0 || to < 0 || to >= ids.length) return ids
  ;[ids[from], ids[to]] = [ids[to], ids[from]]
  return ids
}

/** La liste des pages cachées une fois `id` caché ou remontré. Réglages ne se cache jamais : sans lui, plus moyen de les remettre. */
export function toggleHidden(hidden: PageId[], id: PageId): PageId[] {
  if (id === 'settings') return hidden.filter((h) => h !== 'settings')
  return hidden.includes(id) ? hidden.filter((h) => h !== id) : [...hidden, id]
}

/** « 7 pages visibles sur 9 (cachées : Steam, Office). » */
export function pagesSummary(rows: PageRow[]): string {
  const shown = rows.filter((r) => r.visible).length
  const gone = rows.filter((r) => !r.visible).map((r) => r.page.tab)
  const base = `${shown} page${shown > 1 ? 's' : ''} visible${shown > 1 ? 's' : ''} sur ${rows.length}`
  if (gone.length === 0) return `${base}.`
  return `${base} (${gone.length > 1 ? 'cachées' : 'cachée'} : ${gone.join(', ')}).`
}
