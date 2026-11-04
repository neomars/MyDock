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
    await expect(page.getByTestId('system-host')).toBeVisible()
    await use(page)
  },
})

export { expect }

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
 * MYDOCK_SHOTS=g ne refait que les captures de l'étape g, MYDOCK_SHOTS=a, b, c, d, e ou f celles des étapes
 * précédentes (figées à leur livraison : on ne les écrase pas par erreur), MYDOCK_SHOTS=1 toutes.
 */
export async function shot(page: Page, name: string, stage: 'a' | 'b' | 'c' | 'd' | 'e' | 'f' | 'g' = 'a'): Promise<void> {
  const wanted = process.env.MYDOCK_SHOTS
  if (!wanted || (wanted !== '1' && wanted !== stage)) return
  const dir = path.join(SHOTS_ROOT, stage)
  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

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
