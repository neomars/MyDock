// Étape k : Divertissement (Freetv et Kiosque) et le petit bouton d'alimentation à droite de l'heure.
// Vrai Chromium 1024×600 tactile -> vrai dock -> assistant de lecture en démonstration (aucun VLC), faux Freebox
// (la liste des chaînes en local) et faux « smbclient » (scripts/demo) : rien ne sort de cette machine.

import type { Page } from '@playwright/test'
import { expect, goHome, openPage, shot, test, typeOnKeyboard } from './fixtures'

const noScroll = (page: Page) =>
  page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))

/** Attend que l'assistant de lecture soit connecté au dock (il démarre avec la pile, un instant après). */
async function helperReady(page: Page): Promise<void> {
  await openPage(page, 'media')
  await page.getByTestId('media-tv').tap()
  await expect(page.getByTestId('tv-list')).toBeVisible()
  await expect(page.getByTestId('tv-blocker')).toHaveCount(0)
  await goHome(page)
}

test('Accueil : « Divertissement » remplace « Alimentation », qui devient un petit bouton à droite de l’heure', async ({ dock }) => {
  await expect(dock.getByTestId('tile-media')).toContainText('Divertissement')
  await expect(dock.getByTestId('tile-power')).toHaveCount(0)
  const clock = (await dock.getByTestId('clock').boundingBox())!
  const power = (await dock.getByTestId('topbar-power').boundingBox())!
  expect(power.x, 'à droite de l’heure').toBeGreaterThan(clock.x + clock.width)
  expect(power.x + power.width, 'dans l’écran').toBeLessThanOrEqual(1024)
  expect(power.width, 'petit').toBeLessThanOrEqual(64)
  expect(power.width, 'mais franchement touchable').toBeGreaterThanOrEqual(48)
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await shot(dock, '01-accueil-divertissement', 'k')

  await dock.getByTestId('topbar-power').tap()
  await expect(dock.getByTestId('page-power')).toBeVisible()
  await expect(dock.getByTestId('power-card')).toBeVisible()
  await shot(dock, '02-alimentation-par-la-barre', 'k')
  await dock.getByTestId('topbar-power').tap() // la barre du haut reste la même sur toutes les pages
  await expect(dock.getByTestId('page-power')).toBeVisible()
})

test('Divertissement : le menu propose Freetv et Kiosque', async ({ dock }) => {
  await openPage(dock, 'media')
  await expect(dock.getByTestId('media-tv')).toContainText('Freetv')
  await expect(dock.getByTestId('media-files')).toContainText('Kiosque')
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await dock.waitForTimeout(300)
  await shot(dock, '03-divertissement-menu', 'k')
})

test('Freetv : les chaînes de la Freebox, la recherche (nom sans accent ou numéro) et le réglage HD', async ({ dock }) => {
  await openPage(dock, 'media')
  await dock.getByTestId('media-tv').tap()
  await expect(dock.getByTestId('tv-list')).toBeVisible()
  await expect(dock.getByTestId('tv-count')).toContainText('27 chaînes')
  await expect(dock.locator('[data-testid^=tv-ch-]').first()).toContainText('TF1')
  await dock.waitForTimeout(300)
  await shot(dock, '04-freetv-liste', 'k')

  await dock.getByTestId('tv-search').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  await typeOnKeyboard(dock, 'serie')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('tv-count')).toContainText('1 chaîne pour « serie »')
  await expect(dock.locator('[data-testid^=tv-ch-]')).toHaveCount(1)
  await expect(dock.locator('[data-testid^=tv-ch-]').first()).toContainText('TF1 Séries Films') // sans accent, comme on tape
  await dock.getByTestId('tv-clear').tap()

  await dock.getByTestId('tv-search').tap()
  await typeOnKeyboard(dock, '6')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.locator('[data-testid^=tv-ch-]').first()).toContainText('M6') // la chaîne 6 d'abord
  await dock.getByTestId('tv-clear').tap()

  await expect(dock.getByTestId('tv-hd')).toHaveAttribute('aria-pressed', 'false')
  await dock.getByTestId('tv-hd').tap()
  await expect(dock.getByTestId('tv-hd')).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.getByTestId('tv-count')).toContainText('en HD')
  await dock.reload() // le dock garde le choix, pas le navigateur
  await openPage(dock, 'media')
  await dock.getByTestId('media-tv').tap()
  await expect(dock.getByTestId('tv-hd')).toHaveAttribute('aria-pressed', 'true')
  await dock.getByTestId('tv-hd').tap()
})

