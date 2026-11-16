// Étape n : Free TV (tv.free.fr dans Chromium) remplace Freetv.
// Vrai Chromium 1024×600 tactile -> vrai dock -> assistant de lecture en démonstration (une fausse fenêtre de Free TV : aucun
// Chromium sur l'écran du Pi). La vraie fenêtre, son identifiant et le passage de l'une à l'autre sont essayés par les tests
// Python sous un vrai labwc (dock/server/tests/test_freetv_real.py).

import fs from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, goHome, helperReady, openPage, shot, test } from './fixtures'

const noScroll = (page: Page) =>
  page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))

/**
 * Tout l'écran « Free TV ouvert » tient dans 1024 × 600, texte doublé compris : aucun élément ne dépasse de l'écran ni de
 * son bouton (un texte rogné ou coupé en bas ne fait pas défiler la page : seule cette mesure le voit).
 */
async function fitsTheScreen(page: Page, testid: string): Promise<void> {
  const problems = await page.evaluate((id) => {
    const root = document.querySelector(`[data-testid=${id}]`) as HTMLElement
    const out: string[] = []
    const box = root.getBoundingClientRect()
    if (box.bottom > 600) {
      const rows = [...root.children].map((c) => `${c.getAttribute('data-testid') ?? c.className} ${Math.round(c.getBoundingClientRect().top)}→${Math.round(c.getBoundingClientRect().bottom)}`)
      out.push(`le bas de l'écran dépasse : ${Math.round(box.bottom)} px (${rows.join(' ; ')})`)
    }
    for (const el of root.querySelectorAll<HTMLElement>('*')) {
      const r = el.getBoundingClientRect()
      if (r.width === 0 || r.height === 0) continue
      const label = el.getAttribute('data-testid') ?? el.tagName.toLowerCase()
      if (r.right > box.right + 1 || r.left < box.left - 1) out.push(`${label} dépasse en largeur (${Math.round(r.left)} → ${Math.round(r.right)})`)
      if (r.bottom > 600) out.push(`${label} dépasse en bas (${Math.round(r.bottom)} px)`)
      if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).display !== 'inline') out.push(`${label} est rogné (${el.scrollWidth} > ${el.clientWidth})`)
    }
    return out
  }, testid)
  expect(problems).toEqual([])
}

/** Ouvre Free TV depuis le menu du Divertissement. */
async function openFreeTv(page: Page): Promise<void> {
  await openPage(page, 'media')
  await page.getByTestId('media-freetv').tap()
  await expect(page.getByTestId('freetv-now')).toBeVisible()
}

test('Free TV : toucher l’entrée l’ouvre ; le dock montre « Free TV ouvert » avec ses boutons, rien ne déborde', async ({ dock }) => {
  await helperReady(dock)
  await openPage(dock, 'media')
  await expect(dock.getByTestId('media-freetv')).toContainText('Free TV')
  await expect(dock.getByTestId('media-tv')).toHaveCount(0) // Freetv n'existe plus
  await dock.waitForTimeout(300)
  await shot(dock, '00-divertissement-free-tv', 'n')
  await dock.getByTestId('media-freetv').tap()

  const now = dock.getByTestId('freetv-now')
  await expect(now).toBeVisible()
  await expect(dock.getByTestId('freetv-title')).toHaveText('Free TV ouvert')
  await expect(now).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await expect(dock.getByTestId('freetv-state')).toHaveText('Free TV est à l’écran')
  await expect(dock.getByTestId('freetv-back')).toHaveText('Revenir à Free TV')
  await expect(dock.getByTestId('freetv-keyboard')).toHaveText('Clavier')
  await expect(dock.getByTestId('freetv-close')).toHaveText('Fermer Free TV')
  await expect(dock.getByTestId('freetv-howto')).toContainText('en haut à gauche')
  await expect(dock.getByTestId('freetv-howto')).toContainText('Un toucher navigue dans Free TV')
  await expect(dock.getByTestId('freetv-audio')).toHaveText('Son : sur le PC', { timeout: 15_000 })
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await fitsTheScreen(dock, 'freetv-now')
  await dock.waitForTimeout(300)
  await shot(dock, '01-free-tv-ouvert', 'n')

  await goHome(dock)
  await expect(dock.getByTestId('tile-media-sub')).toHaveText('Free TV') // l'accueil dit ce qui occupe l'écran
  await dock.getByTestId('tile-media').tap()
  await expect(now).toBeVisible() // la page s'ouvre sur Free TV tant qu'il est ouvert
  await dock.getByTestId('freetv-close').tap()
  await expect(now).toHaveCount(0)
})

test('Free TV : un appui long dans le coin ramène le dock, Free TV reste ouvert derrière et revient à la demande', async ({ dock, stack }) => {
  await helperReady(dock)
  await openFreeTv(dock)
  const now = dock.getByTestId('freetv-now')
  await expect(now).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await expect(now).toHaveAttribute('data-front', 'video')

  fs.writeFileSync(path.join(stack.runDir, 'mydock-demo-corner'), '') // l'appui long, lu en evdev par l'assistant : en démo, un fichier
  await expect(now).toHaveAttribute('data-front', 'dock', { timeout: 5_000 })
  await expect(dock.getByTestId('freetv-state')).toHaveText('Free TV reste ouvert derrière')
  await fitsTheScreen(dock, 'freetv-now')
  await dock.waitForTimeout(300)
  await shot(dock, '02-free-tv-derriere-le-dock', 'n')

  // Les autres pages du dock restent accessibles, et revenir au dock ne ferme pas Free TV.
  await openPage(dock, 'system')
  await expect(dock.getByTestId('page-system')).toBeVisible()
  await openPage(dock, 'media')
  await expect(now).toBeVisible()
  await expect(now).toHaveAttribute('data-state', 'playing')

  await dock.getByTestId('freetv-back').tap() // « Revenir à Free TV »
  await expect(now).toHaveAttribute('data-front', 'video', { timeout: 5_000 })
  await dock.getByTestId('freetv-close').tap()
})

