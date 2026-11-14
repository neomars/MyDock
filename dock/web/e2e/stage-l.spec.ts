// Étape l : Retrogame — les jeux d'arcade de MAME sur l'écran du dock, à la manette Bluetooth, avec un bouton physique.
// Vrai Chromium 1024×600 tactile -> vrai dock (liste de faux jeux, fausses manettes : `--demo-retro`) -> assistant de lecture
// en démonstration. Le bouton physique et le toucher de l'écran d'un jeu sont, en démo, deux fichiers créés dans le dossier
// d'exécution de l'assistant (`stack.runDir`). Rien ne sort de cette machine.

import type { BrowserContext, Page } from '@playwright/test'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'
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

/** Une vraie archive ZIP (un seul fichier, sans compression) de la taille voulue : pour envoyer un jeu de plusieurs Mo. */
function storedZip(entry: string, data: Buffer): Buffer {
  const name = Buffer.from(entry)
  const crc = zlib.crc32(data)
  const local = Buffer.alloc(30)
  local.writeUInt32LE(0x04034b50, 0)
  local.writeUInt16LE(20, 4)
  local.writeUInt32LE(crc, 14)
  local.writeUInt32LE(data.length, 18)
  local.writeUInt32LE(data.length, 22)
  local.writeUInt16LE(name.length, 26)
  const body = Buffer.concat([local, name, data])
  const central = Buffer.alloc(46)
  central.writeUInt32LE(0x02014b50, 0)
  central.writeUInt16LE(20, 4)
  central.writeUInt16LE(20, 6)
  central.writeUInt32LE(crc, 16)
  central.writeUInt32LE(data.length, 20)
  central.writeUInt32LE(data.length, 24)
  central.writeUInt16LE(name.length, 28)
  const directory = Buffer.concat([central, name])
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(1, 8)
  end.writeUInt16LE(1, 10)
  end.writeUInt32LE(directory.length, 12)
  end.writeUInt32LE(body.length, 16)
  return Buffer.concat([body, directory, end])
}

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

