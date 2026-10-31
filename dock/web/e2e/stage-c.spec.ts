// Étape c : page Son et volume de la page Musique. Vrai Chromium 1024×600 tactile -> vrai dock -> vrai
// agent de démonstration (un faux serveur de son qui parle comme pactl, voir agent/mydock_agent/demo/audio.py).

import type { Locator, Page } from '@playwright/test'
import { expect, shot, test } from './fixtures'

const SPEAKERS = 'alsa_output.pci-0000_00_1f.3.analog-stereo'
const HEADSET = 'bluez_output.80_C3_BA_12_34_56.1'
const HDMI_PLUGGED = 'alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra1'
const HDMI_UNPLUGGED = 'alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra2'
const WEBCAM = 'alsa_input.usb-046d_HD_Pro_Webcam_C920-02.analog-stereo'

/** « 54 % » avec son espace insécable : `\s` les accepte toutes. */
const percent = (value: number) => new RegExp(`^${value}\\s%$`)

async function openSound(dock: Page): Promise<void> {
  await dock.getByTestId('tab-sound').tap()
  await expect(dock.getByTestId('sound-page')).toBeVisible()
}

/** Touche un curseur à `value` % de sa course (la poignée de 38 px ou 30 px ne sort jamais de la piste). */
async function tapSlider(slider: Locator, value: number, thumb: number): Promise<void> {
  const box = (await slider.boundingBox())!
  await slider.tap({ position: { x: thumb / 2 + (box.width - thumb) * (value / 100), y: box.height / 2 } })
}

// --- lecture de l'état --------------------------------------------------------------------------

test('Son : sortie par défaut, micro, applications et micro en cours d’utilisation', async ({ dock }) => {
  await openSound(dock)
  await expect(dock.getByTestId('out-label')).toHaveText('Haut-parleurs')
  await expect(dock.getByTestId('out-value')).toHaveText(percent(54))
  await expect(dock.getByTestId('mic-label')).toHaveText('Micro intégré')
  await expect(dock.getByTestId('mic-value')).toHaveText(percent(70))
  await expect(dock.getByTestId('mic-recording')).toHaveText('Micro utilisé par Discord')

  // Trois applications ; Spotify, le lecteur habituel, est en tête. Le son d'événement du bureau,
  // l'indicateur de niveau de pavucontrol et les moniteurs ne sont pas des applications à régler.
  await expect(dock.getByTestId('apps-card')).toContainText('Applications (3)')
  await expect(dock.getByTestId('app-101-name')).toHaveText('Spotify')
  await expect(dock.getByTestId('app-101-value')).toHaveText(percent(85))
  await expect(dock.getByTestId('app-102-name')).toHaveText('Brave')
  await expect(dock.getByTestId('app-102')).toContainText('en pause')
  await expect(dock.getByTestId('app-103-value')).toHaveText(percent(65))
  await expect(dock.getByTestId('app-104')).toHaveCount(0)
  await shot(dock, '01-son', 'c')
})

test('Son : les sorties sont nommées en français, celle sans écran branché est cachée', async ({ dock }) => {
  await openSound(dock)
  const outputs = dock.getByTestId('out-devices')
  await expect(outputs).toContainText('Haut-parleurs')
  await expect(outputs).toContainText('WH-1000XM4')
  await expect(outputs).toContainText('HDMI 1')
  await expect(dock.getByTestId(`out-device-${HDMI_PLUGGED}`)).toBeVisible()
  await expect(dock.getByTestId(`out-device-${HDMI_UNPLUGGED}`)).toHaveCount(0) // « HDMI 2 » : pas d'écran
  await expect(dock.getByTestId(`out-device-${SPEAKERS}`)).toHaveAttribute('aria-checked', 'true')
  await expect(dock.getByTestId(`out-device-${HEADSET}`)).toHaveAttribute('aria-checked', 'false')
  // Deux micros : l'intégré et celui de la webcam, avec le nom que donne l'appareil, sans « Analog Stereo ».
  await expect(dock.getByTestId('mic-devices')).toContainText('HD Pro Webcam C920')
  await expect(dock.getByTestId('mic-devices')).not.toContainText('Analog Stereo')
})

// --- volume -------------------------------------------------------------------------------------

