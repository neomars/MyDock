// Étape d : lanceur d'applications, Steam, LibreOffice. Vrai Chromium 1024×600 tactile -> vrai dock -> vrai agent
// de démonstration : de vrais fichiers .desktop, une vraie bibliothèque Steam et un vrai historique LibreOffice
// (dossier temporaire, voir agent/mydock_agent/demo/apps.py), lus par les vrais fournisseurs.

import type { Page } from '@playwright/test'
import { expect, shot, test, typeOnKeyboard } from './fixtures'

const FIREFOX = 'org.mozilla.firefox.desktop'
const SPOTIFY = 'spotify.desktop'
const STEAM = 'steam.desktop'
const CALC = 'libreoffice-calc.desktop'
const CALCULATOR = 'org.gnome.Calculator.desktop'

async function openPicker(dock: Page): Promise<void> {
  await dock.getByTestId('tab-apps').tap()
  await dock.getByTestId('apps-choose').tap()
  await expect(dock.getByTestId('picker')).toBeVisible()
  await expect(dock.getByTestId(`pick-${FIREFOX}`)).toBeVisible() // la liste du PC est arrivée
}

async function pin(dock: Page, ...ids: string[]): Promise<void> {
  for (const id of ids) {
    await dock.getByTestId(`pick-${id}`).tap()
    await expect(dock.getByTestId(`chosen-${id}`)).toBeVisible()
  }
}

/** Noms des applications de la page Applis, dans l'ordre affiché. */
const tileNames = (dock: Page) => dock.locator('[data-testid="apps-grid"] .name').allInnerTexts()

// --- lanceur d'applications ---------------------------------------------------------------------

test('Applis : sans choix, la page propose de choisir les applications', async ({ dock }) => {
  await dock.getByTestId('tab-apps').tap()
  await expect(dock.getByTestId('apps-empty')).toContainText('Aucune application choisie')
  await shot(dock, '01-applis-vide', 'd')
  await dock.getByTestId('apps-choose').tap()
  await expect(dock.getByTestId('picker')).toBeVisible()
})

test('Sélecteur : toutes les applications visibles du PC, et seulement elles', async ({ dock }) => {
  await openPicker(dock)
  const list = dock.getByTestId('picker-all')
  // 18 applications visibles ; la « configuration initiale » (NoDisplay) et l'application réservée à KDE sont écartées.
  await expect(list.locator('.row')).toHaveCount(18)
  await expect(list).toContainText('LibreOffice Writer')
  await expect(list).not.toContainText('Configuration initiale')
  await expect(list).not.toContainText('Réservé à KDE')
  await expect(dock.getByTestId('picker-chosen')).toContainText('0/24')
  await expect(dock.getByTestId('chosen-none')).toBeVisible()
})

test('Sélecteur : la recherche passe par le clavier tactile, sans tenir compte des accents', async ({ dock }) => {
  await openPicker(dock)
  await dock.getByTestId('picker-search').tap()
  await typeOnKeyboard(dock, 'editeur')
  await dock.getByTestId('keyboard-ok').tap()
  const rows = dock.getByTestId('picker-all').locator('.row')
  await expect(rows).toHaveCount(2) // « Éditeur de texte » et « Éditeur d'images » (GIMP, par sa catégorie)
  await expect(rows.first()).toContainText('Éditeur de texte')
  await dock.getByTestId('picker-clear').tap()
  await expect(rows).toHaveCount(18)
  await dock.getByTestId('picker-search').tap()
  await typeOnKeyboard(dock, 'zzz')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('picker-none')).toBeVisible()
})

test('Sélecteur : ajouter, ordonner et retirer, avec mémorisation par le dock', async ({ dock }) => {
  await openPicker(dock)
  await pin(dock, FIREFOX, SPOTIFY, STEAM, CALC)
  await expect(dock.getByTestId('picker-chosen')).toContainText('4/24')
  await expect(dock.getByTestId(`pick-${FIREFOX}`)).toHaveAttribute('aria-pressed', 'true')
  await dock.getByTestId('picker-all').evaluate((list) => list.scrollTo(0, 0)) // les appuis ont fait défiler la liste
  await shot(dock, '02-selecteur', 'd')

  await dock.getByTestId(`up-${CALC}`).tap()
  await dock.getByTestId(`down-${FIREFOX}`).tap()
  await dock.getByTestId(`remove-${STEAM}`).tap()
  await expect(dock.getByTestId(`chosen-${STEAM}`)).toHaveCount(0)
  await expect(dock.getByTestId(`pick-${STEAM}`)).toHaveAttribute('aria-pressed', 'false')
  await dock.getByTestId('picker-done').tap()
  await expect(dock.getByTestId('picker')).toHaveCount(0)

  expect(await tileNames(dock)).toEqual(['Spotify', 'Firefox', 'LibreOffice Calc']) // l'ordre choisi
  // Le Pi s'en souvient : même après un rechargement de la page.
  await dock.reload()
  await dock.getByTestId('tab-apps').tap()
  await expect(dock.getByTestId('apps-grid')).toBeVisible()
  expect(await tileNames(dock)).toEqual(['Spotify', 'Firefox', 'LibreOffice Calc'])
  await expect(dock.getByTestId('apps-count')).toHaveText('3 applications')
})