test('Free TV : le bouton physique ramène aussi le dock, sans fermer Free TV', async ({ dock, stack }) => {
  await helperReady(dock)
  await openFreeTv(dock)
  const now = dock.getByTestId('freetv-now')
  await expect(now).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  fs.writeFileSync(path.join(stack.runDir, 'mydock-demo-button'), '')
  await expect(now).toHaveAttribute('data-front', 'dock', { timeout: 5_000 })
  await expect(now).toHaveAttribute('data-state', 'playing')
  await dock.getByTestId('freetv-close').tap()
})

test('Free TV : « Clavier » remet Free TV devant avec un clavier à l’écran, le retour au dock le cache', async ({ dock, stack }) => {
  await helperReady(dock)
  await openFreeTv(dock)
  const now = dock.getByTestId('freetv-now')
  await expect(now).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  fs.writeFileSync(path.join(stack.runDir, 'mydock-demo-corner'), '')
  await expect(now).toHaveAttribute('data-front', 'dock', { timeout: 5_000 })

  const keyboard = dock.getByTestId('freetv-keyboard')
  await expect(keyboard).toHaveAttribute('aria-pressed', 'false')
  await keyboard.tap()
  await expect(now).toHaveAttribute('data-front', 'video', { timeout: 5_000 }) // Free TV d'abord, puis le clavier dedans
  await expect(keyboard).toHaveAttribute('aria-pressed', 'true', { timeout: 5_000 })
  await shot(dock, '03-free-tv-clavier', 'n')

  fs.writeFileSync(path.join(stack.runDir, 'mydock-demo-corner'), '')
  await expect(now).toHaveAttribute('data-front', 'dock', { timeout: 5_000 })
  await expect(keyboard).toHaveAttribute('aria-pressed', 'false', { timeout: 5_000 }) // le clavier se cache au retour au dock
  await dock.getByTestId('freetv-close').tap()
})

test('Free TV : « Fermer Free TV » le ferme et rend le menu, sans erreur', async ({ dock }) => {
  await helperReady(dock)
  await openFreeTv(dock)
  await expect(dock.getByTestId('freetv-now')).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await dock.getByTestId('freetv-close').tap()
  await expect(dock.getByTestId('freetv-now')).toHaveCount(0)
  await expect(dock.getByTestId('media-freetv')).toBeVisible()
  await expect(dock.getByTestId('media-error')).toHaveCount(0)
  await expect(dock.getByTestId('media-now')).toHaveCount(0)
  await goHome(dock)
  await expect(dock.getByTestId('tile-media-sub')).toHaveCount(0)
})

test('Free TV et le Kiosque : un seul occupant à la fois, l’un ferme l’autre', async ({ dock }) => {
  await helperReady(dock)
  await openFreeTv(dock)
  await expect(dock.getByTestId('freetv-now')).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await dock.getByTestId('media-other').tap() // « Menu » : Free TV reste ouvert
  await expect(dock.getByTestId('media-now')).toContainText('Free TV')

  await dock.getByTestId('media-files').tap() // une vidéo du Kiosque ferme Free TV
  await dock.getByTestId('share-Disque dur').tap()
  await dock.getByTestId('dir-Films').tap()
  await dock.getByTestId("video-Un film d'été (2020).mkv").tap()
  await expect(dock.getByTestId('remote')).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await expect(dock.getByTestId('freetv-now')).toHaveCount(0)
  await dock.getByTestId('media-other').tap()
  await expect(dock.getByTestId('media-now')).toContainText("Un film d'été (2020)")

  await dock.getByTestId('media-freetv').tap() // et Free TV ferme la vidéo
  await expect(dock.getByTestId('freetv-now')).toBeVisible()
  await expect(dock.getByTestId('remote')).toHaveCount(0)
  await expect(dock.getByTestId('media-now')).toHaveCount(0)
  await dock.getByTestId('freetv-close').tap()
})

test('Réglages : la carte Divertissement parle de Free TV et de dock.toml, plus de la liste des chaînes', async ({ dock }) => {
  await openPage(dock, 'settings')
  const card = dock.getByTestId('settings-media')
  await card.scrollIntoViewIfNeeded()
  await expect(card).toContainText('Free TV')
  await expect(card).toContainText('[freetv]')
  await expect(card).not.toContainText('Freetv')
  await expect(card).not.toContainText('M3U')
  await expect(dock.getByTestId('media-url')).toHaveCount(0)
  await expect(dock.getByTestId('toast')).toHaveCount(0, { timeout: 8_000 })
  await dock.waitForTimeout(300)
  await shot(dock, '06-reglages-divertissement', 'n')
})
