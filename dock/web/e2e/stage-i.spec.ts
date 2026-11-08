// Étape i : la page Spotify (lecteur, recherche, bibliothèque, pages d'album, d'artiste et de playlist).
// Vrai Chromium 1024×600 tactile -> vrai dock -> vrai agent de démonstration (catalogue fictif, aucun réseau).
// Les états que la démonstration ne produit pas (pas de compte, compte refusé, agent trop ancien…) sont dans
// states.spec.ts, avec un dock simulé.

import type { Page } from '@playwright/test'
import { expect, openPage, shot, swipe, test, typeOnKeyboard } from './fixtures'

/** Éléments de la page qui dépassent à droite de l'écran (le texte doublé ne doit rien couper). */
async function overflowing(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bad: string[] = []
    for (const el of document.querySelectorAll('main *')) {
      const box = el.getBoundingClientRect()
      if (box.width > 0 && box.right > window.innerWidth + 1) {
        bad.push(`${el.tagName.toLowerCase()}[${el.getAttribute('data-testid') ?? String((el as HTMLElement).className).slice(0, 24)}] → ${Math.round(box.right)} px`)
      }
    }
    return bad.slice(0, 5)
  })
}

async function noOverflow(page: Page, what: string): Promise<void> {
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size, `${what} : l'écran ne défile pas`).toEqual({ w: 1024, h: 600 })
  expect(await overflowing(page), `${what} : rien ne dépasse à droite`).toEqual([])
}

/** Cherche au clavier tactile : ouvre l'onglet, touche le champ, tape, valide. */
async function searchFor(page: Page, text: string): Promise<void> {
  await page.getByTestId('sp-tab-search').tap()
  await page.getByTestId('sp-search-field').tap()
  await typeOnKeyboard(page, text)
  await page.getByTestId('keyboard-ok').tap()
  await expect(page.getByTestId('sp-search-field')).toContainText(text)
}

/** Les pochettes des lignes `from` à `to` (non comprise) sont arrivées : chacune montre une image, pas une icône de repli. */
async function coversLoaded(page: Page, rows: string, from = 0, to?: number): Promise<void> {
  const list = page.locator(rows)
  const last = to ?? (await list.count())
  for (let i = from; i < last; i++) {
    await expect(list.nth(i).locator('.thumb img'), `pochette de la ligne ${i + 1}`).toBeVisible({ timeout: 8_000 })
  }
}

// --- l'accueil -------------------------------------------------------------------------------------------------

test('Accueil : le bouton de la musique s’appelle Spotify et montre le morceau en cours', async ({ dock }) => {
  await expect(dock.getByTestId('tile-music')).toContainText('Spotify')
  await expect(dock.getByTestId('tile-music')).not.toContainText('Musique')
  await expect(dock.getByTestId('tile-music-sub')).toHaveText('Nuit blanche')
  await dock.waitForTimeout(450) // l'accueil a fini d'apparaître
  await shot(dock, '00-accueil', 'i')
})

// --- le lecteur ------------------------------------------------------------------------------------------------

test('Spotify : le lecteur montre pochette, titre, commandes, aléatoire, répétition et volume, sans rien couper', async ({ dock }) => {
  await openPage(dock, 'music')
  await expect(dock.getByTestId('sp-tab-player')).toHaveAttribute('aria-selected', 'true')
  await expect(dock.getByTestId('music-title')).toHaveText('Nuit blanche')
  await expect(dock.getByTestId('music-artist')).toHaveText('Les Fantômes · Néons')
  await expect(dock.getByTestId('music-cover-img')).toHaveAttribute('src', /^data:image\/svg\+xml;base64,/)
  for (const id of ['music-shuffle', 'music-prev', 'music-play', 'music-next', 'music-repeat', 'music-volume', 'progress']) {
    await expect(dock.getByTestId(id), id).toBeVisible()
  }
  // Les trois onglets, avec leur nom lisible par un lecteur d'écran, sont des cibles tactiles confortables.
  for (const [id, name] of [['player', 'En lecture'], ['search', 'Rechercher'], ['library', 'Ta bibliothèque']]) {
    const tab = dock.getByTestId(`sp-tab-${id}`)
    await expect(tab).toHaveAccessibleName(name)
    const box = (await tab.boundingBox())!
    expect(Math.min(box.width, box.height)).toBeGreaterThanOrEqual(72)
  }
  await dock.waitForTimeout(400)
  await noOverflow(dock, 'le lecteur')
  await shot(dock, '01-lecteur', 'i')
})

