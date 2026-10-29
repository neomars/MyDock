// Pile de test : agent en mode démo + serveur dock + interface compilée, un jeu neuf par test.

import { expect, test as base, type Page } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import dgram from 'node:dgram'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'

// Les tests se lancent depuis dock/web (npm run e2e).
const ROOT = path.resolve(process.cwd(), '../..')
const PYTHON = process.env.MYDOCK_PYTHON ?? path.join(ROOT, '.venv/bin/python')
export const SHOTS_DIR = path.join(ROOT, 'docs', 'etapes', 'a')

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

export interface Stack {
  url: string
  /** « PC éteint » : plus rien ne répond sur le port de l'agent. */
  pcOff(): void
  /** « Agent arrêté seul » : le PC répond mais l'agent est absent (connexion refusée). */
  agentDown(): void
  /** Tout revient. */
  pcOn(): void
  /** Paquets de réveil reçus par le faux récepteur UDP. */
  wolPackets: Buffer[]
}

export const test = base.extend<{ stack: Stack; dock: Page; consoleProblems: string[] }>({
  stack: async ({}, use) => {
    const [dockPort, agentPort, wolPort] = [await freePort(), await freePort(), await freePort()]
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
      ],
      { stdio: ['ignore', 'pipe', 'inherit'], cwd: ROOT },
    ) // prettier-ignore
    const url = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('La pile de test ne démarre pas.')), 30_000)
      child.stdout!.on('data', (chunk: Buffer) => {
        const match = /MYDOCK_STACK_READY (\S+)/.exec(chunk.toString())
        if (match) {
          clearTimeout(timer)
          resolve(match[1])
        }
      })
      child.on('exit', (code) => reject(new Error(`La pile de test s'est arrêtée (code ${code}).`)))
    })

    await use({
      url,
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

/** Capture 1024x600 rangée avec le rapport d'étape (seulement avec MYDOCK_SHOTS=1). */
export async function shot(page: Page, name: string): Promise<void> {
  if (!process.env.MYDOCK_SHOTS) return
  fs.mkdirSync(SHOTS_DIR, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS_DIR, `${name}.png`) })
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
