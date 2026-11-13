// Étape l : Retrogame — les jeux d'arcade de MAME sur l'écran du dock, à la manette Bluetooth, avec un bouton physique.
// Vrai Chromium 1024×600 tactile -> vrai dock (liste de faux jeux, fausses manettes : `--demo-retro`) -> assistant de lecture
// en démonstration. Le bouton physique et le toucher de l'écran d'un jeu sont, en démo, deux fichiers créés dans le dossier
// d'exécution de l'assistant (`stack.runDir`). Rien ne sort de cette machine.

import type { Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { expect, goHome, openPage, shot, test, typeOnKeyboard } from './fixtures'

const noScroll = (page: Page) =>
  page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))

/** Ouvre Divertissement → Retrogame : la liste des jeux. */
async function openGames(page: Page): Promise<void> {
  await openPage(page, 'media')
  await page.getByTestId('media-games').tap()
  await expect(page.getByTestId('games-list')).toBeVisible()
  await expect(page.locator('[data-testid^=game-]:not([data-testid=games-list])').first()).toBeVisible()
}

/** L'assistant de lecture de l'écran est-il là ? Lancer un jeu ne dit « MAME est absent » qu'avant sa connexion. */
async function helperReady(page: Page): Promise<void> {
  await openGames(page)
  await expect(page.getByTestId('games-blocker')).toHaveCount(0)
}

const MINIMAL_ZIP = Buffer.from('504b0506' + '00'.repeat(18), 'hex') // une archive ZIP vide mais valide

test('Divertissement : le menu propose Freetv, Kiosque et Retrogame, sans rien qui déborde', async ({ dock }) => {
  await openPage(dock, 'media')
  await expect(dock.getByTestId('media-tv')).toContainText('Freetv')
  await expect(dock.getByTestId('media-files')).toContainText('Kiosque')
  await expect(dock.getByTestId('media-games')).toContainText('Retrogame')
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  const boxes = await Promise.all(['media-tv', 'media-files', 'media-games'].map(async (id) => (await dock.getByTestId(id).boundingBox())!))
  for (const box of boxes) {
    expect(box.x + box.width, 'dans l’écran').toBeLessThanOrEqual(1024)
    expect(box.height, 'un gros bouton').toBeGreaterThanOrEqual(240)
  }
  await dock.waitForTimeout(300)
  await shot(dock, '01-divertissement-trois-entrees', 'l')
})

test('Retrogame : la liste montre les jeux (noms entiers, année, éditeur, capture) et l’état de la manette', async ({ dock }) => {
  await openGames(dock)
  await expect(dock.getByTestId('games-count')).toContainText('12 jeux')
  await expect(dock.getByTestId('game-pacman')).toContainText('Pac-Man (Midway)')
  await expect(dock.getByTestId('game-pacman')).toContainText('1980 · Namco')
  await expect(dock.getByTestId('game-sf2')).toContainText('Street Fighter II: The World Warrior (World 910522)') // le nom entier, jamais le nom MAME
  await expect(dock.getByTestId('game-sf2')).toContainText('émulation imparfaite')
  await expect(dock.getByTestId('game-pacman').locator('img')).toHaveCount(1)
  await expect(dock.getByTestId('game-defender').locator('img')).toHaveCount(0) // sans capture : une manette à la place
  await expect.poll(() => dock.getByTestId('game-pacman').locator('img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0)
  await expect(dock.getByTestId('pad-pill')).toHaveAttribute('data-connected', 'true')
  await expect(dock.getByTestId('pad-pill-label')).toHaveText('Manette connectée')
  await expect(dock.getByTestId('pad-pill-detail')).toHaveText('Xbox Wireless Controller · 78 %')
  await expect(dock.getByTestId('games-nopad')).toHaveCount(0)
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await dock.waitForTimeout(400)
  await shot(dock, '02-retrogame-liste', 'l')
})

test('Retrogame : la recherche au clavier tactile trouve par le nom, l’éditeur ou l’année, sans accents', async ({ dock }) => {
  await openGames(dock)
  await dock.getByTestId('games-search').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  await typeOnKeyboard(dock, 'pac man') // « pac » seul trouverait aussi Space Invaders
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('games-count')).toContainText('2 jeux pour « pac man »')
  await expect(dock.getByTestId('game-pacman')).toBeVisible()
  await expect(dock.getByTestId('game-mspacman')).toBeVisible()
  await expect(dock.getByTestId('game-galaga')).toHaveCount(0)
  await shot(dock, '03-retrogame-recherche', 'l')
  await dock.getByTestId('games-clear').tap()
  await expect(dock.getByTestId('games-count')).toContainText('12 jeux')

  await dock.getByTestId('games-search').tap()
  await typeOnKeyboard(dock, 'capcom')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.locator('[data-testid^=game-]:not([data-testid=games-list]):not([data-testid=games-count])')).toHaveCount(2)
  await dock.getByTestId('games-clear').tap()

  await dock.getByTestId('games-search').tap()
  await typeOnKeyboard(dock, 'zzzz')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('games-none')).toContainText('Aucun jeu ne correspond.')
})

