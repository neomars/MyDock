// Pile de test : agent en mode démo + serveur dock + interface compilée, un jeu neuf par test.

import { expect, test as base, type Page } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import dgram from 'node:dgram'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

// Les tests se lancent depuis dock/web (npm run e2e).
const ROOT = path.resolve(process.cwd(), '../..')
const PYTHON = process.env.MYDOCK_PYTHON ?? path.join(ROOT, '.venv/bin/python')
export const SHOTS_ROOT = path.join(ROOT, 'docs', 'etapes')

async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as net.AddressInfo
      server.close(() => resolve(port))
    })
    server.on('error', reject)
  })
}

/** Le jeton de démonstration de l'agent (public, écrit dans le dépôt : agent/mydock_agent/demo). */
export const DEMO_TOKEN = /DEMO_TOKEN = "([^"]+)"/.exec(
  fs.readFileSync(path.join(ROOT, 'agent/mydock_agent/demo/__init__.py'), 'utf8'),
)![1]

export interface Stack {
  url: string
  /** Le portail du dock : la page d'installation du PC et l'API d'association. */
  portal: string
  /** Port de l'agent de démonstration (celui qu'un PC annonce en s'associant). */
  agentPort: number
  /** Le dossier de l'interface servie par le dock : une copie jetable de dist/ si `liveWeb`, sinon vide. */
  webDir: string
  /** Le dossier d'état du dock (réglages, vidéos de l'économiseur) : jetable, propre à ce test. */
  stateDir: string
  /** Le dossier des vidéos de l'économiseur d'écran : il n'existe qu'une fois qu'on y a mis une vidéo. */
  saverDir: string
  /** Le dossier d'exécution de l'assistant de lecture en démonstration : y créer `mydock-demo-button` ou `mydock-demo-touch`
   *  fait comme un appui sur le bouton physique ou un toucher de l'écran pendant un jeu. */
  runDir: string
  /** « PC éteint » : plus rien ne répond sur le port de l'agent. */
  pcOff(): void
  /** « Agent arrêté seul » : le PC répond mais l'agent est absent (connexion refusée). */
  agentDown(): void
  /** Tout revient. */
  pcOn(): void
  /** Paquets de réveil reçus par le faux récepteur UDP. */
  wolPackets: Buffer[]
}