test('Spotify : lecture aléatoire et répétition se règlent au toucher, la répétition fait le tour rien → liste → morceau', async ({ dock }) => {
  await openPage(dock, 'music')
  const shuffle = dock.getByTestId('music-shuffle')
  await expect(shuffle).toHaveAttribute('aria-pressed', 'false')
  await shuffle.tap()
  await expect(shuffle).toHaveAttribute('aria-pressed', 'true')
  await shuffle.tap()
  await expect(shuffle).toHaveAttribute('aria-pressed', 'false')

  const repeat = dock.getByTestId('music-repeat')
  await expect(repeat).toHaveAttribute('data-loop', 'none')
  await repeat.tap()
  await expect(repeat).toHaveAttribute('data-loop', 'playlist')
  await expect(repeat).toHaveAttribute('aria-pressed', 'true')
  await repeat.tap()
  await expect(repeat).toHaveAttribute('data-loop', 'track')
  await expect(repeat).toHaveAccessibleName(/Répéter le morceau/)
  await repeat.tap()
  await expect(repeat).toHaveAttribute('data-loop', 'none')
  await expect(repeat).toHaveAttribute('aria-pressed', 'false')
})

test('Spotify : le lecteur d’un navigateur n’a ni aléatoire ni répétition (il ne sait pas les régler)', async ({ dock }) => {
  await openPage(dock, 'music')
  await dock.getByTestId('music-select-brave.instance4242').tap()
  await expect(dock.getByTestId('music-title')).toHaveText('Documentaire : les océans')
  await expect(dock.getByTestId('music-shuffle')).toHaveCount(0)
  await expect(dock.getByTestId('music-repeat')).toHaveCount(0)
  await expect(dock.getByTestId('music-source')).toContainText('Brave') // un autre lecteur que Spotify dit son nom
  await dock.getByTestId('music-select-spotify').tap()
  await expect(dock.getByTestId('music-shuffle')).toBeVisible()
  await expect(dock.getByTestId('music-source')).toHaveCount(0)
})

// --- rechercher -----------------------------------------------------------------------------------------------

test('Spotify : chercher au clavier tactile classe les résultats en morceaux, artistes et albums, avec leurs pochettes', async ({ dock }) => {
  await openPage(dock, 'music')
  await dock.getByTestId('sp-tab-search').tap()
  await expect(dock.getByTestId('sp-search-idle')).toBeVisible()
  await shot(dock, '02-recherche-vide', 'i')
  await dock.getByTestId('sp-search-field').tap()
  await typeOnKeyboard(dock, 'fantomes')
  await shot(dock, '03-recherche-clavier', 'i')
  await dock.getByTestId('keyboard-ok').tap()

  await expect(dock.getByTestId('sp-results')).toBeVisible()
  await expect(dock.getByTestId('sp-group-tracks').locator('[data-kind=track]')).toHaveCount(7)
  await expect(dock.getByTestId('sp-group-artists').locator('[data-kind=artist]')).toHaveCount(1)
  await expect(dock.getByTestId('sp-group-albums').locator('[data-kind=album]')).toHaveCount(2)
  await expect(dock.getByTestId('sp-group-playlists')).toHaveCount(0) // un groupe vide n'est pas montré
  // Le morceau que joue le lecteur est repéré dans la liste.
  await expect(dock.locator('[data-testid^=sp-track-][data-active=true]')).toHaveCount(1)
  await expect(dock.locator('[data-testid^=sp-track-][data-active=true]')).toContainText('Nuit blanche')
  const rows = '[data-testid=sp-group-tracks] .row'
  await coversLoaded(dock, rows, 0, 4) // les lignes à l'écran ont leur pochette
  await noOverflow(dock, 'les résultats')
  await shot(dock, '04-recherche', 'i')
  // Les pochettes se demandent au fil du défilement : celles du bas de la liste arrivent quand on y descend.
  await dock.locator('main.page').evaluate((el) => el.scrollTo(0, 420))
  await coversLoaded(dock, rows, 4, 7)
})