test('Manette filaire USB : branchée, elle est connectée sans rien associer ; débranchée, elle disparaît', async ({ dock, stack }) => {
  // Le dock de démonstration lit la liste des manettes USB « branchées » dans un fichier de son dossier d'état.
  const usb = path.join(stack.stateDir, 'demo-usb-pads.txt')
  await openGames(dock)
  await expect(dock.getByTestId('pad-pill-label')).toHaveText('Manette connectée') // la Xbox Bluetooth
  fs.writeFileSync(usb, 'Manette USB arcade\n')
  await expect(dock.getByTestId('pad-pill-label')).toHaveText('2 manettes connectées', { timeout: 8_000 })
  await expect(dock.getByTestId('pad-pill-detail')).toContainText('Manette USB arcade')

  await dock.getByTestId('games-pads').tap()
  const wired = dock.getByTestId('pads-wired')
  await expect(wired).toContainText('Manette USB arcade')
  await expect(wired).toContainText('Branchée en USB')
  await expect(wired.getByRole('button')).toHaveCount(0) // ni « Oublier » ni « Associer » : rien à faire
  await expect(dock.getByTestId('pads-paired')).not.toContainText('Manette USB arcade') // pas dans les manettes Bluetooth
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await dock.waitForTimeout(300)
  await shot(dock, '16-manette-usb', 'l')

  // Seule manette : l'en-tête la nomme (USB) et l'avertissement « aucune manette » ne s'affiche pas.
  for (const id of ['AA:BB:CC:00:00:01', 'AA:BB:CC:00:00:02']) {
    await dock.getByTestId(`pad-forget-${id}`).tap()
    await dock.getByTestId('confirm-ok').tap()
    await expect(dock.getByTestId(`pad-${id}`)).toHaveCount(0)
  }
  await dock.getByTestId('media-back').tap()
  await expect(dock.getByTestId('pad-pill-label')).toHaveText('Manette connectée')
  await expect(dock.getByTestId('pad-pill-detail')).toHaveText('Manette USB arcade · USB')
  await expect(dock.getByTestId('games-nopad')).toHaveCount(0)

  fs.rmSync(usb) // on la débranche
  await expect(dock.getByTestId('pad-pill-label')).toHaveText('Aucune manette', { timeout: 8_000 })
  await expect(dock.getByTestId('games-nopad')).toBeVisible()
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

/** Ouvre la page /jeux du portail comme le ferait le PC, en surveillant la console (hors refus HTTP provoqués exprès). */
async function pcPage(context: BrowserContext, portal: string, path = '/jeux') {
  const page = await context.newPage()
  const problems: string[] = []
  // Chromium écrit en console chaque réponse 4xx d'une requête : les refus que ces tests provoquent exprès n'en sont pas.
  const expected = /Failed to load resource: (the server responded with a status of (400|403|415|507)|net::ERR_(FAILED|CONNECTION_RESET))/
  page.on('console', (message) => ['error', 'warning'].includes(message.type()) && !expected.test(message.text()) && problems.push(message.text()))
  page.on('pageerror', (error) => problems.push(error.message))
  await page.goto(`${portal}${path}`)
  return { page, problems }
}

test('Envoyer des jeux depuis le PC associé : la page du portail reçoit un jeu sans code, sans toucher au Pi', async ({ dock, stack, context }) => {
  // Le PC de cette pile est « associé » (127.0.0.1) : c'est lui qui ouvre la page. Rien n'est ouvert sur l'écran du dock.
  const { page: home, problems: homeProblems } = await pcPage(context, stack.portal, '/')
  await expect(home.getByRole('link', { name: 'Envoyer des jeux' })).toBeVisible() // le lien est sur la page d'accueil du portail
  await home.getByRole('link', { name: 'Envoyer des jeux' }).scrollIntoViewIfNeeded()
  await shot(home, '13-portail-accueil-lien', 'l')
  await home.getByRole('link', { name: 'Envoyer des jeux' }).click()
  await expect(home).toHaveURL(/\/jeux$/)
  await expect(home.locator('h1')).toHaveText('Retrogame')
  await expect(home.locator('#send-card')).toBeVisible() // directement le choix des fichiers
  await expect(home.locator('#login-card')).toBeHidden() // aucun code à saisir
  await expect(home.locator('#note')).toContainText('aucun code à saisir')
  await shot(home, '14-page-pc-sans-code', 'l')

  await home.locator('#pick').setInputFiles({ name: 'PacMan.zip', mimeType: 'application/zip', buffer: MINIMAL_ZIP })
  await expect(home.locator('#list li')).toContainText('envoyé ✔', { timeout: 10_000 })
  await home.locator('#pick').setInputFiles({ name: 'virus.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ') })
  await expect(home.locator('#list li').nth(1)).toContainText('nom refusé')
  await home.locator('#pick').setInputFiles({ name: 'faux.zip', mimeType: 'application/zip', buffer: Buffer.from('MZ pas une archive') })
  await expect(home.locator('#list li').nth(2)).toContainText('pas une archive')
  await shot(home, '15-page-pc-envois', 'l')
  expect(homeProblems, 'la page du PC ne produit aucune erreur ni violation de sécurité').toEqual([])
  await home.close()

  expect(fs.readdirSync(path.join(stack.stateDir, 'roms'))).toEqual(['pacman.zip'])
  // Le jeu arrive dans la liste de l'écran, sans que personne n'ait touché au Pi.
  await openGames(dock)
  await expect(dock.getByTestId('game-pacman')).toBeVisible()
})

test('Envoyer des jeux : un gros jeu de plusieurs Mo arrive entier par la page du PC', async ({ stack, context }) => {
  const game = storedZip('rom.bin', Buffer.alloc(6 * 1024 * 1024, 7)) // plus de 6 Mo : bien au-delà des 256 Ko qu'une réponse de refus saurait vider
  const { page, problems } = await pcPage(context, stack.portal)
  await expect(page.locator('#send-card')).toBeVisible()
  await page.locator('#pick').setInputFiles({ name: 'tetrisp.zip', mimeType: 'application/zip', buffer: game })
  await expect(page.locator('#list li')).toContainText('envoyé ✔', { timeout: 30_000 })
  expect(fs.statSync(path.join(stack.stateDir, 'roms', 'tetrisp.zip')).size).toBe(game.length)
  expect(problems, 'aucune erreur dans la console de la page').toEqual([])
  await page.close()
})

test('Envoyer des jeux : un refus est annoncé avant l’envoi, avec son vrai message, et rien n’est envoyé', async ({ stack, context }) => {
  const { page } = await pcPage(context, stack.portal)
  await expect(page.locator('#send-card')).toBeVisible()
  const refusal = 'Pas assez de place sur la carte du Pi : 480,0 Mo libres, il en faut 514,0 Mo (14,0 Mo pour ce jeu et 500,0 Mo que le dock garde libres).'
  await page.route('**/api/retro/check', (route) =>
    route.fulfill({ status: 507, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'no_space', message: refusal }) }),
  )
  let sent = 0
  await page.route('**/api/retro/upload*', (route) => {
    sent += 1
    return route.abort()
  })
  await page.locator('#pick').setInputFiles({ name: 'tetrisp2.zip', mimeType: 'application/zip', buffer: storedZip('rom.bin', Buffer.alloc(2 * 1024 * 1024, 1)) })
  await expect(page.locator('#list li')).toContainText('480,0 Mo libres')
  await expect(page.locator('#list li span').nth(1)).toHaveClass('ko')
  await shot(page, '17-page-pc-refus-place', 'l')
  expect(sent, 'le fichier refusé n’est pas envoyé').toBe(0)
  expect(fs.existsSync(path.join(stack.stateDir, 'roms', 'tetrisp2.zip'))).toBe(false)
  await page.close()
})

