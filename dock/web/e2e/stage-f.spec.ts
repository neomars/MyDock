// Étape f : Alimentation, Réglages complets, veille de l'écran. Vrai Chromium 1024×600 tactile -> vrai dock
// -> vrai agent de démonstration (aucun ordre n'éteint rien : le mode démo répond sans rien faire).

import type { Page } from '@playwright/test'
import { expect, goHome, openPage, shot, test, tileOrder } from './fixtures'

async function openPower(dock: Page): Promise<void> {
  await openPage(dock, 'power')
  await expect(dock.getByTestId('power-card')).toBeVisible()
  await expect(dock.getByTestId('power-poweroff')).toBeEnabled() // les droits sont arrivés
}

// --- Alimentation --------------------------------------------------------------------------------

test('Alimentation : les quatre commandes du PC sont proposées et nomment le PC', async ({ dock }) => {
  await openPower(dock)
  await expect(dock.getByTestId('power-card')).toContainText('Contrôler « pc-demo »')
  for (const id of ['lock', 'suspend', 'reboot', 'poweroff']) {
    await expect(dock.getByTestId(`power-${id}`)).toBeEnabled()
    await expect(dock.getByTestId(`power-why-${id}`)).toHaveCount(0)
  }
  await expect(dock.getByTestId('power-blockers')).toHaveCount(0)
  // Le réveil à distance reste à côté, comme avant.
  await expect(dock.getByTestId('wol-card')).toBeVisible()
  await shot(dock, '01-alimentation', 'f')
})

test('Alimentation : verrouiller se fait d’un appui, sans confirmation', async ({ dock }) => {
  await openPower(dock)
  await dock.getByTestId('power-lock').tap()
  await expect(dock.getByTestId('toast')).toContainText('Écran du PC verrouillé.')
  await expect(dock.getByTestId('confirm')).toHaveCount(0)
})

test('Alimentation : éteindre demande confirmation, annuler ne fait rien', async ({ dock }) => {
  await openPower(dock)
  await dock.getByTestId('power-poweroff').tap()
  const dialog = dock.getByTestId('confirm')
  await expect(dialog).toContainText('Éteindre « pc-demo »')
  await expect(dialog).toContainText('les programmes ouverts seront fermés')
  await expect(dialog).toContainText('démo, sans effet réel')
  await shot(dock, '02-confirmer-extinction', 'f')

  await dock.getByTestId('confirm-cancel').tap()
  await expect(dialog).toHaveCount(0)
  await expect(dock.getByTestId('toast')).toHaveCount(0) // aucun message : rien n'a été demandé
  await expect(dock.getByTestId('power-poweroff')).toBeEnabled()
})

test('Alimentation : confirmer envoie l’ordre, et le bouton reste « en cours » le temps que le PC réagisse', async ({ dock }) => {
  await openPower(dock)
  await dock.getByTestId('power-reboot').tap()
  await expect(dock.getByTestId('confirm')).toContainText('Redémarrer « pc-demo »')
  await dock.getByTestId('confirm-ok').tap()
  await expect(dock.getByTestId('toast')).toContainText('Redémarrage demandé')
  // Pas de second ordre par mégarde : toutes les commandes sont suspendues, la touchée dit « En cours… ».
  await expect(dock.getByTestId('power-reboot')).toContainText('En cours…')
  for (const id of ['lock', 'suspend', 'reboot', 'poweroff']) {
    await expect(dock.getByTestId(`power-${id}`)).toBeDisabled()
  }
  await shot(dock, '03-ordre-envoye', 'f')
  await expect(dock.getByTestId('power-reboot')).toBeEnabled({ timeout: 12_000 })
  await expect(dock.getByTestId('power-reboot')).toContainText('Redémarrer')
})

test('Alimentation : mettre en veille demande aussi confirmation', async ({ dock }) => {
  await openPower(dock)
  await dock.getByTestId('power-suspend').tap()
  await expect(dock.getByTestId('confirm')).toContainText('Mettre « pc-demo » en veille')
  await dock.getByTestId('confirm-ok').tap()
  await expect(dock.getByTestId('toast')).toContainText('Mise en veille demandée')
})

