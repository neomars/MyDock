// Tests de bout en bout : vrai navigateur 1024x600 tactile -> vrai serveur dock -> vrai agent (démo).

import type { Page } from '@playwright/test'
import { clearKeyboard, expect, goHome, openPage, shot, test, typeOnKeyboard } from './fixtures'

const TABS: [string, string][] = [
  ['music', 'Spotify'],
  ['sound', 'Son'],
  ['apps', 'Applis'],
  ['steam', 'Steam'],
  ['office', 'Office'],
  ['markets', 'Bourse'],
  ['system', 'Système'],
  ['media', 'Divertissement'],
  ['settings', 'Réglages'],
]

/** Les pages à parcourir pour les cibles tactiles : les neuf de l'accueil, plus l'alimentation (petit bouton de la barre du haut). */
const ALL_PAGES = [...TABS.map(([id]) => id), 'power']

/** Éléments cliquables visibles dont le plus petit côté est sous 48 px. */
async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll('button')]
      .map((el) => ({ el, box: el.getBoundingClientRect() }))
      .filter(({ el, box }) => box.width > 0 && box.height > 0 && !(el as HTMLButtonElement).disabled)
      .filter(({ box }) => Math.min(box.width, box.height) < 48)
      .map(({ el, box }) => `${(el.textContent ?? '').trim().slice(0, 30)} (${Math.round(box.width)}×${Math.round(box.height)})`),
  )
}

const noScroll = (page: Page) =>
  page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))

test('démarre connecté sur l’accueil (gros boutons), en 1024×600 sans défilement de la page', async ({ dock }) => {
  expect(dock.viewportSize()).toEqual({ width: 1024, height: 600 })
  await expect(dock.getByTestId('pc-label')).toHaveText('pc-demo')
  await expect(dock.getByTestId('demo-chip')).toBeVisible()
  await expect(dock.getByTestId('home-grid')).toBeVisible()
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })

  await openPage(dock, 'system')
  await expect(dock.getByTestId('system-host')).toContainText('pc-demo')
  await expect(dock.getByTestId('system-host')).toHaveAttribute('title', /Ubuntu 26\.04 LTS \(démo\)/)
  await expect(dock.getByTestId('uptime')).toContainText('3 h')
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
})

test('les neuf boutons de l’accueil ouvrent leur page, et le bouton de la barre du haut y ramène', async ({ dock }) => {
  for (const [id, label] of TABS) {
    const tile = dock.getByTestId(`tile-${id}`)
    await expect(tile).toContainText(label)
    await tile.tap()
    await expect(dock.getByTestId(`page-${id}`)).toBeVisible()
    await goHome(dock)
  }
})

test('toutes les cibles tactiles font au moins 48 px, clavier compris', async ({ dock }) => {
  expect(await smallTargets(dock), 'accueil').toEqual([])
  for (const id of ALL_PAGES) {
    await openPage(dock, id)
    await expect(dock.getByTestId(`page-${id}`)).toBeVisible()
    await dock.waitForTimeout(150)
    expect(await smallTargets(dock), `page ${id}`).toEqual([])
  }
  await openPage(dock, 'settings')
  await dock.getByTestId('edit-token').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  expect(await smallTargets(dock), 'clavier tactile').toEqual([])
})

test('après un rechargement, on repart toujours de l’accueil', async ({ dock }) => {
  await openPage(dock, 'markets')
  await expect(dock.getByTestId('markets-status')).toBeVisible()
  await dock.reload()
  await expect(dock.getByTestId('home')).toBeVisible()
  await expect(dock.locator('main.page')).toHaveCount(0)
})

// --- panne et reprise --------------------------------------------------------------------------

test('PC éteint : écran hors ligne, réveil Wake-on-LAN, reconnexion automatique', async ({ dock, stack }) => {
  await openPage(dock, 'system')
  stack.pcOff()
  await expect(dock.getByTestId('offline-title')).toHaveText('PC hors ligne', { timeout: 20_000 })
  await expect(dock.getByTestId('offline-detail')).toContainText('éteint, en veille')
  // Le message nomme l'adresse essayée : c'est ce qui permet de comprendre une panne à distance.
  await expect(dock.getByTestId('offline-detail')).toContainText(/127\.0\.0\.1:\d+/)
  await expect(dock.getByTestId('wake')).toBeVisible()
  await expect(dock.getByTestId('clock')).toHaveText(/^\d{2}:\d{2}$/)
  await shot(dock, '06-hors-ligne')

  await dock.getByTestId('wake').tap()
  await expect(dock.getByTestId('toast')).toContainText('Paquet de réveil envoyé')
  await expect(dock.getByTestId('offline-waking')).toBeVisible()
  await expect.poll(() => stack.wolPackets.length).toBe(1)
  const packet = stack.wolPackets[0]
  expect(packet).toHaveLength(102)
  expect([...packet.subarray(0, 6)]).toEqual([255, 255, 255, 255, 255, 255])
  const mac = Buffer.from('02005e100001', 'hex')
  for (let i = 0; i < 16; i++) expect(packet.subarray(6 + i * 6, 12 + i * 6)).toEqual(mac)
  await shot(dock, '07-reveil-envoye')

  stack.pcOn()
  await expect(dock.getByTestId('system-host')).toBeVisible({ timeout: 40_000 })
  await expect(dock.getByTestId('offline')).toHaveCount(0)
  await expect(dock.getByTestId('pc-label')).toHaveText('pc-demo')
})