test('Envoyer des jeux : si la connexion lâche pour de bon, la page redemande au dock pourquoi avant d’accuser le réseau', async ({ stack, context }) => {
  test.setTimeout(120_000) // la page réessaie six fois, avec une pause qui s'allonge, avant d'abandonner
  const { page } = await pcPage(context, stack.portal)
  await expect(page.locator('#send-card')).toBeVisible()
  const refusal = 'Pas assez de place sur la carte du Pi : 3,0 Mo libres, il en faut 503,0 Mo.'
  let checks = 0
  await page.route('**/api/retro/check', (route) => {
    checks += 1
    if (checks === 1) return route.continue() // avant l'envoi : tout va bien
    return route.fulfill({ status: 507, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'no_space', message: refusal }) })
  })
  await page.route('**/api/retro/upload*', (route) => route.abort('connectionreset')) // le dock coupe la connexion
  await page.locator('#pick').setInputFiles({ name: 'tetris.zip', mimeType: 'application/zip', buffer: storedZip('rom.bin', Buffer.alloc(1024 * 1024, 1)) })
  await expect(page.locator('#list li')).toContainText('3,0 Mo libres', { timeout: 40_000 })
  expect(checks).toBe(2)
  // Et quand le dock ne voit rien d'anormal, c'est bien le réseau qu'on soupçonne.
  await page.unroute('**/api/retro/check')
  await page.locator('#pick').setInputFiles({ name: 'tetris2.zip', mimeType: 'application/zip', buffer: storedZip('rom.bin', Buffer.alloc(1024 * 1024, 1)) })
  await expect(page.locator('#list li').nth(1)).toContainText('réseau ou Wi-Fi', { timeout: 40_000 })
  await expect(page.locator('#list li').nth(1)).toContainText('envoi interrompu à 0 %')
  await page.close()
})

