// Mise à jour du paquet pendant que l'écran est ouvert. Les pages (Musique, Son…) se chargent à la demande depuis
// des fichiers dont le nom change à chaque compilation : une interface restée ouverte pendant la mise à jour
// demandait des fichiers disparus, et l'onglet touché restait vide. Ici, la vraie pile (Chromium -> dock ->
// agent de démonstration) sert une copie jetable de l'interface que le test « met à jour » en renommant tous les
// fichiers à empreinte, comme le fait une nouvelle compilation.

import type { Page, WebSocketRoute } from '@playwright/test'
import { expect, openPage, rehashInterface, shot, test } from './fixtures'

test.use({ liveWeb: true })

type Marked = Window & { __avant?: boolean }

/** Les erreurs de console attendues quand un fichier a disparu : le navigateur les rapporte, c'est normal. */
const EXPECTED = /404|Failed to (load resource|fetch dynamically imported module)|Unable to preload CSS|MIME type|ERR_FAILED/

async function open(page: Page, url: string, problems: string[]): Promise<void> {
  page.on('console', (m) => {
    if (['error', 'warning'].includes(m.type()) && !EXPECTED.test(m.text())) problems.push(`${m.type()}: ${m.text()}`)
  })
  page.on('pageerror', (e) => {
    if (!EXPECTED.test(e.message)) problems.push(`exception: ${e.message}`)
  })
  await page.goto(url)
  await expect(page.getByTestId('home')).toBeVisible()
  await page.evaluate(() => ((window as Marked).__avant = true)) // la page « d'avant » : elle perd ce repère en se rechargeant
}

const wasReloaded = (page: Page) => page.evaluate(() => (window as Marked).__avant === undefined)

test('mise à jour : un bouton touché après coup ouvre sa page, l’écran se recharge de lui-même', async ({ page, stack }) => {
  const problems: string[] = []
  await open(page, stack.url, problems)

  rehashInterface(stack.webDir) // « apt install » vient de remplacer les fichiers
  await openPage(page, 'music')

  await expect(page.getByTestId('music-player').or(page.getByTestId('music-empty'))).toBeVisible()
  expect(await wasReloaded(page)).toBe(true)
  await expect(page.getByTestId('page-music')).toBeVisible() // la page touchée s'est rouverte d'elle-même, pas l'accueil
  await expect(page.getByTestId('page-failed')).toHaveCount(0)
  // Et l'interface neuve marche : les autres pages aussi.
  await openPage(page, 'sound')
  await expect(page.getByTestId('page-sound')).toBeVisible()
  await expect(page.getByTestId('page-failed')).toHaveCount(0)
  expect(problems).toEqual([])
})

test('mise à jour : le dock revient après son redémarrage, l’écran se met à jour sans qu’on y touche', async ({
  page,
  stack,
}) => {
  const problems: string[] = []
  let route: WebSocketRoute | null = null
  await page.routeWebSocket(/\/ws$/, (ws) => {
    ws.connectToServer() // transparent : le vrai dock répond
    route = ws
  })
  await open(page, stack.url, problems)

  rehashInterface(stack.webDir)
  await route!.close() // le dock redémarre (c'est ce que fait la mise à jour) : la liaison se coupe, puis revient

  await expect.poll(() => wasReloaded(page), { timeout: 15_000 }).toBe(true)
  await expect(page.getByTestId('home')).toBeVisible() // l'écran est reparti, sur la nouvelle interface
  expect(problems).toEqual([])
})

test('la liaison qui revient sans mise à jour ne recharge rien', async ({ page, stack }) => {
  const problems: string[] = []
  let route: WebSocketRoute | null = null
  let connections = 0
  await page.routeWebSocket(/\/ws$/, (ws) => {
    connections += 1
    ws.connectToServer()
    route = ws
  })
  await open(page, stack.url, problems)

  await route!.close()
  await expect.poll(() => connections, { timeout: 15_000 }).toBeGreaterThanOrEqual(2) // reconnecté
  await expect(page.getByTestId('home')).toBeVisible()
  expect(await wasReloaded(page)).toBe(false) // même page, rien n'a bougé
  expect(problems).toEqual([])
})

test('si la mise à jour ne règle rien, l’écran ne recharge pas en boucle : il dit ce qui manque', async ({
  page,
  stack,
}) => {
  const problems: string[] = []
  await open(page, stack.url, problems)
  // Un rechargement automatique vient d'avoir lieu : le suivant attendra.
  await page.evaluate(() => sessionStorage.setItem('mydock.reloaded', String(Date.now())))

  rehashInterface(stack.webDir)
  await openPage(page, 'music')

  await expect(page.getByTestId('page-failed')).toBeVisible()
  await expect(page.getByTestId('page-failed-detail')).toContainText('/assets/') // le fichier qui manque est nommé
  await shot(page, '08-page-introuvable', 'g')
  expect(await wasReloaded(page)).toBe(false)
  // Le bouton, lui, recharge pour de bon : c'est le geste de la personne, pas un automatisme. Et la page se rouvre.
  await page.getByTestId('page-reload').tap()
  await expect(page.getByTestId('music-player').or(page.getByTestId('music-empty'))).toBeVisible()
  expect(await wasReloaded(page)).toBe(true)
  expect(problems).toEqual([])
})

test('un fichier de page injoignable sans mise à jour : l’erreur est dite, « Recharger » retente', async ({ page, stack }) => {
  const problems: string[] = []
  await page.route('**/assets/Music-*.js', (route) => route.abort())
  await open(page, stack.url, problems)

  await openPage(page, 'music')
  await expect(page.getByTestId('page-failed')).toBeVisible()
  await expect(page.getByTestId('page-failed')).toContainText("Cette page n'a pas pu être chargée")
  expect(await wasReloaded(page)).toBe(false) // l'interface n'a pas changé : recharger tout de suite ne servirait à rien

  await page.unroute('**/assets/Music-*.js')
  await page.getByTestId('page-reload').tap()
  await expect(page.getByTestId('music-player').or(page.getByTestId('music-empty'))).toBeVisible()
  expect(problems).toEqual([])
})

test('une page qui plante sur des données inattendues le dit au lieu de vider l’écran', async ({ page, stack }) => {
  const problems: string[] = []
  // Un faux dock transparent, qui abîme seulement le thème « system » (un processeur sans mesures).
  await page.routeWebSocket(/\/ws$/, (ws) => {
    const server = ws.connectToServer()
    server.onMessage((raw) => {
      const message = JSON.parse(String(raw))
      if (message.t === 'snap' && message.topic === 'system') message.data = { ...message.data, cpu: null }
      ws.send(JSON.stringify(message))
    })
  })
  page.on('pageerror', (e) => problems.push(`exception: ${e.message}`))
  await page.goto(stack.url)
  await openPage(page, 'system')

  await expect(page.getByTestId('page-crashed')).toBeVisible()
  await expect(page.getByTestId('page-crashed-detail')).not.toBeEmpty() // le message de l'erreur, pas un écran muet
  await shot(page, '09-page-en-erreur', 'g')
  // Le reste de l'interface vit : on change de page, la suivante s'affiche normalement.
  await openPage(page, 'power')
  await expect(page.getByTestId('power-card')).toBeVisible()
  await expect(page.getByTestId('page-crashed')).toHaveCount(0)
  expect(problems).toEqual([])
})
