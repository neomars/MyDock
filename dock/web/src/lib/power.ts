// Alimentation du PC : les quatre commandes, ce que le système en autorise, et les messages d'après.

import type { Ack, PowerBlocker, PowerData } from './protocol'

export type PowerControlId = 'lock' | 'suspend' | 'reboot' | 'poweroff'

export interface PowerControlInfo {
  id: PowerControlId
  label: string
  /** Message affiché quand le PC a accepté l'ordre. */
  done: string
  /** Vrai : l'ordre coupe la liaison avec le PC (veille, redémarrage, extinction). */
  cuts: boolean
}

export const POWER_CONTROLS: readonly PowerControlInfo[] = [
  { id: 'lock', label: 'Verrouiller', done: 'Écran du PC verrouillé.', cuts: false },
  { id: 'suspend', label: 'Mettre en veille', done: 'Mise en veille demandée…', cuts: true },
  { id: 'reboot', label: 'Redémarrer', done: 'Redémarrage demandé…', cuts: true },
  { id: 'poweroff', label: 'Éteindre', done: 'Extinction demandée…', cuts: true },
]

export const controlById = (id: PowerControlId): PowerControlInfo =>
  POWER_CONTROLS.find((c) => c.id === id) ?? POWER_CONTROLS[0]

export interface ControlView {
  ok: boolean
  /** Raison du grisage, sous le bouton. */
  why: string | null
}

/** Ce que le bouton montre : actif, ou grisé avec sa raison. */
export function controlView(
  id: PowerControlId,
  connected: boolean,
  data: PowerData | undefined,
): ControlView {
  if (!connected) return { ok: false, why: 'PC hors ligne' }
  if (!data) return { ok: false, why: 'Lecture des droits…' }
  const control = data.controls?.[id]
  if (!control) return { ok: false, why: data.unavailable ? 'Indisponible' : 'Non pris en charge' }
  return control.ok ? { ok: true, why: null } : { ok: false, why: control.why ?? 'Indisponible' }
}

/** Une phrase par programme qui s'oppose : « « Mises à jour » empêche l'extinction (Installation en cours). » */
export function blockerLine(blocker: PowerBlocker): string {
  const what = blocker.scope.includes('shutdown')
    ? blocker.scope.includes('sleep')
      ? "l'extinction et la mise en veille"
      : "l'extinction"
    : 'la mise en veille'
  const why = blocker.why ? ` (${blocker.why})` : ''
  return `« ${blocker.who} » empêche ${what}${why}.`
}

export interface Outcome {
  kind: 'good' | 'info' | 'bad'
  text: string
}

/**
 * Le message à montrer après une commande, d'après l'accusé de réception ; `null` si la personne a
 * annulé la confirmation (rien à dire).
 *
 * Une commande qui coupe le PC peut ne jamais recevoir son accusé : la liaison tombe avant. Ce n'est
 * pas une panne, mais on ne l'affirme pas non plus comme une réussite.
 */
export function outcome(id: PowerControlId, ack: Ack): Outcome | null {
  if (ack.cancelled) return null
  const control = controlById(id)
  if (ack.ok) return { kind: 'good', text: control.done }
  const code = ack.e?.code
  if (control.cuts && (code === 'pc_offline' || code === 'timeout')) {
    return {
      kind: 'info',
      text: 'Plus de réponse du PC : la commande est probablement partie, regarde son écran.',
    }
  }
  if (code === 'unknown_action') {
    return {
      kind: 'bad',
      text: "Cette version de l'agent ne sait pas faire cela : mets le paquet du PC à jour.",
    }
  }
  return { kind: 'bad', text: ack.e?.msg || "La commande n'a pas pu être envoyée." }
}
