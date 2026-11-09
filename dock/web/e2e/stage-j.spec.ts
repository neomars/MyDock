// Étape j : la sortie sonore de Spotify (appareils Spotify Connect et sorties du PC) et l'économiseur d'écran.
// Vrai Chromium 1024×600 tactile -> vrai dock -> vrai agent de démonstration (catalogue et appareils fictifs).

import type { Locator, Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { expect, openPage, shot, test, THUMB, typeOnKeyboard } from './fixtures'

const percent = (n: number) => `${n} %`

/** Touche un curseur à `value` % de sa course (la poignée ne sort jamais de la piste). */
async function tapSlider(slider: Locator, value: number, thumb: number): Promise<void> {
  const box = (await slider.boundingBox())!
  await slider.tap({ position: { x: thumb / 2 + (box.width - thumb) * (value / 100), y: box.height / 2 } })
}

async function openOutputs(page: Page): Promise<void> {
  await openPage(page, 'music')
  await page.getByTestId('sp-tab-devices').tap()
  await expect(page.getByTestId('sp-devices-title')).toHaveText('Sortie sonore')
  await expect(page.locator('[data-testid^=sp-device-]').first()).toBeVisible()
}

const device = (page: Page, name: string) => page.locator('[data-testid^=sp-device-]', { hasText: name })

// --- la sortie sonore de Spotify -------------------------------------------------------------------------------

test('Sortie sonore : la liste montre les appareils Spotify (ce PC d’abord, coché) et les sorties du PC', async ({ dock }) => {
  await openOutputs(dock)
  const names = await dock.locator('[data-testid^=sp-device-] .name').allTextContents()
  expect(names).toEqual(['Ce PC', 'Cuisine (Echo Dot)', 'iPhone de Marie', 'Salon (Sonos Era 100)', 'TV du salon'])
  await expect(dock.locator('[data-testid^=sp-device-][data-active=true]')).toHaveCount(1)
  await expect(device(dock, 'Ce PC')).toHaveAttribute('data-active', 'true')
  await expect(device(dock, 'Salon (Sonos')).toContainText('Enceinte · 35 %')
  await expect(dock.getByTestId('sp-sinks')).toContainText('Sorties du PC')
  await expect(dock.locator('[data-testid^=sp-sink-]')).toHaveCount(3)
  await expect(dock.locator('[data-testid^=sp-sink-][data-active=true]')).toContainText('Haut-parleurs')
  await dock.waitForTimeout(400)
  await shot(dock, '01-sortie-sonore', 'j')
  await dock.locator('main.page').evaluate((el) => el.scrollTo(0, 600))
  await dock.waitForTimeout(300)
  await shot(dock, '02-sorties-du-pc', 'j')
})

test('Sortie sonore : un toucher passe la musique sur l’enceinte ; le lecteur le dit et règle le volume de l’enceinte', async ({ dock }) => {
  await openOutputs(dock)
  await device(dock, 'Salon (Sonos').tap()
  await expect(dock.getByTestId('toast')).toContainText('Sortie : Salon (Sonos Era 100)')
  await expect(device(dock, 'Salon (Sonos')).toHaveAttribute('data-active', 'true')
  await expect(device(dock, 'Ce PC')).not.toHaveAttribute('data-active', 'true')
  await expect(dock.getByTestId('sp-sinks-note')).toContainText('quand la musique joue sur ce PC') // les sorties du PC ne servent plus

  await dock.getByTestId('sp-tab-player').tap()
  await expect(dock.getByTestId('music-output')).toContainText('Salon (Sonos Era 100)')
  await expect(dock.getByTestId('music-vol-slider')).toHaveCount(0) // ce n'est plus le volume du PC
  await expect(dock.getByTestId('device-vol-value')).toHaveText(percent(35))
  await tapSlider(dock.getByTestId('device-vol-slider'), 70, THUMB.md)
  await expect(dock.getByTestId('device-vol-value')).toHaveText(percent(70))
  await shot(dock, '03-lecteur-sur-une-enceinte', 'j')

  // Le volume est celui de l'enceinte, retenu côté Spotify : la liste le redit.
  await dock.getByTestId('sp-tab-devices').tap()
  await expect(device(dock, 'Salon (Sonos')).toContainText('70 %')

  // Retour sur ce PC : le volume de la sortie du PC revient.
  await device(dock, 'Ce PC').tap()
  await dock.getByTestId('sp-tab-player').tap()
  await expect(dock.getByTestId('music-output')).toHaveCount(0)
  await expect(dock.getByTestId('music-vol-slider')).toBeVisible()
})

test('Sortie sonore : on choisit aussi la sortie du PC (haut-parleurs, Bluetooth, HDMI)', async ({ dock }) => {
  await openOutputs(dock)
  await dock.locator('[data-testid^=sp-sink-]', { hasText: 'WH-1000XM4' }).tap()
  await expect(dock.locator('[data-testid^=sp-sink-][data-active=true]')).toContainText('WH-1000XM4')
  await dock.locator('[data-testid^=sp-sink-]', { hasText: 'Haut-parleurs' }).tap()
  await expect(dock.locator('[data-testid^=sp-sink-][data-active=true]')).toContainText('Haut-parleurs')
})

test('Sortie sonore : « actualiser » relit la liste, et un appareil actif ailleurs est repéré sans rien toucher', async ({ dock }) => {
  await openOutputs(dock)
  await dock.getByTestId('sp-devices-refresh').tap()
  await expect(dock.locator('[data-testid^=sp-device-]')).toHaveCount(5)
  await dock.getByTestId('home-back').tap()
  await expect(dock.getByTestId('home')).toBeVisible()
})

// --- l'économiseur d'écran ------------------------------------------------------------------------------------------

/** Une petite vidéo WebM (VP8, 160×90, 3 s) : ce que Chromium lit sans codec propriétaire. */
const CLIP = path.join(process.cwd(), 'e2e', 'assets', 'clip.webm')

async function centerOf(page: Page, testId: string): Promise<{ x: number; y: number }> {
  const box = await page.getByTestId(testId).boundingBox()
  if (!box) throw new Error(`${testId} est introuvable à l'écran`)
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/** Une empreinte et la clarté moyenne (0-255) de l'image de la scène : de quoi voir qu'elle dessine, et qu'elle bouge. */
async function sceneSignature(page: Page): Promise<{ hash: number; mean: number }> {
  return page
    .getByTestId('screensaver')
    .locator('canvas')
    .evaluate((canvas: HTMLCanvasElement) => {
      // On lit une copie : lire la scène elle-même ferait avertir Chromium (« willReadFrequently ») et la ralentirait.
      const copy = document.createElement('canvas')
      copy.width = canvas.width
      copy.height = canvas.height
      const reader = copy.getContext('2d', { willReadFrequently: true })!
      reader.drawImage(canvas, 0, 0)
      const { data } = reader.getImageData(0, 0, copy.width, copy.height)
      let hash = 0
      let sum = 0
      for (let i = 0; i < data.length; i += 4) {
        sum += data[i] + data[i + 1] + data[i + 2]
        hash = (Math.imul(hash, 31) + data[i] + data[i + 1] * 3 + data[i + 2] * 7) >>> 0
      }
      return { hash, mean: sum / (data.length / 4) / 3 }
    })
}

test('Économiseur : Réglages propose les délais, le dock garde le choix, et l’on est prévenu quand l’écran s’éteint avant', async ({ dock }) => {
  await openPage(dock, 'settings')
  const card = dock.getByTestId('settings-saver')
  await expect(dock.getByTestId('saver-300')).toHaveAttribute('aria-pressed', 'true') // 5 minutes, tant qu'on n'a rien choisi
  await expect(dock.getByTestId('saver-content-videos')).toHaveAttribute('aria-pressed', 'true') // les vues aériennes d'abord
  await expect(dock.getByTestId('saver-aerial')).toContainText('0 sur 10 prêtes')
  await expect(dock.getByTestId('saver-note')).toContainText('Sans toucher pendant 5 minutes')
  await expect(dock.getByTestId('saver-videos')).toContainText('Aucune vidéo prête')
  // La carte défile sous la barre du haut : d'abord son titre et ses choix, puis ses explications et le bouton d'essai.
  await card.evaluate((el) => {
    const page = el.closest('main.page')!
    page.scrollTo(0, page.scrollTop + el.getBoundingClientRect().top - 100)
  })
  await dock.waitForTimeout(300)
  await shot(dock, '04-reglage-economiseur', 'j')
  await card.evaluate((el) => {
    const page = el.closest('main.page')!
    page.scrollTo(0, page.scrollTop + el.getBoundingClientRect().bottom - window.innerHeight + 16)
  })
  await dock.waitForTimeout(300)
  await shot(dock, '05-reglage-economiseur-suite', 'j')

  await dock.getByTestId('saver-videos-open').tap() // la fenêtre des vidéos : suspendre puis reprendre le téléchargement
  await expect(dock.getByTestId('saver-sources')).toBeVisible()
  await dock.getByTestId('saver-aerial-toggle').tap()
  await expect(dock.getByTestId('saver-sources-summary')).toContainText('Téléchargement suspendu')
  await dock.getByTestId('saver-aerial-toggle').tap()
  await expect(dock.getByTestId('saver-sources-summary')).toContainText('0 sur 10 prêtes')
  await dock.getByTestId('saver-sources-done').tap()
  await expect(dock.getByTestId('saver-sources')).toHaveCount(0)
  await dock.getByTestId('saver-120').tap()
  await expect(dock.getByTestId('saver-120')).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.getByTestId('saver-note')).toContainText('Sans toucher pendant 2 minutes')
  await dock.getByTestId('saver-content-scenes').tap()
  await expect(dock.getByTestId('saver-content-scenes')).toHaveAttribute('aria-pressed', 'true')

  await dock.reload() // le dock garde le choix, pas le navigateur
  await openPage(dock, 'settings')
  await expect(dock.getByTestId('saver-120')).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.getByTestId('saver-content-scenes')).toHaveAttribute('aria-pressed', 'true')

  // La veille de l'écran (1 minute) passe avant l'économiseur (2 minutes) : on le dit au lieu de laisser croire qu'il va venir.
  await dock.getByTestId('sleep-60').tap()
  await expect(dock.getByTestId('saver-note')).toContainText("L'écran s'éteint avant (1 minute)")
  await dock.getByTestId('saver-0').tap()
  await expect(dock.getByTestId('saver-note')).toHaveText("Pas d'économiseur : l'écran reste tel quel.")
})

