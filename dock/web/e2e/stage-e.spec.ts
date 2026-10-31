// Étape e : la Bourse. Vrai Chromium 1024×600 tactile -> vrai dock (cotations de démonstration calculées,
// aucun accès à Internet) -> la page marche sans PC : c'est le Pi qui lit les cotations.

import type { Page } from '@playwright/test'
import { expect, shot, test, typeOnKeyboard } from './fixtures'

const plain = (text: string | null): string => (text ?? '').replace(/[  ]/g, ' ').trim()

async function openMarkets(dock: Page): Promise<void> {
  await dock.getByTestId('tab-markets').tap()
  await expect(dock.getByTestId('price-crypto:BTC')).toHaveText(/\d/) // la première cotation est arrivée
}

async function openEditor(dock: Page): Promise<void> {
  await openMarkets(dock)
  await dock.getByTestId('markets-edit').tap()
  await expect(dock.getByTestId('mk-editor')).toBeVisible()
}

/** Ajoute une valeur proposée en suggestion : vérification, puis confirmation. */
async function addSuggested(dock: Page, symbol: string): Promise<void> {
  await dock.getByTestId(`mk-suggest-${symbol}`).tap()
  await expect(dock.getByTestId('mk-found')).toContainText(symbol)
  await dock.getByTestId('mk-confirm').tap()
  await expect(dock.getByTestId('mk-found')).toHaveCount(0)
}

async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll('button, [role=tab]')]
      .map((el) => ({ el, box: el.getBoundingClientRect() }))
      .filter(({ el, box }) => box.width > 0 && box.height > 0 && !(el as HTMLButtonElement).disabled)
      .filter(({ box }) => Math.min(box.width, box.height) < 48)
      .map(({ el, box }) => `${(el.textContent ?? '').trim().slice(0, 30)} (${Math.round(box.width)}×${Math.round(box.height)})`),
  )
}

async function noPageScroll(page: Page): Promise<void> {
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size).toEqual({ w: 1024, h: 600 })
}

// --- la liste ------------------------------------------------------------------------------------

test('Bourse : Bitcoin et Ethereum au départ, avec prix, variation signée et mini-graphique', async ({ dock }) => {
  await openMarkets(dock)
  for (const id of ['crypto:BTC', 'crypto:ETH']) {
    await expect(dock.getByTestId(`quote-${id}`)).toBeVisible()
    expect(plain(await dock.getByTestId(`price-${id}`).textContent())).toMatch(/^\d[\d ]*(,\d+)? €$/)
    // Le signe est écrit (« + » ou « − »), jamais seulement la couleur.
    expect(plain(await dock.getByTestId(`pct-${id}`).textContent())).toMatch(/^[+−]\d+,\d\d %$/)
    await expect(dock.getByTestId(`quote-${id}`).getByTestId('spark')).toBeVisible()
  }
  await expect(dock.getByTestId('quote-crypto:BTC')).toContainText('Bitcoin')
  await expect(dock.getByTestId('markets-statustext')).toContainText('Mis à jour')
  await expect(dock.getByTestId('markets-demo')).toHaveText('Démonstration')
  await expect(dock.getByTestId('markets-problem')).toHaveCount(0)
  await shot(dock, '01-bourse', 'e')
})

test('Bourse : elle marche sans le PC (c’est le Pi qui lit les cotations)', async ({ dock, stack }) => {
  stack.pcOff()
  await expect(dock.getByTestId('offline-title')).toBeVisible({ timeout: 20_000 })
  await openMarkets(dock)
  await expect(dock.getByTestId('offline')).toHaveCount(0)
  const before = plain(await dock.getByTestId('markets-statustext').textContent())
  await dock.getByTestId('markets-refresh').tap()
  await expect(dock.getByTestId('markets-statustext')).toContainText('à l’instant')
  expect(before).toContain('Mis à jour')
})

test('Bourse : actualiser met à jour, mais pas deux fois de suite', async ({ dock }) => {
  await openMarkets(dock)
  await dock.getByTestId('markets-refresh').tap()
  await expect(dock.getByTestId('markets-statustext')).toContainText('à l’instant')
  await dock.getByTestId('markets-refresh').tap()
  await expect(dock.getByTestId('toast')).toContainText('viennent d’être actualisées')
})