test('Son : toucher le curseur règle le volume, et le PC le confirme', async ({ dock }) => {
  await openSound(dock)
  await tapSlider(dock.getByTestId('out-slider'), 25, 38)
  await expect(dock.getByTestId('out-value')).toHaveText(percent(25))
  // Le même réglage se retrouve ailleurs : la page Musique lit le même état, venu de l'agent.
  await dock.getByTestId('tab-music').tap()
  await expect(dock.getByTestId('music-vol-value')).toHaveText(percent(25))
  await expect(dock.getByTestId('music-vol-slider')).toHaveAttribute('aria-valuenow', '25')
})

test('Son : glisser le curseur suit le doigt, la dernière valeur est celle du PC', async ({ dock }) => {
  await openSound(dock)
  const slider = dock.getByTestId('out-slider')
  const box = (await slider.boundingBox())!
  const y = box.y + box.height / 2
  const at = (value: number) => box.x + 19 + (box.width - 38) * (value / 100)
  await dock.mouse.move(at(20), y)
  await dock.mouse.down()
  await dock.mouse.move(at(80), y, { steps: 12 })
  await expect(dock.getByTestId('out-value')).toHaveText(percent(80)) // le doigt décide pendant le glissement
  await dock.mouse.up()
  await expect(slider).toHaveAttribute('aria-valuenow', '80')
  await dock.waitForTimeout(1_500) // plus rien n'est « en attente » : l'affichage vient du PC
  await expect(dock.getByTestId('out-value')).toHaveText(percent(80))
  await dock.reload()
  await openSound(dock)
  await expect(dock.getByTestId('out-value')).toHaveText(percent(80)) // preuve : c'est bien l'état du PC
})

test('Son : couper puis rétablir le son de la sortie', async ({ dock }) => {
  await openSound(dock)
  const mute = dock.getByTestId('out-mute')
  await expect(mute).toHaveAttribute('aria-pressed', 'false')
  await mute.tap()
  await expect(mute).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.getByTestId('out-value')).toHaveText('Muet')
  await shot(dock, '03-son-muet', 'c')
  // Le PC confirme : même après quelques secondes, rien ne revient en arrière.
  await dock.waitForTimeout(2_500)
  await expect(dock.getByTestId('out-value')).toHaveText('Muet')
  await mute.tap()
  await expect(mute).toHaveAttribute('aria-pressed', 'false')
  await expect(dock.getByTestId('out-value')).toHaveText(percent(54))
})

test('Son : bouger le curseur d’un son coupé le rétablit', async ({ dock }) => {
  await openSound(dock)
  await dock.getByTestId('out-mute').tap()
  await expect(dock.getByTestId('out-value')).toHaveText('Muet')
  await tapSlider(dock.getByTestId('out-slider'), 40, 38)
  await expect(dock.getByTestId('out-mute')).toHaveAttribute('aria-pressed', 'false')
  await expect(dock.getByTestId('out-value')).toHaveText(percent(40))
})

test('Son : le clavier règle aussi le volume (accessibilité)', async ({ dock }) => {
  await openSound(dock)
  const slider = dock.getByTestId('out-slider')
  await slider.focus()
  await slider.press('ArrowRight')
  await expect(slider).toHaveAttribute('aria-valuenow', '59')
  await slider.press('PageDown')
  await expect(slider).toHaveAttribute('aria-valuenow', '49')
  await slider.press('End')
  await expect(slider).toHaveAttribute('aria-valuenow', '100')
  await slider.press('Home')
  await expect(slider).toHaveAttribute('aria-valuenow', '0')
})

// --- choix de la sortie et du micro -------------------------------------------------------------

test('Son : choisir une autre sortie par défaut', async ({ dock }) => {
  await openSound(dock)
  await dock.getByTestId(`out-device-${HEADSET}`).tap()
  await expect(dock.getByTestId(`out-device-${HEADSET}`)).toHaveAttribute('aria-checked', 'true')
  await expect(dock.getByTestId('out-label')).toHaveText('WH-1000XM4')
  await expect(dock.getByTestId('out-value')).toHaveText(percent(40)) // le volume de CETTE sortie
  await shot(dock, '02-son-sortie-bluetooth', 'c')
  await dock.waitForTimeout(2_500)
  await expect(dock.getByTestId(`out-device-${HEADSET}`)).toHaveAttribute('aria-checked', 'true') // confirmé par le PC
  // La page Musique règle maintenant la nouvelle sortie.
  await dock.getByTestId('tab-music').tap()
  await expect(dock.getByTestId('music-vol-value')).toHaveText(percent(40))
})