test('Freetv : toucher une chaîne la lance ; la télécommande montre le direct, met en pause, règle le volume et arrête', async ({ dock }) => {
  await helperReady(dock)
  await openPage(dock, 'media')
  await dock.getByTestId('media-tv').tap()
  await dock.locator('[data-testid^=tv-ch-]').first().tap() // TF1

  const remote = dock.getByTestId('remote')
  await expect(remote).toBeVisible()
  await expect(dock.getByTestId('remote-title')).toHaveText('TF1')
  await expect(dock.getByTestId('remote-subtitle')).toContainText('1 · SD')
  await expect(remote).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await expect(dock.getByTestId('remote-state')).toHaveText('En direct')
  await expect(dock.getByTestId('remote-progress')).toHaveCount(0) // un direct n'a ni durée ni position
  await expect(dock.getByTestId('remote-back')).toBeDisabled() // ni retour en arrière
  await expect(dock.getByTestId('page-media')).toBeVisible()
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await shot(dock, '05-telecommande-direct', 'k')

  await dock.getByTestId('remote-toggle').tap()
  await expect(remote).toHaveAttribute('data-state', 'paused')
  await expect(dock.getByTestId('remote-state')).toHaveText('En pause')
  await dock.getByTestId('remote-toggle').tap()
  await expect(remote).toHaveAttribute('data-state', 'playing')

  const slider = dock.getByTestId('remote-volume')
  const box = (await slider.boundingBox())!
  await slider.tap({ position: { x: box.width * 0.3, y: box.height / 2 } })
  await expect(dock.getByTestId('remote-volume-value')).not.toHaveText('70 %')

  await goHome(dock)
  await expect(dock.getByTestId('tile-media-sub')).toHaveText('TF1') // l'accueil dit ce qui joue
  await dock.getByTestId('tile-media').tap()
  await expect(remote).toBeVisible() // la page s'ouvre sur la télécommande tant que ça joue

  await dock.getByTestId('remote-stop').tap()
  await expect(dock.getByTestId('remote')).toHaveCount(0)
  await expect(dock.getByTestId('media-tv')).toBeVisible()
  await goHome(dock)
  await expect(dock.getByTestId('tile-media-sub')).toHaveCount(0)
})

test('Freetv : changer de chaîne remplace la lecture, « Choisir autre chose » garde la vidéo qui joue', async ({ dock }) => {
  await helperReady(dock)
  await openPage(dock, 'media')
  await dock.getByTestId('media-tv').tap()
  await dock.locator('[data-testid^=tv-ch-]').nth(1).tap() // France 2
  await expect(dock.getByTestId('remote-title')).toHaveText('France 2')
  await dock.getByTestId('media-other').tap()
  await expect(dock.getByTestId('media-now')).toContainText('France 2')
  await dock.getByTestId('media-tv').tap()
  await dock.locator('[data-testid^=tv-ch-]').nth(2).tap() // France 3
  await expect(dock.getByTestId('remote-title')).toHaveText('France 3')
  await dock.getByTestId('remote-stop').tap()
})

test('Kiosque : les partages, les dossiers, une vidéo qu’on lance, la télécommande avance et recule', async ({ dock }) => {
  await helperReady(dock)
  await openPage(dock, 'media')
  await dock.getByTestId('media-files').tap()
  await expect(dock.getByTestId('files-shares')).toBeVisible()
  await expect(dock.getByTestId('share-Disque dur')).toBeVisible()
  await expect(dock.getByTestId('share-Enregistrements')).toBeVisible()
  await expect(dock.getByTestId('files-where')).toHaveText('Partages')
  await shot(dock, '06-kiosque-partages', 'k')

  await dock.getByTestId('share-Disque dur').tap()
  await expect(dock.getByTestId('dir-Films')).toBeVisible()
  await expect(dock.getByTestId('files-where')).toHaveText('Disque dur')
  await expect(dock.getByTestId('video-Photos de vacances.mkv')).toContainText('700 Mo')
  await dock.getByTestId('dir-Films').tap()
  await expect(dock.getByTestId('files-where')).toHaveText('Disque dur › Films')
  await expect(dock.getByTestId("video-Un film d'été (2020).mkv")).toContainText('1,4 Go')
  await expect(dock.getByTestId('video-lisez-moi.txt')).toHaveCount(0) // seuls les dossiers et les vidéos
  await dock.waitForTimeout(300)
  await shot(dock, '07-kiosque-dossier', 'k')

  await dock.getByTestId('dir-Dessins animés').tap()
  await expect(dock.getByTestId('files-where')).toHaveText('Disque dur › Films › Dessins animés')
  await dock.getByTestId('files-up').tap()
  await expect(dock.getByTestId('files-where')).toHaveText('Disque dur › Films')

  await dock.getByTestId("video-Un film d'été (2020).mkv").tap()
  const remote = dock.getByTestId('remote')
  await expect(remote).toBeVisible()
  await expect(dock.getByTestId('remote-title')).toHaveText("Un film d'été (2020)")
  await expect(dock.getByTestId('remote-subtitle')).toHaveText('Disque dur/Films')
  await expect(remote).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await expect(dock.getByTestId('remote-progress')).toBeVisible()
  await expect(dock.getByTestId('remote-state')).toContainText('/ 1:30:00')
  await dock.getByTestId('remote-fwd5').tap()
  await expect.poll(async () => (await dock.getByTestId('remote-position').textContent()) ?? '', { timeout: 6_000 }).toMatch(/^5:0\d$/)
  await dock.getByTestId('remote-back').tap()
  await expect.poll(async () => (await dock.getByTestId('remote-position').textContent()) ?? '', { timeout: 6_000 }).toMatch(/^4:3\d$/)
  await shot(dock, '08-telecommande-fichier', 'k')

  await dock.getByTestId('remote-video').tap() // « Revenir à la vidéo » : VLC repasse devant
  await expect(remote).toHaveAttribute('data-front', 'video')
  await dock.getByTestId('remote-stop').tap()
  await expect(dock.getByTestId('remote')).toHaveCount(0)
})