test('Retrogame : toucher un jeu le lance ; l’écran dit « En jeu » et ne propose aucun bouton ; l’accueil le dit aussi', async ({ dock }) => {
  await helperReady(dock)
  await dock.getByTestId('game-pacman').tap()

  const now = dock.getByTestId('game-now')
  await expect(now).toBeVisible()
  await expect(dock.getByTestId('game-now-title')).toHaveText('Pac-Man (Midway)')
  await expect(now).toHaveAttribute('data-state', 'starting')
  await expect(dock.getByTestId('game-now-state')).toHaveText('Lancement de MAME…')
  await shot(dock, '04-jeu-lancement', 'l')
  await expect(now).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await expect(dock.getByTestId('game-now-state')).toHaveText('En jeu')
  await expect(dock.getByTestId('game-now-hint')).toContainText('Touche l’écran pour quitter')
  // Aucun bouton « Quitter » ni « Revenir au jeu », pas de télécommande : la page n'a que sa grande zone tactile (le toucher quitte,
  // comme le bouton physique et Select + Start tenus). Le jeu masque la liste : rien d'autre à toucher que l'en-tête de l'application.
  await expect(dock.getByTestId('page-media').getByRole('button')).toHaveCount(1)
  await expect(dock.getByTestId('remote')).toHaveCount(0)
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await shot(dock, '05-jeu-en-cours', 'l')

  await goHome(dock)
  await expect(dock.getByTestId('tile-media-sub')).toHaveText('En jeu : Pac-Man (Midway)')
  await dock.getByTestId('tile-media').tap()
  await expect(dock.getByTestId('game-now')).toBeVisible() // la page s'ouvre sur le jeu tant qu'il joue
})

test('Retrogame : un toucher de l’écran quitte le jeu et ramène à la liste des jeux, sans erreur', async ({ dock, stack }) => {
  await helperReady(dock)
  await dock.getByTestId('game-galaga').tap()
  await expect(dock.getByTestId('game-now')).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  fs.writeFileSync(path.join(stack.runDir, 'mydock-demo-touch'), '') // l'assistant lit le toucher de l'écran (evdev) : en démo, un fichier
  await expect(dock.getByTestId('game-now')).toHaveCount(0)
  await expect(dock.getByTestId('games-list')).toBeVisible() // la liste de Retrogame, pas le menu ni une télécommande
  await expect(dock.getByTestId('media-error')).toHaveCount(0)
  await expect(dock.getByTestId('remote')).toHaveCount(0)
  await goHome(dock)
  await expect(dock.getByTestId('tile-media-sub')).toHaveCount(0)
})