test('Applis : les icônes viennent du thème du PC, les autres ont une pastille', async ({ dock }) => {
  await openPicker(dock)
  await pin(dock, FIREFOX, CALC, CALCULATOR)
  await dock.getByTestId('picker-done').tap()
  const art = dock.getByTestId(`launch-${FIREFOX}`).locator('.art img')
  await expect(art).toHaveAttribute('src', /^data:image\/svg\+xml;base64,/)
  await expect(dock.getByTestId(`launch-${CALCULATOR}`).locator('.art img')).toHaveAttribute('src', /^data:image\/svg\+xml/)
  await shot(dock, '03-applis', 'd')
})

test('Applis : toucher une application la lance, et un second appui pendant le lancement n’en lance pas deux', async ({ dock }) => {
  await openPicker(dock)
  await pin(dock, FIREFOX)
  await dock.getByTestId('picker-done').tap()
  const tile = dock.getByTestId(`launch-${FIREFOX}`)
  const height = (await tile.boundingBox())?.height
  await tile.tap()
  await expect(dock.getByTestId(`launching-${FIREFOX}`)).toHaveText('Lancement…')
  expect((await tile.boundingBox())?.height).toBe(height) // la grille ne bouge pas pendant le lancement
  await expect(dock.getByTestId('toast')).toContainText('« Firefox » se lance…') // nom donné par l'agent
  await shot(dock, '04-applis-lancement', 'd')
  await expect(dock.getByTestId(`launching-${FIREFOX}`)).toHaveCount(0, { timeout: 8_000 })
})

test('Sélecteur : toutes les cibles se touchent au doigt (au moins 48 px)', async ({ dock }) => {
  await openPicker(dock)
  await pin(dock, FIREFOX, SPOTIFY, STEAM)
  const targets = dock.locator('[data-testid="picker"] button')
  const count = await targets.count()
  expect(count).toBeGreaterThan(10)
  for (let i = 0; i < count; i++) {
    const box = await targets.nth(i).boundingBox()
    if (!box) continue // une ligne défilée hors de la vue n'a pas de boîte
    const name = (await targets.nth(i).getAttribute('data-testid')) ?? (await targets.nth(i).innerText())
    expect(box.height, `${name} : hauteur`).toBeGreaterThanOrEqual(48)
    expect(box.width, `${name} : largeur`).toBeGreaterThanOrEqual(48)
  }
})

test('Réglages : le lanceur a sa carte, qui ouvre le même sélecteur', async ({ dock }) => {
  await dock.getByTestId('tab-settings').tap()
  await expect(dock.getByTestId('launcher-count')).toContainText('Aucune application choisie')
  await dock.getByTestId('choose-apps').tap()
  await expect(dock.getByTestId('picker')).toBeVisible()
  await pin(dock, FIREFOX, SPOTIFY)
  await dock.getByTestId('picker-done').tap()
  await expect(dock.getByTestId('launcher-count')).toContainText('2 applications')
})

// --- Steam --------------------------------------------------------------------------------------

test('Steam : la bibliothèque (deux disques), sans les outils ni le jeu en cours de téléchargement', async ({ dock }) => {
  await dock.getByTestId('tab-steam').tap()
  await expect(dock.getByTestId('steam-grid')).toBeVisible()
  await expect(dock.getByTestId('steam-count')).toHaveText('11 jeux')
  // Proton, Steam Linux Runtime, Steamworks et un jeu à moitié téléchargé sont écartés.
  for (const hidden of [1493710, 1628350, 228980, 990080]) await expect(dock.getByTestId(`game-${hidden}`)).toHaveCount(0)
  await expect(dock.getByTestId('game-1245620')).toBeVisible() // ELDEN RING, sur le disque système
  await expect(dock.getByTestId('game-1086940')).toBeAttached() // Baldur's Gate 3, sur l'autre disque
  await expect(dock.getByTestId('steam-status')).toContainText('Steam n’est pas lancé')
})