test('Kiosque : un dossier ou un partage introuvable est dit en français, avec de quoi réessayer', async ({ dock }) => {
  await openPage(dock, 'media')
  await dock.getByTestId('media-files').tap()
  await dock.getByTestId('share-Disque dur').tap()
  await dock.getByTestId('dir-Séries').tap()
  await expect(dock.getByTestId('dir-Saison 1')).toBeVisible()
  await dock.getByTestId('dir-Saison 1').tap() // le faux serveur ne connaît pas ce sous-dossier
  await expect(dock.getByTestId('files-error')).toContainText("Ce dossier n'existe plus")
  await expect(dock.getByTestId('files-retry')).toBeVisible()
  await shot(dock, '09-kiosque-erreur', 'k')
  await dock.getByTestId('files-up').tap() // on remonte : le dossier qui n'a pu s'ouvrir n'est jamais « entré »
  await expect(dock.getByTestId('files-where')).toHaveText('Disque dur')
  await expect(dock.getByTestId('dir-Séries')).toBeVisible()
})

test('Réglages : la carte Divertissement garde l’adresse, le serveur, l’identifiant et le mot de passe (jamais affiché) et teste chaque maillon', async ({ dock }) => {
  await helperReady(dock)
  await openPage(dock, 'settings')
  const card = dock.getByTestId('settings-media')
  await card.scrollIntoViewIfNeeded()
  await expect(dock.getByTestId('media-url')).toContainText('/freeboxtv/playlist.m3u')
  await expect(dock.getByTestId('media-host')).toHaveText('freebox.demo')
  await expect(dock.getByTestId('media-user')).toHaveText('accès libre')
  await expect(dock.getByTestId('media-password')).toHaveText('aucun')

  await dock.getByTestId('edit-media-user').tap()
  await typeOnKeyboard(dock, 'freebox')
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('media-user')).toHaveText('freebox')

  await dock.getByTestId('edit-media-password').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  await typeOnKeyboard(dock, 'motdepasse')
  await expect(dock.getByTestId('keyboard-value')).not.toContainText('motdepasse') // masqué à la saisie
  await dock.getByTestId('keyboard-ok').tap()
  await expect(dock.getByTestId('media-password')).toHaveText('••••••••')
  await expect(card).not.toContainText('motdepasse') // jamais affiché

  await dock.getByTestId('media-test').tap()
  await expect(dock.getByTestId('media-results')).toBeVisible({ timeout: 20_000 })
  await expect(dock.getByTestId('media-result-tv')).toContainText('27 chaînes lues')
  await expect(dock.getByTestId('media-result-files')).toContainText('Partages : Disque dur, Enregistrements')
  await expect(dock.getByTestId('media-result-player')).toContainText('VLC est prêt')
  await card.scrollIntoViewIfNeeded()
  await expect(dock.getByTestId('toast')).toHaveCount(0, { timeout: 8_000 }) // la photo sans bulle par-dessus
  await dock.waitForTimeout(300)
  await shot(dock, '10-reglages-divertissement', 'k')

  await dock.getByTestId('clear-media-password').tap()
  await expect(dock.getByTestId('media-password')).toHaveText('aucun')
})

test('Réglages : une adresse de liste invalide est refusée sans rien changer', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('settings-media').scrollIntoViewIfNeeded()
  const before = await dock.getByTestId('media-url').textContent()
  await dock.getByTestId('edit-media-url').tap()
  await dock.getByTestId('keyboard-cancel').tap()
  await expect(dock.getByTestId('media-url')).toHaveText(before ?? '')
})