test('Retrogame : le bouton physique quitte le jeu, et rallume l’écran quand il dort', async ({ dock, stack }) => {
  await helperReady(dock)
  await dock.getByTestId('game-dkong').tap()
  await expect(dock.getByTestId('game-now')).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  fs.writeFileSync(path.join(stack.runDir, 'mydock-demo-button'), '')
  await expect(dock.getByTestId('game-now')).toHaveCount(0)
  await expect(dock.getByTestId('games-list')).toBeVisible()

  // Écran éteint (le noir de la veille) : un appui le rallume, comme un toucher.
  await openPage(dock, 'settings')
  await dock.getByTestId('sleep-now').tap()
  await expect(dock.getByTestId('screen-sleep')).toBeVisible()
  fs.writeFileSync(path.join(stack.runDir, 'mydock-demo-button'), '')
  await expect(dock.getByTestId('screen-sleep')).toHaveCount(0, { timeout: 5_000 })
})

test('Retrogame : un jeu auquel il manque des ROMs revient à la liste avec une phrase claire', async ({ dock }) => {
  await helperReady(dock)
  await dock.getByTestId('game-mslug').tap()
  await expect(dock.getByTestId('media-error')).toBeVisible({ timeout: 10_000 })
  await expect(dock.getByTestId('media-error')).toContainText('Le jeu n’a pas pu se lancer')
  await expect(dock.getByTestId('media-error-text')).toContainText('incomplet')
  await expect(dock.getByTestId('media-error-text')).toContainText('mslug.zip')
  await expect(dock.getByTestId('games-list')).toBeVisible() // on est revenu à la liste, l'erreur au-dessus
  await expect(dock.getByTestId('game-now')).toHaveCount(0)
  await shot(dock, '06-jeu-rom-manquante', 'l')
  await dock.getByTestId('media-error-ok').tap()
  await expect(dock.getByTestId('media-error')).toHaveCount(0)
})

test('Retrogame : sans manette, l’avertissement ne bloque pas le lancement', async ({ dock }) => {
  await helperReady(dock)
  await dock.getByTestId('games-pads').tap()
  for (const id of ['AA:BB:CC:00:00:01', 'AA:BB:CC:00:00:02']) {
    await dock.getByTestId(`pad-forget-${id}`).tap()
    await expect(dock.getByTestId('confirm')).toBeVisible()
    await dock.getByTestId('confirm-ok').tap()
    await expect(dock.getByTestId(`pad-${id}`)).toHaveCount(0)
  }
  await expect(dock.getByTestId('pads-none')).toBeVisible()
  await dock.getByTestId('media-back').tap()
  await expect(dock.getByTestId('pad-pill')).toContainText('Aucune manette')
  await expect(dock.getByTestId('pad-pill')).toHaveAttribute('data-connected', 'false')
  await expect(dock.getByTestId('games-nopad')).toContainText('Aucune manette connectée')
  await shot(dock, '07-retrogame-sans-manette', 'l')
  await dock.getByTestId('game-pacman').tap()
  await expect(dock.getByTestId('game-now')).toBeVisible() // le jeu se lance quand même
  await dock.getByTestId('game-now').click({ delay: 2_000 }) // un toucher de la page (MAME ne la recouvre pas en démo) le quitte
  await expect(dock.getByTestId('games-list')).toBeVisible()
})

test('Manettes : la liste (connectée avec sa batterie, hors ligne), l’oubli après confirmation', async ({ dock }) => {
  await openGames(dock)
  await dock.getByTestId('games-pads').tap()
  await expect(dock.getByTestId('pads-paired')).toBeVisible()
  const xbox = dock.getByTestId('pad-AA:BB:CC:00:00:01')
  await expect(xbox).toContainText('Xbox Wireless Controller')
  await expect(xbox).toContainText('Connectée')
  await expect(dock.getByTestId('pad-battery-AA:BB:CC:00:00:01')).toHaveText('78 %')
  const dual = dock.getByTestId('pad-AA:BB:CC:00:00:02')
  await expect(dual).toContainText('DualSense Wireless Controller')
  await expect(dual).toContainText('Hors ligne')
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await dock.waitForTimeout(300)
  await shot(dock, '08-manettes-liste', 'l')

  await dock.getByTestId('pad-forget-AA:BB:CC:00:00:02').tap()
  await expect(dock.getByTestId('confirm')).toBeVisible()
  await dock.getByTestId('confirm-cancel').tap() // annuler ne change rien
  await expect(dual).toBeVisible()
  await dock.getByTestId('pad-forget-AA:BB:CC:00:00:02').tap()
  await dock.getByTestId('confirm-ok').tap()
  await expect(dual).toHaveCount(0)
  await expect(xbox).toBeVisible()
})