test('Spotify : une recherche sans résultat le dit, et la croix efface le champ', async ({ dock }) => {
  await openPage(dock, 'music')
  await searchFor(dock, 'zzzz')
  await expect(dock.getByTestId('sp-search-empty')).toContainText('zzzz')
  await dock.getByTestId('sp-search-clear').tap()
  await expect(dock.getByTestId('sp-search-idle')).toBeVisible()
  await expect(dock.getByTestId('sp-search-field')).toContainText('Que veux-tu écouter ?')
})

test('Spotify : toucher un morceau le joue ; le lecteur et le mini-lecteur le montrent', async ({ dock }) => {
  await openPage(dock, 'music')
  await searchFor(dock, 'fantomes')
  await dock.locator('[data-testid^=sp-track-]', { hasText: 'Écho' }).tap()
  await expect(dock.getByTestId('toast')).toContainText('Lecture : Écho')
  await expect(dock.getByTestId('mini-title')).toHaveText('Écho')
  await expect(dock.locator('[data-testid^=sp-track-][data-active=true]')).toContainText('Écho') // la ligne suit
  await dock.getByTestId('sp-tab-player').tap()
  await expect(dock.getByTestId('music-title')).toHaveText('Écho')
  await expect(dock.getByTestId('music-artist')).toHaveText('Les Fantômes · Néons')
  await expect(dock.getByTestId('music-play')).toHaveAccessibleName('Pause') // et il joue
  await expect(dock.getByTestId('music-cover-img')).toHaveAttribute('src', /^data:image\/svg\+xml/)
})

test('Spotify : un album s’ouvre, « Lecture » le joue en entier, le retour revient aux résultats', async ({ dock }) => {
  await openPage(dock, 'music')
  await searchFor(dock, 'saison')
  await dock.locator('[data-testid^=sp-album-]', { hasText: 'Saisons' }).tap()
  await expect(dock.getByTestId('sp-detail')).toBeVisible()
  await expect(dock.getByTestId('sp-detail-title')).toHaveText('Saisons')
  const tracks = dock.getByTestId('sp-detail-tracks').locator('[data-kind=track]')
  await expect(tracks).toHaveCount(4)
  await expect(tracks.first()).toContainText('Sous la pluie de Kyoto')
  await expect(tracks.nth(1).locator('.num')).toHaveText('2') // dans un album, le numéro remplace la pochette
  await expect(dock.getByTestId('sp-detail').locator('.cover img')).toBeVisible() // la pochette de l'album
  await noOverflow(dock, 'la page d’un album')
  await shot(dock, '05-album', 'i')

  await dock.getByTestId('sp-detail-play').tap()
  await expect(dock.getByTestId('mini-title')).toHaveText('Sous la pluie de Kyoto')
  await tracks.nth(2).tap() // un morceau de l'album, joué dans son album
  await expect(dock.getByTestId('mini-title')).toHaveText('Neige fine')

  await dock.getByTestId('sp-back').tap()
  await expect(dock.getByTestId('sp-results')).toBeVisible()
  await expect(dock.getByTestId('sp-search-field')).toContainText('saison')
})

test('Spotify : la page d’un artiste a ses titres populaires et ses albums, et les retours se suivent', async ({ dock }) => {
  await openPage(dock, 'music')
  await searchFor(dock, 'mizu')
  await dock.locator('[data-testid^=sp-artist-]').first().tap()
  await expect(dock.getByTestId('sp-detail-title')).toHaveText('Mizu')
  await expect(dock.getByTestId('sp-detail-tracks')).toContainText('Titres populaires')
  await expect(dock.getByTestId('sp-detail-albums')).toContainText('Saisons')
  await shot(dock, '06-artiste', 'i')
  await dock.locator('[data-testid^=sp-album-]').first().tap() // un album depuis l'artiste : deux pages empilées
  await expect(dock.getByTestId('sp-detail-title')).toHaveText('Saisons')
  await dock.getByTestId('sp-back').tap()
  await expect(dock.getByTestId('sp-detail-title')).toHaveText('Mizu')
  await dock.getByTestId('sp-back').tap()
  await expect(dock.getByTestId('sp-results')).toBeVisible()
  await expect(dock.getByTestId('sp-detail')).toHaveCount(0)
})

// --- la bibliothèque ------------------------------------------------------------------------------------------