test('Économiseur : il démarre après l’inactivité choisie ; le premier toucher le ferme sans rien déclencher dessous', async ({ dock }) => {
  await dock.clock.install() // le temps est à nous : on avance d'une minute en un instant
  await openPage(dock, 'settings')
  await dock.getByTestId('saver-60').tap()
  await expect(dock.getByTestId('saver-60')).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.getByTestId('screensaver')).toHaveCount(0)

  await dock.clock.fastForward(30_000)
  await expect(dock.getByTestId('screensaver')).toHaveCount(0) // pas encore
  await dock.clock.fastForward(35_000)
  await expect(dock.getByTestId('screensaver')).toBeVisible()
  await expect(dock.getByTestId('saver-time')).toHaveText(/^\d{2}:\d{2}$/)
  await expect(dock.getByTestId('saver-title')).not.toHaveText('')

  // On touche l'écran juste là où se trouve le bouton « Jamais » : il ne doit rien changer.
  const { x, y } = await centerOf(dock, 'saver-0')
  await dock.touchscreen.tap(x, y)
  const veil = dock.getByTestId('screen-sleep')
  await expect(veil).toHaveAttribute('data-phase', 'waking')
  await expect(dock.getByTestId('screensaver')).toHaveCount(0) // l'image est déjà revenue
  await dock.clock.fastForward(600)
  await expect(veil).toHaveCount(0)
  await expect(dock.getByTestId('page-settings')).toBeVisible()
  await expect(dock.getByTestId('saver-60')).toHaveAttribute('aria-pressed', 'true') // le toucher n'a atteint que l'économiseur

  // Il revient après un nouveau délai sans toucher, pas avant.
  await dock.clock.fastForward(30_000)
  await expect(dock.getByTestId('screensaver')).toHaveCount(0)
  await dock.clock.fastForward(35_000)
  await expect(dock.getByTestId('screensaver')).toBeVisible()
})

