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

/** Pages visibles dans l'ordre choisi (les pages absentes de `order` suivent l'ordre par défaut). */
export function visiblePages(order: PageId[], hidden: PageId[]): PageInfo[] {
  const rank = (id: PageId) => {
    const i = order.indexOf(id)
    return i === -1 ? order.length + PAGES.findIndex((p) => p.id === id) : i
  }
  return PAGES.filter((p) => !hidden.includes(p.id) || p.id === 'settings').sort(
    (a, b) => rank(a.id) - rank(b.id),
  )
}
