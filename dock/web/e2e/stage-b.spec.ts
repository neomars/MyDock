// Étape b : pages Système et Musique, mini-lecteur. Vrai Chromium 1024×600 tactile -> vrai dock -> vrai agent de démonstration.

import type { Page } from '@playwright/test'
import { expect, openPage, shot, test } from './fixtures'

/** Secondes d'une durée « 3:42 » ou « 1:02:15 ». */
const seconds = (text: string): number =>
  text
    .trim()
    .split(':')
    .map(Number)
    .reduce((total, part) => total * 60 + part, 0)

async function openMusic(dock: Page): Promise<void> {
  await openPage(dock, 'music')
  await expect(dock.getByTestId('music-player')).toBeVisible()
}

// --- Système -----------------------------------------------------------------------------------

test('Système : tuiles, graphique de 60 s, cœurs, stockage et batterie', async ({ dock }) => {
  await openPage(dock, 'system')
  await expect(dock.getByTestId('system-page')).toBeVisible()
  await expect(dock.getByTestId('cpu-value')).toContainText('%')
  await expect(dock.getByTestId('tile-mem')).toContainText('/ 32 Go')
  await expect(dock.getByTestId('net-down')).toContainText('↓')
  await expect(dock.getByTestId('net-up')).toContainText('↑')
  // Portable hybride de démonstration : la carte NVIDIA dort, on le dit au lieu d'afficher 0 %.
  await expect(dock.getByTestId('gpu-value')).toHaveText('En veille')
  await expect(dock.getByTestId('tile-gpu')).toContainText('RTX 4000 Ada dort')
  await expect(dock.getByTestId('cores')).toContainText('Cœurs (8)')
  await expect(dock.getByTestId('disk-Système')).toContainText('412 / 1000 Go')
  await expect(dock.getByTestId('disk-Jeux')).toContainText('1,2 / 2 To')
  await expect(dock.getByTestId('battery')).toContainText('82 %')
  // L'historique est fourni dès l'ouverture (l'agent le garde) : la courbe n'est pas vide.
  const lines = dock.getByTestId('chart-line')
  await expect(lines.first()).toBeVisible()
  const points = await lines.first().getAttribute('points')
  expect(points!.split(' ').length).toBeGreaterThanOrEqual(2)
  await shot(dock, '01-systeme', 'b')
})

test('Système : le graphique passe de la charge au réseau', async ({ dock }) => {
  await openPage(dock, 'system')
  await expect(dock.getByTestId('chart-card')).toContainText('Processeur')
  await dock.getByTestId('view-net').tap()
  await expect(dock.getByTestId('chart-card')).toContainText('Réception')
  await expect(dock.getByTestId('chart-card')).toContainText('échelle')
  await shot(dock, '02-systeme-reseau', 'b')
  await dock.getByTestId('view-load').tap()
  await expect(dock.getByTestId('chart-card')).toContainText('Mémoire')
})

test('Système : l’historique grandit seconde après seconde', async ({ dock }) => {
  await openPage(dock, 'system')
  const count = async () => ((await dock.getByTestId('chart-line').first().getAttribute('points')) ?? '').split(' ').length
  const before = await count()
  await expect.poll(count, { timeout: 8_000 }).toBeGreaterThan(before)
})

test('Système : les processus du système n’ont pas de bouton d’arrêt', async ({ dock }) => {
  await openPage(dock, 'system')
  await expect(dock.getByTestId('procs')).toContainText('brave')
  await expect(dock.getByTestId('kill-4101')).toBeVisible()
  await expect(dock.getByTestId('proc-2210')).toContainText('gnome-shell')
  await expect(dock.getByTestId('kill-2210')).toHaveCount(0) // gnome-shell : protégé
  await expect(dock.getByTestId('kill-812')).toHaveCount(0) // pipewire : protégé
})

test('Système : arrêter un processus demande confirmation (annuler ne fait rien, confirmer l’arrête)', async ({ dock }) => {
  await openPage(dock, 'system')
  await expect(dock.getByTestId('proc-9034')).toContainText('zoom-vide')
  await dock.getByTestId('kill-9034').tap()
  const dialog = dock.getByTestId('confirm')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('« zoom-vide » (PID 9034)')
  await expect(dialog).toContainText('démo, sans effet réel')
  await shot(dock, '03-arret-processus', 'b')

  await dock.getByTestId('confirm-cancel').tap()
  await expect(dock.getByTestId('toast')).toContainText('Annulé')
  await expect(dock.getByTestId('proc-9034')).toBeVisible()

  await dock.getByTestId('kill-9034').tap()
  await dock.getByTestId('confirm-ok').tap()
  await expect(dock.getByTestId('toast')).toContainText('« zoom-vide » est arrêté')
  await expect(dock.getByTestId('proc-9034')).toHaveCount(0, { timeout: 8_000 })
})

test('Système : les mesures disparaissent avec le PC et reviennent avec lui', async ({ dock, stack }) => {
  await openPage(dock, 'system')
  stack.pcOff()
  await expect(dock.getByTestId('offline-title')).toBeVisible({ timeout: 20_000 })
  await expect(dock.getByTestId('system-page')).toHaveCount(0)
  stack.pcOn()
  await expect(dock.getByTestId('system-page')).toBeVisible({ timeout: 40_000 })
})

// --- Musique -----------------------------------------------------------------------------------