test('agent arrêté seul : message dédié, sans proposer de réveil', async ({ dock, stack }) => {
  await openPage(dock, 'system')
  stack.agentDown()
  await expect(dock.getByTestId('offline-title')).toHaveText('PC allumé, agent arrêté', { timeout: 20_000 })
  await expect(dock.getByTestId('offline-detail')).toContainText("n'écoute pas")
  await expect(dock.getByTestId('offline-detail')).toContainText(/127\.0\.0\.1:\d+/)
  await expect(dock.getByTestId('wake')).toHaveCount(0)
  await shot(dock, '08-agent-arrete')
  stack.pcOn()
  await expect(dock.getByTestId('system-host')).toBeVisible({ timeout: 30_000 })
})

test('hors ligne, Bourse et Réglages restent utilisables ; les pages du PC montrent l’écran hors ligne', async ({
  dock,
  stack,
}) => {
  await openPage(dock, 'system')
  stack.pcOff()
  await expect(dock.getByTestId('offline-title')).toBeVisible({ timeout: 20_000 })
  await openPage(dock, 'markets')
  await expect(dock.getByTestId('markets-list')).toBeVisible()
  await expect(dock.getByTestId('price-crypto:BTC')).not.toHaveText('…', { timeout: 10_000 }) // les cotations ne viennent pas du PC
  await expect(dock.getByTestId('offline')).toHaveCount(0)
  await openPage(dock, 'settings')
  await expect(dock.getByTestId('settings-pc')).toBeVisible()
  await expect(dock.getByTestId('pc-resolved')).toContainText('Dernière adresse essayée')
  await expect(dock.getByTestId('pc-resolved')).toContainText(/127\.0\.0\.1:\d+/)
  await openPage(dock, 'power')
  await expect(dock.getByTestId('wol-card')).toBeVisible()
  await openPage(dock, 'music')
  await expect(dock.getByTestId('offline')).toBeVisible()
})

test('l’écran hors ligne ne clignote pas pendant les nouvelles tentatives', async ({ dock, stack }) => {
  await openPage(dock, 'system')
  stack.pcOff()
  const title = dock.getByTestId('offline-title')
  await expect(title).toHaveText('PC hors ligne', { timeout: 20_000 })
  const seen = new Set<string>()
  for (let i = 0; i < 24; i++) {
    seen.add((await title.innerText()).trim())
    await dock.waitForTimeout(500)
  }
  expect([...seen]).toEqual(['PC hors ligne'])
})

// --- Réglages ---------------------------------------------------------------------------------

test('Réglages : montre l’adresse du PC jointe et celle du Pi', async ({ dock }) => {
  await openPage(dock, 'settings')
  await expect(dock.getByTestId('pc-resolved')).toContainText('Connecté à')
  await expect(dock.getByTestId('pc-resolved')).toContainText(/127\.0\.0\.1:\d+/)
  await expect(dock.getByTestId('pi-ips')).toHaveText(/^(\d{1,3}\.){3}\d{1,3}( · (\d{1,3}\.){3}\d{1,3})*$|^—$/)
})

test('Réglages : saisir l’adresse du PC au clavier tactile', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('edit-host').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  await expect(dock.getByTestId('keyboard-value')).toHaveText('127.0.0.1')
  await shot(dock, '05-clavier')

  await clearKeyboard(dock)
  await typeOnKeyboard(dock, 'ubuntu.local')
  await expect(dock.getByTestId('keyboard-value')).toHaveText('ubuntu.local')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('toast')).toContainText('Adresse enregistrée')
  await expect(dock.getByTestId('pc-host')).toHaveText('ubuntu.local')

  // On remet l'adresse du PC de démonstration : le dock se reconnecte tout seul.
  await dock.getByTestId('edit-host').tap()
  await clearKeyboard(dock)
  await typeOnKeyboard(dock, '127.0.0.1')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('pc-host')).toHaveText('127.0.0.1')
  await expect(dock.getByTestId('pc-label')).toHaveText('pc-demo', { timeout: 20_000 })
})

test('Réglages : une adresse invalide est refusée avec une explication', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('edit-host').tap()
  await clearKeyboard(dock)
  await typeOnKeyboard(dock, 'pc')
  await dock.getByTestId('keyboard').getByRole('button', { name: '.', exact: true }).tap()
  await dock.getByTestId('keyboard').getByRole('button', { name: '.', exact: true }).tap()
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('toast')).toContainText('pas un nom d')
  await expect(dock.getByTestId('pc-host')).toHaveText('127.0.0.1') // rien n'a changé
})