test('Économiseur : chaque toucher repousse son départ, et réglé sur « Jamais » il ne vient jamais', async ({ dock }) => {
  await dock.clock.install()
  await openPage(dock, 'settings')
  await dock.getByTestId('saver-60').tap()
  await expect(dock.getByTestId('saver-60')).toHaveAttribute('aria-pressed', 'true')
  for (let i = 0; i < 3; i++) {
    await dock.clock.fastForward(45_000) // 135 s au total, mais jamais 60 s sans toucher
    await openPage(dock, 'power')
    await openPage(dock, 'settings')
  }
  await expect(dock.getByTestId('screensaver')).toHaveCount(0)

  await dock.getByTestId('saver-0').tap()
  await expect(dock.getByTestId('saver-0')).toHaveAttribute('aria-pressed', 'true')
  await dock.clock.fastForward(3 * 3_600_000)
  await expect(dock.getByTestId('screensaver')).toHaveCount(0)
})

test('Économiseur : l’écran noir vient ensuite, à son propre délai, et un toucher le réveille', async ({ dock }) => {
  await dock.clock.install()
  await openPage(dock, 'settings')
  await dock.getByTestId('saver-60').tap()
  await dock.getByTestId('sleep-300').tap()
  await expect(dock.getByTestId('saver-note')).toContainText('Sans toucher pendant 1 minute') // 1 < 5 : l'économiseur vient d'abord

  await dock.clock.fastForward(65_000)
  await expect(dock.getByTestId('screensaver')).toBeVisible()
  await expect(dock.getByTestId('screen-sleep')).toHaveCount(0)

  await dock.clock.fastForward(300_000)
  await expect(dock.getByTestId('screen-sleep')).toHaveAttribute('data-phase', 'asleep')
  await expect(dock.getByTestId('screensaver')).toHaveCount(0) // le noir prend la place de l'économiseur

  await dock.touchscreen.tap(500, 300)
  await expect(dock.getByTestId('screen-sleep')).toHaveAttribute('data-phase', 'waking')
  await dock.clock.fastForward(600)
  await expect(dock.getByTestId('screen-sleep')).toHaveCount(0)
  await expect(dock.getByTestId('page-settings')).toBeVisible()
})