// --- modifier la liste ---------------------------------------------------------------------------

test('Modifier : ajouter une crypto proposée, vérifiée avant d’être ajoutée, et la retrouver après un rechargement', async ({ dock }) => {
  await openEditor(dock)
  await expect(dock.getByTestId('mk-chosen')).toContainText('2/20')
  await dock.getByTestId('mk-suggest-SOL').tap()
  await expect(dock.getByTestId('mk-found')).toContainText('Solana')
  await expect(dock.getByTestId('mk-found')).toContainText('Démonstration')
  expect(plain(await dock.getByTestId('mk-found').textContent())).toMatch(/\d+,\d\d €/)
  await shot(dock, '05-editeur', 'e')
  await dock.getByTestId('mk-confirm').tap()
  await expect(dock.getByTestId('mk-item-crypto:SOL')).toBeVisible()
  await expect(dock.getByTestId('mk-chosen')).toContainText('3/20')
  await expect(dock.getByTestId('toast')).toContainText('« Solana » ajouté')
  await dock.getByTestId('mk-done').tap()
  await expect(dock.getByTestId('quote-crypto:SOL')).toBeVisible()
  await expect(dock.getByTestId('price-crypto:SOL')).toHaveText(/\d/)
  await dock.reload()
  await expect(dock.getByTestId('quote-crypto:SOL')).toBeVisible() // mémorisé par le Pi
})

test('Modifier : on cherche par nom ou par symbole au clavier tactile, et un résultat s’ajoute d’un toucher', async ({ dock }) => {
  await openEditor(dock)
  await dock.getByTestId('mk-kind-stock').tap()
  await dock.getByTestId('mk-search').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  await typeOnKeyboard(dock, 'strategy')
  await expect(dock.getByTestId('keyboard-value')).toHaveText('strategy')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('mk-results')).toContainText('Strategy Inc')
  await shot(dock, '06-editeur-recherche', 'e')
  await dock.getByTestId('mk-result-MSTR').tap()
  await expect(dock.getByTestId('toast')).toContainText('« Strategy Inc » ajouté')
  await expect(dock.getByTestId('mk-item-stock:MSTR')).toBeVisible()
  await expect(dock.getByTestId('mk-search')).toContainText('Rechercher') // la recherche est vidée

  // Les touches ^ et = servent aux symboles comme ^FCHI et EURUSD=X.
  await dock.getByTestId('mk-search').tap()
  await typeOnKeyboard(dock, '^fchi')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('mk-results')).toContainText('CAC 40')
  await dock.getByTestId('mk-result-^FCHI').tap()
  await expect(dock.getByTestId('mk-item-stock:^FCHI')).toBeVisible()

  await dock.getByTestId('mk-search').tap()
  await typeOnKeyboard(dock, 'eurusd=x')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('mk-results')).toContainText('EUR/USD')
  // Une valeur déjà suivie se reconnaît dans les résultats.
  await dock.getByTestId('mk-clear').tap()
  await dock.getByTestId('mk-search').tap()
  await typeOnKeyboard(dock, 'mstr')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('mk-result-MSTR')).toBeDisabled()
  await expect(dock.getByTestId('mk-result-MSTR')).toHaveText('Suivie')
})

test('Modifier : les cryptos courantes se trouvent par leur nom, même sans réseau', async ({ dock }) => {
  await openEditor(dock)
  await dock.getByTestId('mk-search').tap()
  await typeOnKeyboard(dock, 'bit')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('mk-results')).toContainText('Bitcoin')
  await expect(dock.getByTestId('mk-results')).toContainText('Bitcoin Cash')
  await dock.getByTestId('mk-result-BCH').tap() // inconnue de la démonstration : l'ajout est refusé, expliqué
  await expect(dock.getByTestId('mk-error')).toContainText('« BCH » est inconnu')
  await expect(dock.getByTestId('mk-item-crypto:BCH')).toHaveCount(0)
})