test('Alimentation : PC éteint, les commandes sont grisées et le réveil reste possible', async ({ dock, stack }) => {
  await openPower(dock)
  stack.pcOff()
  await expect(dock.getByTestId('power-offline')).toBeVisible({ timeout: 20_000 })
  for (const id of ['lock', 'suspend', 'reboot', 'poweroff']) {
    await expect(dock.getByTestId(`power-${id}`)).toBeDisabled()
    await expect(dock.getByTestId(`power-why-${id}`)).toHaveText('PC hors ligne')
  }
  await expect(dock.getByTestId('wake-power')).toBeEnabled()
  await shot(dock, '04-pc-eteint', 'f')

  stack.pcOn()
  await expect(dock.getByTestId('power-poweroff')).toBeEnabled({ timeout: 30_000 })
  await expect(dock.getByTestId('power-offline')).toHaveCount(0)
})

// --- Réglages : pages visibles et ordre ------------------------------------------------------------------

/** Les boutons de l'accueil dans l'ordre : on repasse par l'accueil si une page est affichée. */
async function tilesShown(page: Page): Promise<string[]> {
  if (!(await page.getByTestId('home').isVisible())) await goHome(page)
  return tileOrder(page)
}

async function openPagesEditor(dock: Page): Promise<void> {
  await openPage(dock, 'settings')
  await dock.getByTestId('choose-pages').tap()
  await expect(dock.getByTestId('pages-editor')).toBeVisible()
}

const DEFAULT_TILES = ['music', 'sound', 'apps', 'steam', 'office', 'markets', 'system', 'media', 'settings']

test('Réglages : cacher une page retire son bouton de l’accueil, et le dock s’en souvient après un rechargement', async ({ dock }) => {
  expect(await tilesShown(dock)).toEqual(DEFAULT_TILES)
  await openPagesEditor(dock)
  await dock.getByTestId('page-toggle-steam').tap()
  await dock.getByTestId('page-toggle-office').tap()
  await expect(dock.getByTestId('pages-summary')).toContainText('7 pages visibles sur 9')
  await expect(dock.getByTestId('page-row-steam')).toContainText('cachée')
  await shot(dock, '09-pages-cachees', 'f')
  await dock.getByTestId('pages-done').tap()
  await expect(dock.getByTestId('pages-count')).toHaveText('7 pages visibles sur 9 (cachées : Steam, Office).')
  await shot(dock, '10-reglages-pages', 'f')
  expect(await tilesShown(dock)).toEqual(['music', 'sound', 'apps', 'markets', 'system', 'media', 'settings'])

  await dock.reload() // c'est le Pi qui garde le choix, pas le navigateur
  await expect(dock.getByTestId('tile-settings')).toBeVisible()
  expect(await tilesShown(dock)).toEqual(['music', 'sound', 'apps', 'markets', 'system', 'media', 'settings'])
})

test('Réglages : monter une page déplace son bouton, « ordre d’origine » remet tout', async ({ dock }) => {
  await openPagesEditor(dock)
  await dock.getByTestId('page-up-media').tap()
  await dock.getByTestId('page-up-media').tap()
  await expect(dock.getByTestId('page-row-markets')).toContainText('7') // Bourse est descendue d'un cran
  expect(await tilesShown(dock)).toEqual(['music', 'sound', 'apps', 'steam', 'office', 'media', 'markets', 'system', 'settings'])
  await dock.getByTestId('pages-reset').tap()
  await expect.poll(() => tilesShown(dock)).toEqual(DEFAULT_TILES)
  await expect(dock.getByTestId('pages-reset')).toBeDisabled()
})

test('Réglages : on ne peut pas cacher les Réglages, il n’y aurait plus de retour', async ({ dock }) => {
  await openPagesEditor(dock)
  await expect(dock.getByTestId('page-toggle-settings')).toBeDisabled()
  for (const id of ['music', 'sound', 'apps', 'steam', 'office', 'markets', 'system', 'media']) {
    await dock.getByTestId(`page-toggle-${id}`).tap()
  }
  await expect(dock.getByTestId('pages-summary')).toContainText('1 page visible sur 9')
  expect(await tilesShown(dock)).toEqual(['settings'])
  await dock.getByTestId('pages-done').tap()
  await expect(dock.getByTestId('tile-settings')).toBeVisible() // il reste un bouton : celui des Réglages
  await dock.getByTestId('tile-settings').tap()
  await expect(dock.getByTestId('page-settings')).toBeVisible() // et la page reste utilisable
})

// --- Réglages : veille de l'écran ------------------------------------------------------------------------

