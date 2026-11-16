// Étape m : le son des vidéos, de la TV et des jeux sort sur le PC (le Raspberry Pi n'a pas d'enceintes).
// Vrai Chromium 1024×600 tactile -> vrai dock -> assistant de lecture en démonstration (aucun VLC) -> vrai agent en démonstration
// (qui compte le son qu'on lui envoie sans rien jouer). Le circuit réel, avec de vrais sons, est essayé par les tests Python
// (agent/tests/test_audio_in.py, dock/server/tests/test_pc_audio.py) avec un vrai PipeWire sans carte son.

import type { Page } from '@playwright/test'
import { expect, helperReady, openPage, playFilm, shot, test } from './fixtures'

const noScroll = (page: Page) =>
  page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))

test('Divertissement : le son sort sur le PC et la télécommande le dit ; sans le PC elle dit pourquoi on n’entend rien', async ({ dock, stack }) => {
  test.setTimeout(120_000)
  await helperReady(dock)
  await playFilm(dock)
  await expect(dock.getByTestId('remote')).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })

  const audio = dock.getByTestId('remote-audio')
  await expect(audio).toHaveText('Son : sur le PC', { timeout: 15_000 })
  await expect(audio).toHaveAttribute('data-level', 'good')
  expect(await noScroll(dock)).toEqual({ w: 1024, h: 600 })
  await shot(dock, '01-telecommande-son-sur-le-pc', 'm')

  stack.pcOff() // le PC s'éteint pendant la lecture : la vidéo continue, mais plus rien ne sort
  await expect(audio).toHaveText('PC hors ligne : pas de son', { timeout: 40_000 })
  await expect(audio).toHaveAttribute('data-level', 'warn')
  await shot(dock, '02-telecommande-pc-hors-ligne', 'm')
  await expect(dock.getByTestId('remote')).toHaveAttribute('data-state', 'playing')

  stack.pcOn() // il revient : le son repart tout seul
  await expect(audio).toHaveText('Son : sur le PC', { timeout: 60_000 })
  await dock.getByTestId('remote-stop').tap()
})

test('Réglages : le son sort sur le PC ou reste sur le Pi, et l’avance du son des vidéos se règle', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('settings-media').scrollIntoViewIfNeeded()
  await expect(dock.getByTestId('sound-mode')).toHaveText('sur le PC')
  await expect(dock.getByTestId('sound-advance')).toHaveText('150 ms')
  await expect(dock.getByTestId('sound-help')).toContainText('Le Pi n’a pas d’enceintes')
  await dock.getByTestId('sound-advance-up').tap()
  await expect(dock.getByTestId('sound-advance')).toHaveText('200 ms')
  await dock.getByTestId('sound-advance-down').tap()
  await dock.getByTestId('sound-advance-down').tap()
  await expect(dock.getByTestId('sound-advance')).toHaveText('100 ms')
  await dock.getByTestId('settings-media').scrollIntoViewIfNeeded()
  await expect(dock.getByTestId('toast')).toHaveCount(0, { timeout: 8_000 }) // la photo sans bulle par-dessus
  await dock.waitForTimeout(300)
  await shot(dock, '03-reglages-son', 'm')

  await dock.getByTestId('sound-toggle').tap()
  await expect(dock.getByTestId('sound-mode')).toHaveText('sur le Pi')
  await expect(dock.getByTestId('sound-advance')).toHaveCount(0) // sans le PC, l'avance n'a plus de sens
  await expect(dock.getByTestId('sound-toggle')).toHaveAttribute('aria-pressed', 'false')

  // Le choix est gardé par le dock (il survit à un rechargement de l'écran) : on le remet.
  await dock.reload()
  await openPage(dock, 'settings')
  await dock.getByTestId('settings-media').scrollIntoViewIfNeeded()
  await expect(dock.getByTestId('sound-mode')).toHaveText('sur le Pi')
  await dock.getByTestId('sound-toggle').tap()
  await expect(dock.getByTestId('sound-mode')).toHaveText('sur le PC')
  await expect(dock.getByTestId('sound-advance')).toHaveText('100 ms') // l'avance réglée n'a pas été perdue
  await dock.getByTestId('sound-advance-up').tap()
  await expect(dock.getByTestId('sound-advance')).toHaveText('150 ms')
})

test('Divertissement : avec le son sur le Pi, la télécommande le dit sans alarmer', async ({ dock }) => {
  await helperReady(dock)
  await openPage(dock, 'settings')
  await dock.getByTestId('settings-media').scrollIntoViewIfNeeded()
  await dock.getByTestId('sound-toggle').tap()
  await expect(dock.getByTestId('sound-mode')).toHaveText('sur le Pi')
  await playFilm(dock)
  await expect(dock.getByTestId('remote')).toHaveAttribute('data-state', 'playing', { timeout: 10_000 })
  await expect(dock.getByTestId('remote-audio')).toHaveText('Son : sur le Pi')
  await expect(dock.getByTestId('remote-audio')).toHaveAttribute('data-level', 'dim')
  await dock.getByTestId('remote-stop').tap()
  await openPage(dock, 'settings')
  await dock.getByTestId('settings-media').scrollIntoViewIfNeeded()
  await dock.getByTestId('sound-toggle').tap() // on remet le son sur le PC pour les essais qui suivent
  await expect(dock.getByTestId('sound-mode')).toHaveText('sur le PC')
})