test('Spotify : la bibliothèque a ses cinq rubriques, chacune se remplit', async ({ dock }) => {
  await openPage(dock, 'music')
  await dock.getByTestId('sp-tab-library').tap()
  await expect(dock.getByTestId('sp-library-title')).toHaveText('Ta bibliothèque')
  await expect(dock.getByTestId('sp-sections').locator('button')).toHaveCount(5)
  await noOverflow(dock, 'la bibliothèque')
  await shot(dock, '07-bibliotheque', 'i')

  for (const [section, count, kind] of [
    ['playlists', 2, 'playlist'],
    ['liked', 12, 'track'],
    ['albums', 6, 'album'],
    ['artists', 4, 'artist'],
    ['recent', 5, 'track'],
  ] as const) {
    await dock.getByTestId(`sp-section-${section}`).tap()
    await expect(dock.getByTestId('sp-library-items').locator(`[data-kind=${kind}]`)).toHaveCount(count)
    await dock.getByTestId('sp-back').tap()
    await expect(dock.getByTestId('sp-sections')).toBeVisible()
  }
})

test('Spotify : une playlist de la bibliothèque s’ouvre et se joue', async ({ dock }) => {
  await openPage(dock, 'music')
  await dock.getByTestId('mini-next').tap() // on part d'un autre morceau que celui de la playlist
  await expect(dock.getByTestId('mini-title')).toHaveText('Sous la pluie de Kyoto')
  await dock.getByTestId('sp-tab-library').tap()
  await dock.getByTestId('sp-section-playlists').tap()
  await coversLoaded(dock, '[data-testid=sp-library-items] .row')
  await shot(dock, '08-playlists', 'i')
  await dock.locator('[data-testid^=sp-playlist-]', { hasText: 'Soirée calme' }).tap()
  await expect(dock.getByTestId('sp-detail-title')).toHaveText('Soirée calme')
  await expect(dock.getByTestId('sp-detail-tracks').locator('[data-kind=track]')).toHaveCount(5)
  await dock.getByTestId('sp-detail-play').tap()
  await expect(dock.getByTestId('mini-title')).toHaveText('Nuit blanche')
  await dock.getByTestId('sp-back').tap() // retour à la liste des playlists, puis aux rubriques
  await expect(dock.getByTestId('sp-library-items')).toBeVisible()
  await dock.getByTestId('sp-back').tap()
  await expect(dock.getByTestId('sp-sections')).toBeVisible()
})

// --- navigation ------------------------------------------------------------------------------------------------

test('Spotify : on revient toujours sur ce qui joue ; la recherche faite est gardée', async ({ dock }) => {
  await openPage(dock, 'music')
  await searchFor(dock, 'mizu')
  await expect(dock.getByTestId('sp-results')).toBeVisible()
  await dock.getByTestId('home-back').tap()
  await expect(dock.getByTestId('home')).toBeVisible()
  await openPage(dock, 'music')
  await expect(dock.getByTestId('sp-tab-player')).toHaveAttribute('aria-selected', 'true')
  await dock.getByTestId('sp-tab-search').tap()
  await expect(dock.getByTestId('sp-search-field')).toContainText('mizu')
  await expect(dock.getByTestId('sp-results')).toBeVisible()
})

test('Spotify : le balayage de gauche à droite ramène à l’accueil depuis chaque onglet', async ({ dock }) => {
  await openPage(dock, 'music')
  for (const tab of ['player', 'search', 'library']) {
    await dock.getByTestId(`sp-tab-${tab}`).tap()
    await dock.waitForTimeout(350)
    await swipe(dock, { x: 8, y: 300 }, { x: 640, y: 308 })
    await expect(dock.getByTestId('home'), `depuis l'onglet ${tab}`).toBeVisible()
    await openPage(dock, 'music')
  }
})

test('Spotify : le texte doublé ne coupe rien, quelle que soit la vue', async ({ dock }) => {
  await openPage(dock, 'music')
  await noOverflow(dock, 'lecteur')
  await dock.getByTestId('sp-tab-search').tap()
  await noOverflow(dock, 'recherche vide')
  await searchFor(dock, 'a')
  await expect(dock.getByTestId('sp-results')).toBeVisible()
  await noOverflow(dock, 'résultats')
  await dock.getByTestId('sp-tab-library').tap()
  await noOverflow(dock, 'bibliothèque')
})