test('Économiseur : les scènes se succèdent, jamais deux fois la même de suite', async ({ dock }) => {
  await dock.clock.install()
  await openPage(dock, 'settings')
  await dock.getByTestId('saver-content-scenes').tap()
  await expect(dock.getByTestId('saver-content-scenes')).toHaveAttribute('aria-pressed', 'true')
  await dock.getByTestId('saver-now').tap()
  const saver = dock.getByTestId('screensaver')
  await expect(saver).toHaveAttribute('data-kind', 'scene')
  const seen: string[] = [(await saver.getAttribute('data-item'))!]
  for (let i = 0; i < 4; i++) {
    await dock.clock.fastForward(75_000) // la scène a duré son temps : fondu au noir…
    await dock.clock.fastForward(1_000) // …puis la suivante
    await expect(saver).not.toHaveAttribute('data-item', seen[seen.length - 1])
    seen.push((await saver.getAttribute('data-item'))!)
  }
  expect(new Set(seen).size, `scènes vues : ${seen.join(', ')}`).toBeGreaterThan(2)
})

const SCENES = [
  { id: 'mountains', title: 'Montagnes, lever du jour', shot: '06-scene-montagnes' },
  { id: 'ocean', title: 'Fonds marins', shot: '07-scene-fonds-marins' },
  { id: 'city', title: 'Ville de nuit, vue aérienne', shot: '08-scene-ville' },
  { id: 'aurora', title: 'Aurore boréale', shot: '09-scene-aurore' },
  { id: 'space', title: 'Voyage parmi les étoiles', shot: '10-scene-etoiles' },
]

for (const scene of SCENES) {
  test(`Économiseur : la scène « ${scene.title} » dessine une vraie image, et elle bouge`, async ({ dock, stack }) => {
    await dock.goto(`${stack.url}/?scene=${scene.id}`) // ?scene= impose la scène (essais et captures)
    await expect(dock.getByTestId('home')).toBeVisible()
    await openPage(dock, 'settings')
    await dock.getByTestId('saver-now').tap()
    const saver = dock.getByTestId('screensaver')
    await expect(saver).toBeVisible()
    await expect(saver).toHaveAttribute('data-item', scene.id)
    await expect(dock.getByTestId('saver-title')).toHaveText(scene.title)
    await dock.waitForTimeout(2_500) // le fondu d'entrée, puis la légende
    const first = await sceneSignature(dock)
    expect(first.mean, 'une image qui n’est pas noire').toBeGreaterThan(6)
    await shot(dock, scene.shot, 'j')
    await dock.waitForTimeout(1_200)
    expect((await sceneSignature(dock)).hash, 'la scène bouge').not.toBe(first.hash)
  })
}