test('Son : le micro se règle, se coupe et se change', async ({ dock }) => {
  await openSound(dock)
  await tapSlider(dock.getByTestId('mic-slider'), 50, 30)
  await expect(dock.getByTestId('mic-value')).toHaveText(percent(50))
  await dock.getByTestId('mic-mute').tap()
  await expect(dock.getByTestId('mic-value')).toHaveText('Muet')
  await expect(dock.getByTestId('mic-mute')).toHaveAttribute('aria-label', /Rétablir le micro/)
  await dock.getByTestId(`mic-device-${WEBCAM}`).tap()
  await expect(dock.getByTestId('mic-label')).toHaveText('HD Pro Webcam C920')
  await expect(dock.getByTestId('mic-value')).toHaveText(percent(90))
  // Les applications suivent le micro par défaut (comme avec PipeWire) : Discord écoute le nouveau.
  await expect(dock.getByTestId('mic-recording')).toHaveText('Micro utilisé par Discord')
})

// --- applications -------------------------------------------------------------------------------

test('Son : régler et couper une application sans toucher aux autres', async ({ dock }) => {
  await openSound(dock)
  await tapSlider(dock.getByTestId('app-103-slider'), 30, 30)
  await expect(dock.getByTestId('app-103-value')).toHaveText(percent(30))
  await dock.getByTestId('app-101-mute').tap()
  await expect(dock.getByTestId('app-101-value')).toHaveText('Muet')
  await expect(dock.getByTestId('app-101-mute')).toHaveAttribute('aria-label', 'Rétablir le son (Spotify)')
  await dock.waitForTimeout(2_300)
  // Les autres lignes n'ont pas bougé, ni la sortie.
  await expect(dock.getByTestId('app-102-value')).toHaveText(percent(100))
  await expect(dock.getByTestId('out-value')).toHaveText(percent(54))
  await expect(dock.getByTestId('app-101-value')).toHaveText('Muet')
})

// --- ergonomie ----------------------------------------------------------------------------------

test('Son : chaque bouton et chaque curseur se touche au doigt (au moins 48 px)', async ({ dock }) => {
  await openSound(dock)
  const targets = dock.locator('[data-testid="sound-page"] button, [data-testid="sound-page"] [role="slider"]')
  const count = await targets.count()
  expect(count).toBeGreaterThanOrEqual(12)
  for (let i = 0; i < count; i++) {
    const box = (await targets.nth(i).boundingBox())!
    const name = await targets.nth(i).getAttribute('data-testid')
    expect(box.height, `${name} : hauteur`).toBeGreaterThanOrEqual(48)
    expect(box.width, `${name} : largeur`).toBeGreaterThanOrEqual(48)
  }
})

test('Son : tout tient dans l’écran, sans défilement de la page', async ({ dock }) => {
  await openSound(dock)
  const size = await dock.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size).toEqual({ w: 1024, h: 600 })
  const page = dock.getByTestId('page-sound')
  expect(await page.evaluate((el) => el.scrollHeight <= el.clientHeight)).toBe(true)
})

test('Son : le volume de la page Musique règle la sortie, et le bouton muet aussi', async ({ dock }) => {
  await dock.getByTestId('tab-music').tap()
  await expect(dock.getByTestId('music-volume')).toBeVisible()
  await tapSlider(dock.getByTestId('music-vol-slider'), 70, 30)
  await expect(dock.getByTestId('music-vol-value')).toHaveText(percent(70))
  await dock.getByTestId('music-vol-mute').tap()
  await expect(dock.getByTestId('music-vol-value')).toHaveText('Muet')
  await shot(dock, '04-musique-volume', 'c')
  await openSound(dock)
  await expect(dock.getByTestId('out-value')).toHaveText('Muet')
  await dock.getByTestId('out-mute').tap()
  await expect(dock.getByTestId('out-value')).toHaveText(percent(70))
})

test('Son : la page disparaît avec le PC et revient avec lui', async ({ dock, stack }) => {
  await openSound(dock)
  stack.pcOff()
  await expect(dock.getByTestId('offline-title')).toBeVisible({ timeout: 20_000 })
  await expect(dock.getByTestId('sound-page')).toHaveCount(0)
  stack.pcOn()
  await expect(dock.getByTestId('sound-page')).toBeVisible({ timeout: 40_000 })
  await expect(dock.getByTestId('out-label')).toHaveText('Haut-parleurs')
})