test('Manettes : « Associer une manette » cherche 30 s, ne montre que des manettes et l’association se suit à l’écran', async ({ dock }) => {
  await openGames(dock)
  await dock.getByTestId('games-pads').tap()
  await dock.getByTestId('pads-scan').tap()
  await expect(dock.getByTestId('pads-scanning')).toBeVisible()
  await expect(dock.getByTestId('pads-scanning')).toContainText('PS4')
  await expect(dock.getByTestId('pads-scanning')).toContainText('Xbox')
  await expect(dock.getByTestId('pads-scanning')).toContainText('8BitDo')
  await expect(dock.getByTestId('pads-scanning')).toContainText('Switch Pro')
  const left = Number.parseInt((await dock.getByTestId('pads-left').textContent()) ?? '0', 10)
  expect(left).toBeGreaterThan(24)
  expect(left).toBeLessThanOrEqual(30)
  const found = dock.getByTestId('pad-pair-AA:BB:CC:00:00:03') // la 8BitDo, trouvée au bout de deux secondes
  await expect(found).toBeVisible({ timeout: 8_000 })
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await shot(dock, '09-manettes-recherche', 'l')

  await found.tap()
  await expect(dock.getByTestId('pads-pairing')).toBeVisible()
  await expect(dock.getByTestId('pads-pairing')).toContainText('8BitDo Pro 2')
  await expect(dock.getByTestId('pads-pairing')).toHaveAttribute('data-state', 'done', { timeout: 8_000 })
  await expect(dock.getByTestId('pads-pairing')).toContainText('associée et connectée')
  await expect(dock.getByTestId('pads-scanning')).toHaveCount(0) // l'association arrête la recherche
  await expect(dock.getByTestId('pad-AA:BB:CC:00:00:03')).toContainText('Connectée')
  await shot(dock, '10-manettes-associee', 'l')
})

test('Envoyer des jeux : l’écran montre un code, la page /jeux du portail le demande puis reçoit un jeu', async ({ dock, stack, context }) => {
  await openGames(dock)
  await dock.getByTestId('games-upload').tap()
  const code = (await dock.getByTestId('upload-code').textContent())!.trim()
  expect(code).toMatch(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/)
  await expect(dock.getByTestId('upload-address')).toContainText('/jeux')
  await expect(dock.getByTestId('upload-status')).toContainText('Aucun jeu reçu')
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await shot(dock, '11-envoi-code', 'l')

  // Le PC : la vraie page du portail, avec son vrai script et sa vraie politique de sécurité.
  const pc = await context.newPage()
  const problems: string[] = []
  // Chromium écrit en console chaque réponse 4xx d'une requête : les deux refus que ce test provoque exprès n'en sont pas.
  const expected = /Failed to load resource: the server responded with a status of (400|403|415)/
  pc.on('console', (message) => ['error', 'warning'].includes(message.type()) && !expected.test(message.text()) && problems.push(message.text()))
  pc.on('pageerror', (error) => problems.push(error.message))
  await pc.goto(`${stack.portal}/jeux`)
  await expect(pc.locator('h1')).toHaveText('Retrogame')
  await pc.locator('#code').fill('ZZZZ-ZZZZ')
  await pc.getByRole('button', { name: 'Continuer' }).click()
  await expect(pc.locator('#msg')).toContainText('Code incorrect')
  await pc.locator('#code').fill(code)
  await pc.getByRole('button', { name: 'Continuer' }).click()
  await expect(pc.locator('#send-card')).toBeVisible()
  await pc.locator('#pick').setInputFiles({ name: 'PacMan.zip', mimeType: 'application/zip', buffer: MINIMAL_ZIP })
  await expect(pc.locator('#list li')).toContainText('envoyé ✔', { timeout: 10_000 })
  await pc.locator('#pick').setInputFiles({ name: 'virus.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ') })
  await expect(pc.locator('#list li').nth(1)).toContainText('nom refusé') // refusé par la page avant tout envoi
  await pc.locator('#pick').setInputFiles({ name: 'faux.zip', mimeType: 'application/zip', buffer: Buffer.from('MZ pas une archive') })
  await expect(pc.locator('#list li').nth(2)).toContainText('pas une archive') // refusé par le dock : le nom est bon, le contenu non
  expect(problems, 'la page du PC ne produit aucune erreur ni violation de sécurité').toEqual([])
  await pc.close()

  await expect(dock.getByTestId('upload-status')).toContainText('1 jeu reçu')
  expect(fs.readdirSync(path.join(stack.stateDir, 'roms'))).toEqual(['pacman.zip']) // le nom est celui de MAME, en minuscules
  await dock.getByTestId('upload-done').tap()
  await expect(dock.getByTestId('games-list')).toBeVisible()
  // La session est fermée : le même code ne vaut plus rien.
  const again = await context.request.post(`${stack.portal}/api/retro/login`, { data: { code } })
  expect(again.status()).toBe(409)
})