test('Envoyer des jeux : un morceau coupé en route est renvoyé, et le jeu arrive entier', async ({ stack, context }) => {
  const game = storedZip('rom.bin', crypto.randomBytes(1024 * 1024)) // 1 Mo d'octets quelconques : tout trou ou doublon se verrait
  const { page } = await pcPage(context, stack.portal)
  await expect(page.locator('#send-card')).toBeVisible()
  let uploads = 0
  await page.route('**/api/retro/upload*', (route) => {
    uploads += 1
    if (uploads === 3 || uploads === 4) return route.abort('connectionreset') // deux coupures de suite au milieu du fichier
    return route.continue()
  })
  await page.locator('#pick').setInputFiles({ name: 'tetris.zip', mimeType: 'application/zip', buffer: game })
  await expect(page.locator('#list li')).toContainText('envoyé ✔', { timeout: 40_000 })
  expect(uploads).toBeGreaterThan(Math.ceil(game.length / 131072) + 1) // au moins deux morceaux renvoyés
  expect(fs.readFileSync(path.join(stack.stateDir, 'roms', 'tetris.zip')).equals(game)).toBe(true)
  expect(fs.readdirSync(path.join(stack.stateDir, 'roms')).filter((n) => n.startsWith('.envoi-'))).toEqual([])
  await page.close()
})

test('Envoyer des jeux : une réponse perdue après l’écriture du morceau ne fait ni trou ni doublon', async ({ stack, context }) => {
  const game = storedZip('rom.bin', crypto.randomBytes(768 * 1024))
  const { page } = await pcPage(context, stack.portal)
  await expect(page.locator('#send-card')).toBeVisible()
  let uploads = 0
  await page.route('**/api/retro/upload*', async (route) => {
    uploads += 1
    if (uploads === 3) {
      await route.fetch() // le dock reçoit et écrit le morceau...
      return route.abort('connectionreset') // ... mais la page n'en reçoit jamais la réponse
    }
    return route.continue()
  })
  await page.locator('#pick').setInputFiles({ name: 'tetrisp.zip', mimeType: 'application/zip', buffer: game })
  await expect(page.locator('#list li')).toContainText('envoyé ✔', { timeout: 40_000 })
  expect(fs.readFileSync(path.join(stack.stateDir, 'roms', 'tetrisp.zip')).equals(game)).toBe(true)
  await page.close()
})

test('Envoyer des jeux : sur l’écran, le PC associé est dit n’avoir pas besoin du code', async ({ dock }) => {
  await openGames(dock)
  await dock.getByTestId('games-upload').tap()
  await expect(dock.getByTestId('upload-trusted-hint')).toContainText('PC associé')
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await shot(dock, '11-envoi-code', 'l')
})