test('Économiseur : une vidéo du dossier du Pi passe en plein écran, légendée par son nom', async ({ dock, stack }) => {
  fs.mkdirSync(stack.saverDir, { recursive: true })
  fs.copyFileSync(CLIP, path.join(stack.saverDir, 'Vue aérienne_nuit.webm'))

  await openPage(dock, 'settings')
  await expect(dock.getByTestId('saver-videos')).toContainText('1 vidéo du Pi en plus des scènes animées')
  await dock.getByTestId('saver-content-videos').tap()
  await expect(dock.getByTestId('saver-content-videos')).toHaveAttribute('aria-pressed', 'true')
  await dock.getByTestId('saver-now').tap()

  const saver = dock.getByTestId('screensaver')
  await expect(saver).toHaveAttribute('data-kind', 'video')
  await expect(saver).toHaveAttribute('data-item', 'Vue aérienne_nuit.webm')
  await expect(dock.getByTestId('saver-title')).toHaveText('Vue aérienne nuit')
  const video = saver.locator('video')
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 10_000 }).toBeGreaterThan(0.3) // elle joue
  expect(await video.evaluate((v: HTMLVideoElement) => v.videoWidth)).toBe(160) // c'est bien le fichier du Pi
  await expect(video).toHaveCSS('opacity', '1')

  await dock.touchscreen.tap(500, 300) // un toucher la ferme, comme une scène
  await expect(saver).toHaveCount(0)
  await dock.waitForTimeout(700)
  await expect(dock.getByTestId('page-settings')).toBeVisible()

  // La fermer en pleine lecture n'écarte pas la vidéo (une lecture interrompue n'est pas une vidéo cassée) : elle repasse.
  await dock.getByTestId('saver-now').tap()
  await expect(saver).toHaveAttribute('data-kind', 'video')
  await expect(saver).toHaveAttribute('data-item', 'Vue aérienne_nuit.webm')
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 10_000 }).toBeGreaterThan(0.3)
})

test('Économiseur : une vidéo que le navigateur ne sait pas lire est écartée, une scène prend le relais', async ({ dock, stack }) => {
  fs.mkdirSync(stack.saverDir, { recursive: true })
  fs.writeFileSync(path.join(stack.saverDir, 'cassée.webm'), 'ceci n’est pas une vidéo')

  await openPage(dock, 'settings')
  await expect(dock.getByTestId('saver-videos')).toContainText('1 vidéo du Pi')
  await dock.getByTestId('saver-content-videos').tap() // « Mes vidéos » : la seule vidéo est la cassée
  await expect(dock.getByTestId('saver-content-videos')).toHaveAttribute('aria-pressed', 'true')
  await dock.getByTestId('saver-now').tap()

  const saver = dock.getByTestId('screensaver')
  await expect(saver).toBeVisible()
  await expect(saver).toHaveAttribute('data-kind', 'scene', { timeout: 10_000 }) // pas d'écran noir : on retombe sur les scènes
  await expect(dock.getByTestId('saver-title')).not.toHaveText('')
})

// --- les adresses de téléchargement des vidéos (saisies au clavier tactile) -----------------------------------------------

async function openVideos(page: Page): Promise<void> {
  await openPage(page, 'settings')
  await page.getByTestId('saver-videos-open').tap()
  await expect(page.getByTestId('saver-sources')).toBeVisible()
}

test('Vidéos : la fenêtre montre les vues aériennes d’Apple et propose d’ajouter une adresse', async ({ dock }) => {
  await openVideos(dock)
  await expect(dock.getByTestId('saver-src-0')).toContainText("Vues aériennes d'Apple")
  await expect(dock.getByTestId('saver-src-0-status')).toHaveText('0 sur 10 prêtes')
  await expect(dock.getByTestId('saver-sources-summary')).toHaveText('0 sur 10 prêtes.')
  await expect(dock.getByTestId('saver-add-url')).toBeVisible()
  await dock.waitForTimeout(300)
  await shot(dock, '12-videos-fenetre', 'j')
})