test('Envoyer des jeux : sans le code, la page ne laisse rien écrire', async ({ dock, stack, context }) => {
  const nobody = await context.request.post(`${stack.portal}/api/retro/upload?name=pacman.zip`, { data: MINIMAL_ZIP })
  expect(nobody.status()).toBe(409) // aucune session ouverte
  await openGames(dock)
  await dock.getByTestId('games-upload').tap()
  await expect(dock.getByTestId('upload-code')).toBeVisible()
  const noToken = await context.request.post(`${stack.portal}/api/retro/upload?name=pacman.zip`, { data: MINIMAL_ZIP })
  expect(noToken.status()).toBe(401)
  expect(fs.existsSync(path.join(stack.stateDir, 'roms', 'pacman.zip'))).toBe(false)
})

test('Réglages : le dossier des jeux, la broche du bouton et ce que l’assistant en dit', async ({ dock }) => {
  await openPage(dock, 'settings')
  const card = dock.getByTestId('settings-retro')
  await card.scrollIntoViewIfNeeded()
  await expect(dock.getByTestId('retro-pin')).toHaveText('GPIO17 (broche 11)')
  await expect(dock.getByTestId('retro-wiring')).toContainText('broche 11')
  await expect(dock.getByTestId('retro-wiring')).toContainText('masse')
  await expect(dock.getByTestId('retro-gpio-report')).toContainText('GPIO17 (broche 11)')
  await expect(dock.getByTestId('retro-summary')).toContainText('12 jeux')
  await shot(dock, '12-reglages-retrogame', 'l')

  await dock.getByTestId('retro-pin-up').tap()
  await expect(dock.getByTestId('retro-pin')).toHaveText('GPIO18 (broche 12)')
  await expect(dock.getByTestId('retro-gpio-report')).toContainText('GPIO18 (broche 12)', { timeout: 5_000 }) // l'assistant a reçu la nouvelle broche
  await dock.getByTestId('retro-pin-down').tap()
  await expect(dock.getByTestId('retro-pin')).toHaveText('GPIO17 (broche 11)')

  await dock.getByTestId('edit-retro-dir').tap()
  await typeOnKeyboard(dock, '/srv/jeux')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('retro-dir')).toHaveText('/srv/jeux')
  await dock.reload() // le dock garde le choix, pas le navigateur
  await openPage(dock, 'settings')
  await expect(dock.getByTestId('retro-dir')).toHaveText('/srv/jeux')
})
