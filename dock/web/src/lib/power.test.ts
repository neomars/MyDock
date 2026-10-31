import { describe, expect, it } from 'vitest'
import { blockerLine, controlById, controlView, outcome, POWER_CONTROLS } from './power'
import type { Ack, PowerData } from './protocol'

const ack = (partial: Partial<Ack>): Ack => ({ t: 'ack', id: 'b1', ok: false, ...partial })

const full: PowerData = {
  hostname: 'mars-pc',
  controls: {
    lock: { ok: true, why: null },
    suspend: { ok: false, why: 'Indisponible sur ce PC' },
    reboot: { ok: true, why: null },
    poweroff: { ok: false, why: 'Bloqué par « Mises à jour »' },
  },
  blockers: [],
}

describe('les quatre commandes', () => {
  it("sont dans l'ordre de la page, et seules les trois dernières coupent la liaison", () => {
    expect(POWER_CONTROLS.map((c) => c.id)).toEqual(['lock', 'suspend', 'reboot', 'poweroff'])
    expect(POWER_CONTROLS.map((c) => c.cuts)).toEqual([false, true, true, true])
    expect(controlById('poweroff').label).toBe('Éteindre')
  })
})

describe('controlView', () => {
  it('grise tout, avec la raison, quand le PC est hors ligne', () => {
    for (const control of POWER_CONTROLS) {
      expect(controlView(control.id, false, full)).toEqual({ ok: false, why: 'PC hors ligne' })
    }
  })

  it("attend la réponse de l'agent avant de proposer quoi que ce soit", () => {
    expect(controlView('lock', true, undefined)).toEqual({ ok: false, why: 'Lecture des droits…' })
  })

  it('reprend la décision du système, commande par commande', () => {
    expect(controlView('lock', true, full)).toEqual({ ok: true, why: null })
    expect(controlView('suspend', true, full)).toEqual({ ok: false, why: 'Indisponible sur ce PC' })
    expect(controlView('poweroff', true, full).why).toBe('Bloqué par « Mises à jour »')
  })

  it('ne casse pas avec un agent trop ancien (seul « unavailable » est donné)', () => {
    const old: PowerData = { unavailable: "Pas encore disponible dans cette version de l'agent." }
    expect(controlView('reboot', true, old)).toEqual({ ok: false, why: 'Indisponible' })
    expect(controlView('reboot', true, {})).toEqual({ ok: false, why: 'Non pris en charge' })
  })

  it('donne une raison même si le système n’en a pas donné', () => {
    const data: PowerData = { controls: { reboot: { ok: false, why: null } } }
    expect(controlView('reboot', true, data)).toEqual({ ok: false, why: 'Indisponible' })
  })
})

describe('blockerLine', () => {
  it("nomme le programme et ce qu'il empêche", () => {
    expect(blockerLine({ who: 'Mises à jour', why: 'Installation en cours', scope: ['shutdown'] })).toBe(
      "« Mises à jour » empêche l'extinction (Installation en cours).",
    )
    expect(blockerLine({ who: 'Vidéo', why: '', scope: ['sleep'] })).toBe('« Vidéo » empêche la mise en veille.')
    expect(blockerLine({ who: 'Copie', why: 'Longue', scope: ['shutdown', 'sleep'] })).toBe(
      "« Copie » empêche l'extinction et la mise en veille (Longue).",
    )
  })
})

describe('outcome', () => {
  it('annonce la réussite de chaque commande', () => {
    expect(outcome('lock', ack({ ok: true }))).toEqual({ kind: 'good', text: 'Écran du PC verrouillé.' })
    expect(outcome('poweroff', ack({ ok: true }))?.text).toBe('Extinction demandée…')
  })

  it("ne dit rien quand la confirmation a été refusée", () => {
    expect(outcome('poweroff', ack({ cancelled: true, e: { code: 'confirm_required', msg: 'x' } }))).toBeNull()
  })

  it('traite une liaison coupée après un ordre qui coupe le PC comme probablement réussie', () => {
    for (const code of ['pc_offline', 'timeout']) {
      const result = outcome('suspend', ack({ e: { code, msg: 'Connexion au PC perdue.' } }))
      expect(result?.kind).toBe('info')
      expect(result?.text).toContain('probablement partie')
    }
  })

  it("mais pas pour le verrouillage, qui ne coupe rien : là c'est une vraie panne", () => {
    const result = outcome('lock', ack({ e: { code: 'pc_offline', msg: 'Connexion au PC perdue.' } }))
    expect(result).toEqual({ kind: 'bad', text: 'Connexion au PC perdue.' })
  })

  it("rapporte le refus du système tel que l'agent le dit", () => {
    const result = outcome('poweroff', ack({ e: { code: 'forbidden', msg: '« Mises à jour » empêche l\'extinction.' } }))
    expect(result).toEqual({ kind: 'bad', text: "« Mises à jour » empêche l'extinction." })
  })

  it("explique qu'un agent ancien ne connaît pas la commande", () => {
    const result = outcome('reboot', ack({ e: { code: 'unknown_action', msg: 'Action inconnue' } }))
    expect(result?.kind).toBe('bad')
    expect(result?.text).toContain('mets le paquet du PC à jour')
  })

  it('a toujours un texte, même sans message', () => {
    expect(outcome('lock', ack({}))?.text).toBe("La commande n'a pas pu être envoyée.")
  })
})