export const test = base.extend<{
  stack: Stack
  dock: Page
  consoleProblems: string[]
  unpaired: boolean
  liveWeb: boolean
}>({
  /** Vrai : un dock neuf, sans PC, qui affiche un code d'association (test.use({ unpaired: true })). */
  unpaired: [false, { option: true }],
  /** Vrai : le dock sert une copie jetable de l'interface, que le test peut modifier (mise à jour du paquet). */
  liveWeb: [false, { option: true }],

  stack: async ({ unpaired, liveWeb }, use) => {
    const [dockPort, agentPort, wolPort] = [await freePort(), await freePort(), await freePort()]
    const webDir = liveWeb ? fs.mkdtempSync(path.join(os.tmpdir(), 'mydock-web-')) : ''
    const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mydock-state-'))
    if (liveWeb) fs.cpSync(path.join(ROOT, 'dock', 'web', 'dist'), webDir, { recursive: true })
    const wolPackets: Buffer[] = []
    const udp = dgram.createSocket('udp4')
    udp.on('message', (message) => wolPackets.push(message))
    await new Promise<void>((resolve) => udp.bind(wolPort, '127.0.0.1', resolve))

    const child: ChildProcess = spawn(
      PYTHON,
      [
        path.join(ROOT, 'scripts', 'dev-stack.py'),
        '--dock-port', String(dockPort),
        '--agent-port', String(agentPort),
        '--wol-target', `127.0.0.1:${wolPort}`,
        '--state-dir', stateDir,
        ...(unpaired ? ['--unpaired'] : []),
        ...(liveWeb ? ['--web-dir', webDir] : []),
      ],
      { stdio: ['ignore', 'pipe', 'inherit'], cwd: ROOT },
    ) // prettier-ignore
    const { url, portal } = await new Promise<{ url: string; portal: string }>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('La pile de test ne démarre pas.')), 30_000)
      let output = ''
      child.stdout!.on('data', (chunk: Buffer) => {
        output += chunk.toString()
        const ready = /MYDOCK_STACK_READY (\S+)/.exec(output)
        if (ready) {
          clearTimeout(timer)
          resolve({ url: ready[1], portal: /MYDOCK_PORTAL_READY (\S+)/.exec(output)?.[1] ?? '' })
        }
      })
      child.on('exit', (code) => reject(new Error(`La pile de test s'est arrêtée (code ${code}).`)))
    })

    await use({
      url,
      portal,
      agentPort,
      webDir,
      stateDir,
      saverDir: path.join(stateDir, 'screensaver'),
      runDir: path.join(stateDir, 'run'),
      pcOff: () => child.kill('SIGUSR1'),
      agentDown: () => child.kill('SIGHUP'),
      pcOn: () => child.kill('SIGUSR2'),
      wolPackets,
    })

    child.removeAllListeners('exit')
    child.kill('SIGTERM')
    await new Promise<void>((resolve) => {
      child.once('exit', () => resolve())
      setTimeout(() => {
        child.kill('SIGKILL')
        resolve()
      }, 8_000)
    })
    udp.close()
    if (webDir) fs.rmSync(webDir, { recursive: true, force: true })
    fs.rmSync(stateDir, { recursive: true, force: true })
  },

  // Erreurs console et exceptions de la page : chaque test vérifie qu'il n'y en a aucune.
  consoleProblems: async ({}, use) => {
    const problems: string[] = []
    await use(problems)
    expect(problems, 'erreurs ou avertissements dans la console du navigateur').toEqual([])
  },

  dock: async ({ page, stack, consoleProblems }, use) => {
    page.on('console', (message) => {
      if (['error', 'warning'].includes(message.type())) {
        consoleProblems.push(`${message.type()}: ${message.text()}`)
      }
    })
    page.on('pageerror', (error) => consoleProblems.push(`exception: ${error.message}`))
    await page.goto(stack.url)
    await expect(page.getByTestId('home')).toBeVisible()
    await use(page)
  },
})

export { expect }

/**
 * Ouvre une page depuis l'accueil (le bouton `tile-<id>`). Si une autre page est déjà affichée, on repasse
 * d'abord par l'accueil avec le bouton de la barre du haut : c'est le chemin d'une vraie personne.
 */
export async function openPage(page: Page, id: string): Promise<void> {
  const home = page.getByTestId('home')
  // L'alimentation n'a plus de gros bouton : c'est le petit bouton à droite de l'heure, présent sur toutes les pages.
  if (id === 'power') {
    if (await home.isVisible()) {
      await page.getByTestId('topbar-power').tap()
    } else if ((await page.getByTestId('page-power').count()) === 0) {
      await page.getByTestId('topbar-power').tap()
    }
    await expect(page.getByTestId('page-power')).toBeVisible()
    await expect(home).toHaveCount(0)
    return
  }
  if (!(await home.isVisible())) await goHome(page)
  await page.getByTestId(`tile-${id}`).tap()
  await expect(page.getByTestId(`page-${id}`)).toBeVisible()
  await expect(home).toHaveCount(0) // le fondu de l'accueil est terminé : plus rien ne le recouvre ni ne le dédouble
}

/** Revient à l'accueil par le bouton de la barre du haut. */
export async function goHome(page: Page): Promise<void> {
  await page.getByTestId('home-back').tap()
  await expect(page.getByTestId('home')).toBeVisible()
  await expect(page.locator('main.page')).toHaveCount(0)
}

/** Les pages de l'accueil, dans l'ordre des boutons (de gauche à droite, de haut en bas). */
export async function tileOrder(page: Page): Promise<string[]> {
  return page
    .locator('[data-testid="home-grid"] button[data-testid^="tile-"]')
    .evaluateAll((tiles) => tiles.map((tile) => (tile.getAttribute('data-testid') ?? '').replace('tile-', '')))
}