test('Musique : titre, artiste, pochette, durée et progression', async ({ dock }) => {
  await openMusic(dock)
  await expect(dock.getByTestId('music-title')).toHaveText('Nuit blanche')
  await expect(dock.getByTestId('music-artist')).toHaveText('Les Fantômes · Néons')
  await expect(dock.getByTestId('time-total')).toHaveText('3:42')
  await expect(dock.getByTestId('music-cover-img')).toHaveAttribute('src', /^data:image\/svg\+xml;base64,/)
  // Le morceau joue : la position avance sans que l'agent envoie quoi que ce soit.
  const elapsed = async () => seconds(await dock.getByTestId('time-elapsed').innerText())
  const first = await elapsed()
  await expect.poll(elapsed, { timeout: 6_000 }).toBeGreaterThan(first)
  await shot(dock, '04-musique', 'b')
})

test('Musique : lecture/pause, suivant et précédent, au toucher', async ({ dock }) => {
  await openMusic(dock)
  const play = dock.getByTestId('music-play')
  await expect(play).toHaveAttribute('aria-label', 'Pause')
  await play.tap()
  await expect(play).toHaveAttribute('aria-label', 'Lecture')
  await expect(dock.getByTestId('mini-play')).toHaveAttribute('aria-label', 'Lecture') // même état dans la barre du haut

  // En pause, la position ne bouge pas.
  const before = await dock.getByTestId('time-elapsed').innerText()
  await dock.waitForTimeout(1_200)
  expect(await dock.getByTestId('time-elapsed').innerText()).toBe(before)

  await dock.getByTestId('music-next').tap()
  await expect(dock.getByTestId('music-title')).toHaveText('Sous la pluie de Kyoto')
  await expect(dock.getByTestId('time-elapsed')).toHaveText('0:00')
  await dock.getByTestId('music-prev').tap()
  await expect(dock.getByTestId('music-title')).toHaveText('Nuit blanche')
})

test('Musique : toucher la barre de progression déplace la lecture', async ({ dock }) => {
  await openMusic(dock)
  await dock.getByTestId('music-play').tap() // en pause : la valeur attendue est exacte
  const bar = dock.getByTestId('progress')
  const box = (await bar.boundingBox())!
  await bar.tap({ position: { x: box.width * 0.5, y: box.height / 2 } })
  await expect(dock.getByTestId('time-elapsed')).toHaveText(/^1:(4[89]|5\d)$/) // 3:42 / 2 = 1:51
  await bar.tap({ position: { x: box.width * 0.1, y: box.height / 2 } })
  await expect(dock.getByTestId('time-elapsed')).toHaveText(/^0:(1[89]|2\d)$/) // 10 % de 3:42 ≈ 0:22
})

test('Musique : glisser le curseur ne déplace la lecture qu’au relâchement', async ({ dock }) => {
  await openMusic(dock)
  await dock.getByTestId('music-play').tap()
  const bar = dock.getByTestId('progress')
  const box = (await bar.boundingBox())!
  const y = box.y + box.height / 2
  await dock.mouse.move(box.x + box.width * 0.2, y)
  await dock.mouse.down()
  await dock.mouse.move(box.x + box.width * 0.8, y, { steps: 6 })
  // Pendant le glissement le doigt décide (80 % de 3:42 ≈ 2:58)…
  await expect(dock.getByTestId('time-elapsed')).toHaveText(/^2:5\d$|^3:0\d$/)
  await dock.mouse.up()
  // … et à la fin le lecteur confirme la même position.
  await expect(dock.getByTestId('time-elapsed')).toHaveText(/^2:5\d$|^3:0\d$/)
})

test('Musique : plusieurs lecteurs, on choisit celui qu’on pilote', async ({ dock }) => {
  await openMusic(dock)
  await expect(dock.getByTestId('music-players')).toBeVisible()
  await dock.getByTestId('music-select-brave.instance4242').tap()
  await expect(dock.getByTestId('music-title')).toHaveText('Documentaire : les océans')
  await expect(dock.getByTestId('music-next')).toBeDisabled() // le navigateur n'a pas de morceau suivant
  await expect(dock.getByTestId('time-total')).toHaveText('55:00')
  await shot(dock, '05-musique-navigateur', 'b')
  await dock.getByTestId('music-select-spotify').tap()
  await expect(dock.getByTestId('music-title')).toHaveText('Nuit blanche')
})

test('Mini-lecteur : toujours dans la barre du haut, quelle que soit la page', async ({ dock }) => {
  const mini = dock.getByTestId('mini-player')
  await expect(mini).toBeVisible()
  await expect(dock.getByTestId('mini-title')).toHaveText('Nuit blanche')
  for (const id of ['system', 'power', 'settings', 'markets']) {
    await openPage(dock, id)
    await expect(mini).toBeVisible()
  }
  await openPage(dock, 'settings')
  await shot(dock, '06-mini-lecteur', 'b')

  await dock.getByTestId('mini-play').tap()
  await expect(dock.getByTestId('mini-play')).toHaveAttribute('aria-label', 'Lecture')
  await dock.getByTestId('mini-next').tap()
  await expect(dock.getByTestId('mini-title')).toHaveText('Sous la pluie de Kyoto')
  await dock.getByTestId('mini-prev').tap()
  await expect(dock.getByTestId('mini-title')).toHaveText('Nuit blanche')

  await dock.getByTestId('mini-open').tap()
  await expect(dock.getByTestId('page-music')).toBeVisible()
})

test('Musique : le mini-lecteur disparaît avec le PC', async ({ dock, stack }) => {
  await openPage(dock, 'system')
  stack.pcOff()
  await expect(dock.getByTestId('offline-title')).toBeVisible({ timeout: 20_000 })
  await expect(dock.getByTestId('mini-player')).toHaveCount(0)
  stack.pcOn()
  await expect(dock.getByTestId('mini-player')).toBeVisible({ timeout: 40_000 })
})