test('Modifier : un symbole inconnu est expliqué et rien n’est ajouté', async ({ dock }) => {
  await openEditor(dock)
  await dock.getByTestId('mk-kind-stock').tap()
  await dock.getByTestId('mk-search').tap()
  await typeOnKeyboard(dock, 'nope')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('mk-noresult')).toContainText('Aucun résultat pour « nope »')
  await dock.getByTestId('mk-asis').tap() // « Vérifier « NOPE » tel quel »
  await expect(dock.getByTestId('mk-error')).toContainText('« NOPE » est inconnu')
  await expect(dock.getByTestId('mk-found')).toHaveCount(0)
  await expect(dock.getByTestId('mk-chosen')).toContainText('2/20')
  await dock.getByTestId('mk-kind-crypto').tap() // changer de type efface le message
  await expect(dock.getByTestId('mk-error')).toHaveCount(0)
})

test('Modifier : une valeur déjà suivie ne s’ajoute pas deux fois', async ({ dock }) => {
  await openEditor(dock)
  await dock.getByTestId('mk-suggest-BTC').tap()
  await expect(dock.getByTestId('mk-confirm')).toBeDisabled()
  await expect(dock.getByTestId('mk-confirm')).toHaveText('Déjà suivie')
})

test('Modifier : ordre et retrait, et une liste vide propose d’en choisir une', async ({ dock }) => {
  await openEditor(dock)
  const names = () => dock.locator('[data-testid^="mk-item-"] strong').allInnerTexts()
  expect(await names()).toEqual(['Bitcoin', 'Ethereum'])
  await dock.getByTestId('mk-down-crypto:BTC').tap()
  await expect.poll(names).toEqual(['Ethereum', 'Bitcoin'])
  await dock.getByTestId('mk-up-crypto:BTC').tap()
  await expect.poll(names).toEqual(['Bitcoin', 'Ethereum'])
  await dock.getByTestId('mk-remove-crypto:BTC').tap()
  await dock.getByTestId('mk-remove-crypto:ETH').tap()
  await expect(dock.getByTestId('mk-none')).toBeVisible()
  await dock.getByTestId('mk-done').tap()
  await expect(dock.getByTestId('markets-empty')).toContainText('Aucune valeur suivie')
  await shot(dock, '08-bourse-vide', 'e')
  await dock.getByTestId('markets-choose').tap()
  await expect(dock.getByTestId('mk-editor')).toBeVisible()
})

test('Modifier : la devise des cryptos se change en un appui et se retient', async ({ dock }) => {
  await openEditor(dock)
  await dock.getByTestId('mk-currency-USD').tap()
  await expect(dock.getByTestId('mk-currency-USD')).toHaveAttribute('aria-pressed', 'true')
  await dock.getByTestId('mk-done').tap()
  await expect(dock.getByTestId('price-crypto:BTC')).toContainText('$')
  await dock.reload()
  await expect(dock.getByTestId('price-crypto:BTC')).toContainText('$')
  await dock.getByTestId('markets-edit').tap()
  await dock.getByTestId('mk-currency-EUR').tap()
  await dock.getByTestId('mk-done').tap()
  await expect(dock.getByTestId('price-crypto:BTC')).toContainText('€')
})

test('Bourse : un cours dont le dernier échange est ancien le dit', async ({ dock }) => {
  await openEditor(dock)
  await dock.getByTestId('mk-kind-stock').tap()
  await addSuggested(dock, 'AAPL')
  await addSuggested(dock, 'MSTR')
  await dock.getByTestId('mk-done').tap()
  await expect(dock.getByTestId('lag-stock:AAPL')).toContainText('dernier échange il y a 15 min')
  await expect(dock.getByTestId('lag-stock:MSTR')).toHaveCount(0)
  await expect(dock.getByTestId('lag-crypto:BTC')).toHaveCount(0)
  await dock.getByTestId('quote-stock:AAPL').tap()
  await expect(dock.getByTestId('detail-meta')).toContainText('dernier échange il y a 15 min')
})

// --- détail --------------------------------------------------------------------------------------