test('Steam : les jeux joués récemment viennent d’abord, les jaquettes arrivent au fil du défilement', async ({ dock }) => {
  await dock.getByTestId('tab-steam').tap()
  await expect(dock.getByTestId('steam-grid')).toBeVisible()
  const order = await dock.locator('[data-testid="steam-grid"] .game').evaluateAll((els) => els.map((el) => el.getAttribute('title')))
  expect(order.slice(0, 3)).toEqual(['Hades', 'ELDEN RING', 'Factorio']) // joués il y a 1, 2 et 3 jours
  expect(order.at(-1)).toBe('Cyberpunk 2077') // jamais lancé : la mise à jour date de 90 jours, il reste après
  await expect(dock.getByTestId('game-1145360').locator('img')).toHaveAttribute('src', /^data:image\/svg\+xml;base64,/)
  await shot(dock, '05-steam', 'd')
})

test('Steam : lancer un jeu, puis le voir « en cours » dès que l’agent le détecte', async ({ dock }) => {
  await dock.getByTestId('tab-steam').tap()
  await dock.getByTestId('game-1145360').tap()
  await expect(dock.getByTestId('badge-1145360')).toHaveText('Lancement…')
  await expect(dock.getByTestId('toast')).toContainText('« Hades » se lance…')
  // L'agent surveille les processus toutes les 3 s pendant que la page est ouverte.
  await expect(dock.getByTestId('badge-1145360')).toHaveText('En cours', { timeout: 10_000 })
  await expect(dock.getByTestId('steam-running')).toHaveText('Hades')
  await expect(dock.getByTestId('steam-status')).toContainText('En cours')
  await shot(dock, '06-steam-en-cours', 'd')
})

// --- LibreOffice --------------------------------------------------------------------------------

test('Office : documents récents dans l’ordre de LibreOffice, avec leur famille et leur ancienneté', async ({ dock }) => {
  await dock.getByTestId('tab-office').tap()
  await expect(dock.getByTestId('office-recent')).toContainText('Documents récents (6)')
  const names = await dock.locator('[data-testid^="recent-name-"]').allInnerTexts()
  expect(names).toEqual([
    'Rapport trimestriel.odt',
    'Budget 2026.ods',
    'Présentation clients.odp',
    'Courrier assurance.docx',
    'Planning chantier.xlsx',
    'Schéma réseau.odg',
  ])
  const first = dock.locator('[data-testid^="recent-"]:not([data-testid^="recent-name-"])').first()
  await expect(first).toContainText('Texte · ~/Documents/Travail')
  await expect(first).toContainText('il y a 1 h')
  await expect(dock.getByTestId('office-recent')).toContainText('Tableur · ~/Documents/Finances')
  await expect(dock.getByTestId('office-recent')).toContainText('Dessin · ~/Documents/Réseau')
  await shot(dock, '07-office', 'd')
})

test('Office : un nouveau document, ou rouvrir un récent', async ({ dock }) => {
  await dock.getByTestId('tab-office').tap()
  await dock.getByTestId('new-calc').tap()
  await expect(dock.getByTestId('toast')).toContainText('« LibreOffice Calc » se lance…')
  await expect(dock.getByTestId('new-calc')).toContainText('Lancement…')
  await dock.locator('[data-testid^="recent-"]:not([data-testid^="recent-name-"])').nth(1).tap()
  await expect(dock.getByTestId('toast')).toContainText('« Budget 2026.ods » se lance…')
})

// --- ergonomie et pannes ------------------------------------------------------------------------

test('Applis, Steam et Office : cibles tactiles d’au moins 48 px et aucun défilement de la page', async ({ dock }) => {
  await openPicker(dock)
  await pin(dock, FIREFOX, SPOTIFY, STEAM, CALC, CALCULATOR)
  await dock.getByTestId('picker-done').tap()
  for (const id of ['apps', 'steam', 'office']) {
    await dock.getByTestId(`tab-${id}`).tap()
    await expect(dock.getByTestId(`page-${id}`)).toBeVisible()
    await dock.waitForTimeout(400)
    const small = await dock.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('main button, main [role="button"]')]
        .map((el) => ({ el, box: el.getBoundingClientRect() }))
        .filter(({ box }) => box.width > 0 && (box.height < 48 || box.width < 48))
        .map(({ el }) => el.getAttribute('data-testid') ?? el.textContent?.trim()),
    )
    expect(small, `page ${id}`).toEqual([])
    const size = await dock.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
    expect(size).toEqual({ w: 1024, h: 600 })
  }
})

test('Applis, Steam et Office : elles disparaissent avec le PC et reviennent avec lui', async ({ dock, stack }) => {
  await dock.getByTestId('tab-steam').tap()
  await expect(dock.getByTestId('steam-grid')).toBeVisible()
  stack.pcOff()
  await expect(dock.getByTestId('offline-title')).toBeVisible({ timeout: 20_000 })
  await expect(dock.getByTestId('steam-grid')).toHaveCount(0)
  stack.pcOn()
  await expect(dock.getByTestId('steam-grid')).toBeVisible({ timeout: 40_000 })
})