test.describe('un autre appareil que le PC associé', () => {
  test.use({ unpaired: true }) // un dock sans PC associé : personne n'est reconnu, le code de l'écran est exigé

  test('Envoyer des jeux : l’écran montre un code, la page /jeux du portail le demande puis reçoit un jeu', async ({ dock, stack, context }) => {
    await openGames(dock)
    await dock.getByTestId('games-upload').tap()
    const code = (await dock.getByTestId('upload-code').textContent())!.trim()
    expect(code).toMatch(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/)
    await expect(dock.getByTestId('upload-address')).toContainText('/jeux')
    await expect(dock.getByTestId('upload-status')).toContainText('Aucun jeu reçu')
    expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })

    const { page: pc, problems } = await pcPage(context, stack.portal)
    await expect(pc.locator('h1')).toHaveText('Retrogame')
    await expect(pc.locator('#login-card')).toBeVisible() // pas le PC associé : le code
    await expect(pc.locator('#send-card')).toBeHidden()
    await pc.locator('#code').fill('ZZZZ-ZZZZ')
    await pc.getByRole('button', { name: 'Continuer' }).click()
    await expect(pc.locator('#msg')).toContainText('Code incorrect')
    await pc.locator('#code').fill(code)
    await pc.getByRole('button', { name: 'Continuer' }).click()
    await expect(pc.locator('#send-card')).toBeVisible()
    await pc.locator('#pick').setInputFiles({ name: 'PacMan.zip', mimeType: 'application/zip', buffer: MINIMAL_ZIP })
    await expect(pc.locator('#list li')).toContainText('envoyé ✔', { timeout: 10_000 })
    expect(problems, 'la page du PC ne produit aucune erreur ni violation de sécurité').toEqual([])
    await pc.close()

    await expect(dock.getByTestId('upload-status')).toContainText('1 jeu reçu')
    expect(fs.readdirSync(path.join(stack.stateDir, 'roms'))).toEqual(['pacman.zip'])
    await dock.getByTestId('upload-done').tap()
    await expect(dock.getByTestId('games-list')).toBeVisible()
    // La session est fermée : le même code ne vaut plus rien.
    const again = await context.request.post(`${stack.portal}/api/retro/login`, { data: { code } })
    expect(again.status()).toBe(409)
  })

  test('Envoyer des jeux : sans code ni association, rien ne s’écrit', async ({ stack, context }) => {
    const me = await context.request.post(`${stack.portal}/api/retro/me`, { data: {} })
    expect(me.status()).toBe(200)
    expect((await me.json()).trusted).toBe(false)
    const nobody = await context.request.post(`${stack.portal}/api/retro/upload?name=pacman.zip`, { data: MINIMAL_ZIP })
    expect(nobody.status()).toBe(409)
    expect(fs.existsSync(path.join(stack.stateDir, 'roms', 'pacman.zip'))).toBe(false)
  })
})

test('Envoyer des jeux : un envoi sans jeton ne s’écrit pas, même sur le dock associé', async ({ dock, stack, context }) => {
  const nobody = await context.request.post(`${stack.portal}/api/retro/upload?name=pacman.zip`, { data: MINIMAL_ZIP })
  expect(nobody.status()).toBe(409) // aucune session ouverte
  await openGames(dock)
  await dock.getByTestId('games-upload').tap()
  await expect(dock.getByTestId('upload-code')).toBeVisible()
  const noToken = await context.request.post(`${stack.portal}/api/retro/upload?name=pacman.zip`, { data: MINIMAL_ZIP })
  expect(noToken.status()).toBe(401)
  expect(fs.existsSync(path.join(stack.stateDir, 'roms', 'pacman.zip'))).toBe(false)
})

test('Réglages : le dossier des jeux, la broche du bouton et ce que l’assistant en dit', async ({ dock, stack }) => {
  await openPage(dock, 'settings')
  const card = dock.getByTestId('settings-retro')
  await card.scrollIntoViewIfNeeded()
  await expect(dock.getByTestId('retro-pin')).toHaveText('GPIO17 (broche 11)')
  await expect(dock.getByTestId('retro-wiring')).toContainText('broche 11')
  await expect(dock.getByTestId('retro-wiring')).toContainText('masse')
  await expect(dock.getByTestId('retro-gpio-report')).toContainText('GPIO17 (broche 11)')
  await expect(dock.getByTestId('retro-summary')).toContainText('12 jeux')
  await shot(dock, '12-reglages-retrogame', 'l')

  await expect(dock.getByTestId('retro-upload-mode')).toContainText('sans code depuis le PC associé')
  await dock.getByTestId('retro-upload-toggle').tap()
  await expect(dock.getByTestId('retro-upload-mode')).toContainText('avec le code de l’écran')
  await expect(dock.getByTestId('retro-upload-toggle')).toHaveAttribute('aria-pressed', 'false')
  await expect.poll(async () => (await (await dock.request.post(`${stack.portal}/api/retro/me`, { data: {} })).json()).trusted).toBe(false)
  await dock.getByTestId('retro-upload-toggle').tap()
  await expect.poll(async () => (await (await dock.request.post(`${stack.portal}/api/retro/me`, { data: {} })).json()).trusted).toBe(true)

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