test('Réglages : le jeton se saisit masqué et un jeton trop court est refusé', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('edit-token').tap()
  await typeOnKeyboard(dock, 'court')
  await expect(dock.getByTestId('keyboard-value')).toHaveText('•••••')
  await dock.getByTestId('keyboard').getByRole('button', { name: 'Afficher' }).tap()
  await expect(dock.getByTestId('keyboard-value')).toHaveText('court')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('toast')).toContainText('Jeton invalide')
  await expect(dock.getByTestId('pc-label')).toHaveText('pc-demo') // la liaison n'a pas bougé
})

test('Réglages : l’adresse MAC se tape avec ses deux-points automatiques et se revient en automatique', async ({
  dock,
}) => {
  await openPage(dock, 'settings')
  await expect(dock.getByTestId('wol-mac-setting')).toHaveText('02:00:5e:10:00:01')
  await dock.getByTestId('edit-mac').tap()
  await clearKeyboard(dock)
  await typeOnKeyboard(dock, '02aabbccdd05')
  await expect(dock.getByTestId('keyboard-value')).toHaveText('02:aa:bb:cc:dd:05')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('wol-mac-setting')).toHaveText('02:aa:bb:cc:dd:05')
  await expect(dock.getByTestId('settings-wol')).toContainText('Saisie à la main')
  await dock.getByTestId('auto-mac').tap()
  await expect(dock.getByTestId('wol-mac-setting')).toHaveText('02:00:5e:10:00:01')
})

test('Réglages : « Tester la connexion » confirme que l’agent répond', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('test-connection').tap()
  await expect(dock.getByTestId('test-result')).toContainText('Connexion réussie')
  await shot(dock, '02-reglages')
})

test('Réglages : la latence appui → PC mesurée est sous 50 ms', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('measure-latency').tap()
  const result = dock.getByTestId('latency-result')
  await expect(result).toContainText('sous 50 ms', { timeout: 30_000 })
  const text = await result.innerText()
  const p95 = Number(/PC\s*médiane [\d,]+ ms · p95 ([\d,]+) ms/.exec(text)?.[1].replace(',', '.'))
  expect(p95).toBeGreaterThan(0)
  expect(p95).toBeLessThan(50)
  await shot(dock, '04-latence')
})

test('Confirmation à l’écran : annuler n’exécute rien, confirmer exécute', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('try-confirm').tap()
  await expect(dock.getByTestId('confirm')).toContainText('Tester la confirmation')
  await expect(dock.getByTestId('confirm-timer')).toContainText('Expire dans')
  await shot(dock, '09-confirmation')
  await dock.getByTestId('confirm-cancel').tap()
  await expect(dock.getByTestId('toast')).toContainText('Confirmation annulée')

  await dock.getByTestId('try-confirm').tap()
  await dock.getByTestId('confirm-ok').tap()
  await expect(dock.getByTestId('toast')).toContainText('Action confirmée et exécutée')
  await expect(dock.getByTestId('confirm')).toHaveCount(0)
})

// --- Alimentation -----------------------------------------------------------------------------

test('Alimentation : le paquet Wake-on-LAN part avec la bonne adresse MAC', async ({ dock, stack }) => {
  await openPage(dock, 'power')
  await expect(dock.getByTestId('wol-mac')).toHaveText('02:00:5e:10:00:01')
  await expect(dock.getByTestId('wol-card')).toContainText('apprise automatiquement')
  await dock.getByTestId('wake-power').tap()
  await expect(dock.getByTestId('wake-sent')).toContainText('Envoyé il y a')
  await expect.poll(() => stack.wolPackets.length).toBe(1)
  await shot(dock, '03-alimentation')
})

// --- fluidité ---------------------------------------------------------------------------------

test('navigation sans tâche longue sous ralentissement du processeur ×4 (indicateur, pas une mesure sur Pi)', async ({
  dock,
}) => {
  const cdp = await dock.context().newCDPSession(dock)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 })
  await dock.evaluate(() => {
    const w = window as unknown as { __long: number[] }
    w.__long = []
    new PerformanceObserver((list) => w.__long.push(...list.getEntries().map((e) => e.duration))).observe({
      entryTypes: ['longtask'],
    })
  })
  for (const id of [...ALL_PAGES, ...ALL_PAGES]) {
    await openPage(dock, id)
    await expect(dock.getByTestId(`page-${id}`)).toBeVisible()
  }
  await dock.waitForTimeout(300)
  const durations = await dock.evaluate(() => (window as unknown as { __long: number[] }).__long)
  console.log(`tâches longues (ralentissement ×4) : ${durations.length}, la plus longue ${Math.max(0, ...durations).toFixed(0)} ms`)
  expect(Math.max(0, ...durations)).toBeLessThan(200)
})