async function centerOf(page: Page, testId: string): Promise<{ x: number; y: number }> {
  const box = await page.getByTestId(testId).boundingBox()
  if (!box) throw new Error(`${testId} est introuvable à l'écran`)
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

test('Réglages : la durée de veille se choisit, le dock la garde et la donne au script du Pi', async ({ dock, stack }) => {
  await openPage(dock, 'settings')
  await expect(dock.getByTestId('sleep-0')).toHaveAttribute('aria-pressed', 'true') // jamais, tant qu'on ne l'a pas demandé
  await expect(dock.getByTestId('screen-note')).toHaveText("L'écran reste allumé.")
  const read = async () => (await dock.request.get(new URL('/api/screen', stack.url).toString())).json()
  expect(await read()).toEqual({ sleep_after: 0 })

  await dock.getByTestId('sleep-300').tap()
  await expect(dock.getByTestId('sleep-300')).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.getByTestId('screen-note')).toContainText('Sans toucher pendant 5 minutes')
  expect(await read()).toEqual({ sleep_after: 300 }) // c'est ce que lit le script du Pi
  await shot(dock, '11-reglage-ecran', 'f')

  await dock.reload() // le Pi garde le choix, pas le navigateur
  await openPage(dock, 'settings')
  await expect(dock.getByTestId('sleep-300')).toHaveAttribute('aria-pressed', 'true')
})

test('Veille : l’écran noircit après l’inactivité, et le premier toucher ne déclenche rien', async ({ dock }) => {
  await dock.clock.install() // le temps est maintenant à nous : on avance d'une minute en un instant
  await openPage(dock, 'settings')
  await dock.getByTestId('sleep-60').tap()
  await expect(dock.getByTestId('sleep-60')).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.getByTestId('screen-sleep')).toHaveCount(0)

  await dock.clock.fastForward(30_000)
  await expect(dock.getByTestId('screen-sleep')).toHaveCount(0) // pas encore
  await dock.clock.fastForward(35_000)
  const veil = dock.getByTestId('screen-sleep')
  await expect(veil).toHaveAttribute('data-phase', 'asleep')
  await expect(veil).toHaveCSS('background-color', 'rgb(0, 0, 0)')
  await shot(dock, '12-ecran-eteint', 'f')

  // On touche l'écran noir juste là où se trouve le bouton « Jamais » (la page est défilée jusqu'aux durées de veille) :
  // il ne doit rien changer.
  const { x, y } = await centerOf(dock, 'sleep-0') // sous le voile : le centre reste lisible
  await dock.touchscreen.tap(x, y)
  await expect(veil).toHaveAttribute('data-phase', 'waking')
  await expect(veil).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)') // l'image est déjà revenue
  await dock.clock.fastForward(600)
  await expect(veil).toHaveCount(0)
  await expect(dock.getByTestId('page-settings')).toBeVisible()
  await expect(dock.getByTestId('sleep-60')).toHaveAttribute('aria-pressed', 'true') // le toucher n'a atteint rien d'autre que le voile

  // Le toucher suivant, lui, agit normalement.
  await openPage(dock, 'power')
  await expect(dock.getByTestId('page-power')).toBeVisible()
})

test('Veille : chaque toucher repousse l’extinction', async ({ dock }) => {
  await dock.clock.install()
  await openPage(dock, 'settings')
  await dock.getByTestId('sleep-60').tap()
  await expect(dock.getByTestId('sleep-60')).toHaveAttribute('aria-pressed', 'true')
  for (let i = 0; i < 3; i++) {
    await dock.clock.fastForward(45_000) // 135 s au total, mais jamais 60 s sans toucher
    await openPage(dock, 'power')
    await openPage(dock, 'settings')
  }
  await expect(dock.getByTestId('screen-sleep')).toHaveCount(0)
  await dock.clock.fastForward(65_000)
  await expect(dock.getByTestId('screen-sleep')).toHaveAttribute('data-phase', 'asleep')
})

test('Veille : « éteindre maintenant » sert d’essai, et un toucher rallume', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('sleep-now').tap()
  const veil = dock.getByTestId('screen-sleep')
  await expect(veil).toHaveAttribute('data-phase', 'asleep')
  await dock.touchscreen.tap(500, 300)
  await expect(veil).toHaveCount(0, { timeout: 3_000 })
  await expect(dock.getByTestId('page-settings')).toBeVisible()
})

test('Veille : sans réglage choisi, l’écran ne s’éteint jamais tout seul', async ({ dock }) => {
  await dock.clock.install()
  await openPage(dock, 'power')
  await dock.clock.fastForward(3 * 3_600_000)
  await expect(dock.getByTestId('screen-sleep')).toHaveCount(0)
  await expect(dock.getByTestId('power-card')).toBeVisible()
})