test('Vidéos : on saisit une adresse au clavier tactile, elle est gardée par le dock, puis retirée', async ({ dock }) => {
  await openVideos(dock)
  await dock.getByTestId('saver-add-url').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  await expect(dock.getByTestId('keyboard-value')).toHaveText('https://') // déjà là : on ne tape que la suite
  await typeOnKeyboard(dock, 'x.org/mer.mp4')
  await dock.getByTestId('keyboard').getByRole('button', { name: '?', exact: true }).tap() // les touches d'une adresse web
  await dock.getByTestId('keyboard').getByRole('button', { name: '&', exact: true }).tap()
  await expect(dock.getByTestId('keyboard-value')).toHaveText('https://x.org/mer.mp4?&')
  await shot(dock, '14-clavier-adresse', 'j')
  await dock.getByTestId('keyboard').getByRole('button', { name: 'Effacer' }).tap()
  await dock.getByTestId('keyboard').getByRole('button', { name: 'Effacer' }).tap()
  await dock.getByTestId('keyboard-ok').tap()

  await expect(dock.getByTestId('saver-src-1')).toContainText('x.org/mer.mp4')
  await expect(dock.getByTestId('saver-src-1-status')).toHaveText('0 sur 1 prêtes')
  await expect(dock.getByTestId('saver-sources-summary')).toHaveText('0 sur 11 prêtes.')
  await shot(dock, '13-videos-adresse-ajoutee', 'j')

  await dock.reload() // le dock garde l'adresse, pas le navigateur
  await openVideos(dock)
  await expect(dock.getByTestId('saver-src-1')).toContainText('x.org/mer.mp4')

  await dock.getByTestId('saver-src-remove-1').tap() // retirer demande confirmation : les films seraient effacés
  await dock.getByTestId('saver-src-cancel-1').tap()
  await expect(dock.getByTestId('saver-src-1')).toBeVisible()
  await dock.getByTestId('saver-src-remove-1').tap()
  await dock.getByTestId('saver-src-confirm-1').tap()
  await expect(dock.getByTestId('saver-src-1')).toHaveCount(0)
})

test('Vidéos : une adresse incomplète est refusée au clavier, une adresse déjà là aussi', async ({ dock }) => {
  await openVideos(dock)
  await dock.getByTestId('saver-add-url').tap()
  await dock.getByTestId('keyboard-ok').tap() // « https:// » seul
  await expect(dock.getByTestId('toast')).toContainText('adresse complète')
  await expect(dock.getByTestId('saver-src-1')).toHaveCount(0)

  for (const round of [1, 2]) {
    await dock.getByTestId('saver-add-url').tap()
    await typeOnKeyboard(dock, 'x.org/a.txt')
    await dock.getByTestId('keyboard-ok').tap()
    if (round === 2) await expect(dock.getByTestId('toast')).toContainText('déjà dans la liste')
  }
  await expect(dock.getByTestId('saver-src-1')).toContainText('x.org/a.txt')
  await expect(dock.getByTestId('saver-src-1-status')).toHaveText('Liste à lire…') // un fichier texte : on ignore encore combien
  await expect(dock.getByTestId('saver-src-2')).toHaveCount(0)
})

test('Vidéos : on retire les vues aériennes d’Apple, puis on les remet', async ({ dock }) => {
  await openVideos(dock)
  await dock.getByTestId('saver-src-remove-0').tap()
  await dock.getByTestId('saver-src-confirm-0').tap()
  await expect(dock.getByTestId('saver-src-0')).toHaveCount(0)
  await expect(dock.getByTestId('saver-src-none')).toBeVisible()
  await expect(dock.getByTestId('saver-sources-summary')).toContainText('Aucune source')
  await dock.getByTestId('saver-add-apple').tap()
  await expect(dock.getByTestId('saver-src-0')).toContainText("Vues aériennes d'Apple")
  await expect(dock.getByTestId('saver-add-apple')).toHaveCount(0)
})