/**
 * Un vrai balayage tactile (événements du protocole DevTools, pas un simple clic) : le doigt part de (x0, y0), va en
 * (x1, y1) en `steps` étapes d'environ 16 ms. Les événements touchent la page comme ceux de l'écran du Pi.
 */
export async function swipe(page: Page, from: { x: number; y: number }, to: { x: number; y: number }, steps = 10): Promise<void> {
  const cdp = await page.context().newCDPSession(page)
  const point = (x: number, y: number) => [{ x, y, id: 1 }]
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: point(from.x, from.y) })
  for (let i = 1; i <= steps; i++) {
    const x = from.x + ((to.x - from.x) * i) / steps
    const y = from.y + ((to.y - from.y) * i) / steps
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: point(x, y) })
    await page.waitForTimeout(16)
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await page.waitForTimeout(60) // le relâchement arrive à la page avant qu'on rende la main (détacher la session le perdrait)
}

/**
 * Simule la mise à jour du paquet : tous les fichiers dont le nom porte une empreinte (« Music-B9amGNjk.js »)
 * changent de nom et chaque référence est réécrite, comme après une nouvelle compilation. Les anciens
 * fichiers disparaissent, ce que fait aussi `apt`.
 */
export function rehashInterface(dir: string): void {
  const assets = path.join(dir, 'assets')
  const renames = new Map<string, string>()
  for (const name of fs.readdirSync(assets)) {
    const match = /^(.*)-([A-Za-z0-9_-]{8})(\.[a-z0-9]+)$/.exec(name)
    if (match) renames.set(name, `${match[1]}-${[...match[2]].reverse().join('')}${match[3]}`)
  }
  expect(renames.size, 'des fichiers à empreinte dans dist/assets').toBeGreaterThan(10)
  const files = [path.join(dir, 'index.html'), ...fs.readdirSync(assets).map((name) => path.join(assets, name))]
  for (const file of files) {
    if (/\.woff2?$/.test(file)) continue
    let text = fs.readFileSync(file, 'utf8')
    for (const [from, to] of renames) text = text.split(from).join(to)
    fs.writeFileSync(file, text)
  }
  for (const [from, to] of renames) fs.renameSync(path.join(assets, from), path.join(assets, to))
}

/**
 * Capture 1024x600 rangée avec le rapport de l'étape (docs/etapes/<étape>/).
 * MYDOCK_SHOTS=j ne refait que les captures de l'étape j, MYDOCK_SHOTS=a, b, c, d, e, f, g, h ou i celles des étapes
 * précédentes (figées à leur livraison : on ne les écrase pas par erreur), MYDOCK_SHOTS=1 toutes.
 */
export async function shot(page: Page, name: string, stage: 'a' | 'b' | 'c' | 'd' | 'e' | 'f' | 'g' | 'h' | 'i' | 'j' | 'k' | 'l' = 'a'): Promise<void> {
  const wanted = process.env.MYDOCK_SHOTS
  if (!wanted || (wanted !== '1' && wanted !== stage)) return
  const dir = path.join(SHOTS_ROOT, stage)
  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

/** Diamètre de la poignée des curseurs (voir Slider.svelte) : la course utile de la piste est réduite d'autant. */
export const THUMB = { md: 44, lg: 52 } as const

/** Tape un texte sur le clavier tactile, touche par touche (vrais appuis tactiles). */
export async function typeOnKeyboard(page: Page, text: string): Promise<void> {
  for (const char of text) {
    const label = char === ' ' ? 'Espace' : char
    await page.getByTestId('keyboard').getByRole('button', { name: label, exact: true }).tap()
  }
}

export async function clearKeyboard(page: Page): Promise<void> {
  const erase = page.getByTestId('keyboard').getByRole('button', { name: 'Effacer' })
  while ((await page.getByTestId('keyboard-value').innerText()).trim() !== '') await erase.tap()
}