test('Détail : toucher une valeur ouvre son graphique, les périodes se changent, « Liste » revient', async ({ dock }) => {
  await openMarkets(dock)
  await dock.getByTestId('quote-crypto:BTC').tap()
  await expect(dock.getByTestId('detail')).toBeVisible()
  await expect(dock.getByTestId('detail-name')).toHaveText('Bitcoin')
  await expect(dock.getByTestId('price-chart')).toBeVisible()
  expect(plain(await dock.getByTestId('chart-max').textContent())).toMatch(/€$/)
  await expect(dock.getByTestId('range-1d')).toHaveAttribute('aria-pressed', 'true')
  await shot(dock, '03-detail', 'e')

  const axis = async () => plain(await dock.locator('.times').first().textContent())
  const day = await axis()
  for (const range of ['5d', '1mo', '1y']) {
    await dock.getByTestId(`range-${range}`).tap()
    await expect(dock.getByTestId(`range-${range}`)).toHaveAttribute('aria-pressed', 'true')
    await expect(dock.getByTestId('price-chart')).toBeVisible()
    await expect.poll(axis).not.toBe(day)
  }
  await shot(dock, '04-detail-1an', 'e')
  await dock.getByTestId('detail-back').tap()
  await expect(dock.getByTestId('markets-list')).toBeVisible()
  await expect(dock.getByTestId('detail')).toHaveCount(0)
})

// --- Réglages ------------------------------------------------------------------------------------

test('Réglages : la carte Bourse dit combien de valeurs sont suivies et ouvre la même fenêtre', async ({ dock }) => {
  await dock.getByTestId('tab-settings').tap()
  await expect(dock.getByTestId('markets-count')).toContainText('2 valeurs suivies')
  await expect(dock.getByTestId('markets-count')).toContainText('cryptos cotées en euros')
  await expect(dock.getByTestId('markets-note')).toContainText('Mode démonstration')
  await expect(dock.getByTestId('refresh-15')).toHaveAttribute('aria-pressed', 'true')
  await dock.getByTestId('refresh-60').tap()
  await expect(dock.getByTestId('refresh-60')).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.getByTestId('refresh-15')).toHaveAttribute('aria-pressed', 'false')
  await dock.getByTestId('settings-markets').scrollIntoViewIfNeeded()
  await shot(dock, '07-reglages', 'e')
  await dock.getByTestId('choose-markets').tap()
  await expect(dock.getByTestId('mk-editor')).toBeVisible()
  await dock.getByTestId('mk-suggest-ETH').tap()
  await expect(dock.getByTestId('mk-confirm')).toHaveText('Déjà suivie')
  await dock.getByTestId('mk-done').tap()
  await expect(dock.getByTestId('markets-count')).toContainText('2 valeurs suivies')
  await dock.reload()
  await expect(dock.getByTestId('refresh-60')).toHaveAttribute('aria-pressed', 'true') // mémorisé par le Pi
})

// --- toucher et mise en page ----------------------------------------------------------------------

test('Bourse : huit valeurs, cibles tactiles d’au moins 48 px et aucun défilement de la page', async ({ dock }) => {
  await openEditor(dock)
  for (const symbol of ['SOL', 'DOGE']) await addSuggested(dock, symbol)
  await dock.getByTestId('mk-kind-stock').tap()
  for (const symbol of ['MSTR', 'AAPL', '^FCHI', 'CW8.PA']) await addSuggested(dock, symbol)
  await expect(dock.getByTestId('mk-chosen')).toContainText('8/20')
  expect(await smallTargets(dock), 'fenêtre de modification').toEqual([])
  await noPageScroll(dock)
  await shot(dock, '09-editeur-plein', 'e')
  await dock.getByTestId('mk-done').tap()

  await expect(dock.getByTestId('price-stock:^FCHI')).toHaveText(/\d/)
  await expect(dock.getByTestId('session-stock:^FCHI')).toContainText('Marché fermé')
  await expect(dock.getByTestId('session-stock:MSTR')).toContainText('Marché ouvert')
  await expect(dock.getByTestId('toast')).toHaveCount(0, { timeout: 8_000 }) // les messages d'ajout ont disparu
  expect(await smallTargets(dock), 'liste').toEqual([])
  await noPageScroll(dock)
  await shot(dock, '02-bourse-liste', 'e')

  await dock.getByTestId('quote-stock:MSTR').tap()
  await expect(dock.getByTestId('price-chart')).toBeVisible()
  expect(await smallTargets(dock), 'détail').toEqual([])
  await noPageScroll(dock)
})
