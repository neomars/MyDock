// États limites des pages Système et Musique : on simule le dock (WebSocket factice) pour produire des
// cas que la démonstration ne montre pas : aucun lecteur, bus D-Bus absent, carte NVIDIA éveillée,
// 24 cœurs, processus têtu, textes très longs. Le vrai chemin (dock + agent) est couvert par stage-b.spec.ts.

import type { Page, WebSocketRoute } from '@playwright/test'
import { expect, shot, test } from './fixtures'

type Topics = Record<string, unknown>
interface Mock {
  /** Messages `act` reçus du navigateur, dans l'ordre. */
  acts: { a: string; p: Record<string, unknown>; confirm?: string }[]
  /** Remplace l'instantané d'un thème et le renvoie aux abonnés. */
  push(topic: string, data: unknown): void
}

const HOST = {
  hostname: 'portable',
  os: 'Ubuntu 26.04 LTS',
  kernel: '7.0.0-38-generic',
  arch: 'x86_64',
  python: '3.14.4',
  agent: { version: '0.2.0', proto: 1, demo: false },
  boot_time: Math.floor(Date.now() / 1000) - 3 * 3600,
  session: { type: 'wayland', desktop: 'GNOME' },
  interfaces: [],
}

const GIB = 1024 ** 3

function systemData(extra: Record<string, unknown> = {}) {
  const flat = Array.from({ length: 60 }, (_, i) => 20 + (i % 10))
  return {
    cpu: { percent: 24, cores: [10, 20, 30, 40, 5, 15, 25, 35], freq_mhz: 3200, temp_c: 55, load: [1, 1, 1] },
    mem: { total: 32 * GIB, used: 12 * GIB, percent: 37.5, swap_total: 8 * GIB, swap_used: 0 },
    net: { down: 1_500_000, up: 120_000 },
    disk_io: { read: 0, write: 0 },
    disks: [{ mount: '/', device: 'nvme0n1p2', fs: 'ext4', total: 1000 * GIB, used: 400 * GIB, percent: 40 }],
    temps: { cpu: 55 },
    battery: null,
    gpus: [],
    history: { cpu: flat, mem: flat, gpu: [], down: flat.map((v) => v * 10_000), up: flat.map((v) => v * 1_000) },
    ...extra,
  }
}

function player(extra: Record<string, unknown> = {}) {
  return {
    id: 'spotify',
    name: 'Spotify',
    status: 'playing',
    title: 'Morceau',
    artist: 'Artiste',
    album: 'Album',
    length_ms: 200_000,
    position_ms: 30_000,
    rate: 1,
    art: null,
    can: { play: true, pause: true, next: true, previous: true, seek: true },
    ...extra,
  }
}

// Un PNG 1×1 : de quoi vérifier que la pochette reçue s'affiche.
const PIXEL = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

/** Installe un faux dock : le navigateur croit parler à un PC connecté qui publie ces thèmes. */
async function mockDock(
  page: Page,
  topics: Topics,
  options: { startPage: string; onAct?: (act: Mock['acts'][number], ws: WebSocketRoute) => unknown },
): Promise<Mock> {
  const subscribed = new Set<string>()
  const acts: Mock['acts'] = []
  let current: WebSocketRoute | null = null
  const data: Topics = { host: HOST, ...topics }
  const snap = (ws: WebSocketRoute, topic: string, payload: unknown) =>
    ws.send(JSON.stringify({ t: 'snap', topic, rev: 1, data: payload }))

  await page.routeWebSocket(/\/ws$/, (ws) => {
    current = ws
    ws.send(JSON.stringify({ t: 'hello', proto: 1, agent: { role: 'dock', version: '0.2.0' } }))
    snap(ws, 'link', {
      state: 'connected', detail: 'Connecté.', since: Date.now(), attempt: 0, next_retry_at: null,
      resolved: '127.0.0.1', agent: { version: '0.2.0', demo: false }, host: '127.0.0.1', port: 8765,
    }) // prettier-ignore
    snap(ws, 'dock', {
      version: '0.2.0', proto: 1, hostname: 'raspberrypi', ips: ['192.168.1.60'],
      pc: { host: '127.0.0.1', port: 8765, token_set: true, name: 'portable' },
      wol: { mac: null, source: null, iface: null, kind: null, interfaces: [] },
    }) // prettier-ignore
    snap(ws, 'ui', { last_page: options.startPage, order: [], hidden: [] })
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw))
      if (message.t === 'sub') {
        for (const topic of message.topics as string[]) {
          subscribed.add(topic)
          if (data[topic] !== undefined) snap(ws, topic, data[topic])
        }
      } else if (message.t === 'act') {
        const act = { a: message.a, p: message.p ?? {}, confirm: message.confirm }
        acts.push(act)
        const reply = options.onAct?.(act, ws) as Record<string, unknown> | undefined
        ws.send(JSON.stringify({ t: 'ack', id: message.id, ok: true, r: {}, ...reply }))
      } else if (message.t === 'ping') {
        ws.send(JSON.stringify({ t: 'pong', n: message.n, ts: Date.now() }))
      }
    })
  })

  return {
    acts,
    push(topic, payload) {
      data[topic] = payload
      if (current && subscribed.has(topic)) snap(current, topic, payload)
    },
  }
}

const problems: string[] = []
test.beforeEach(async ({ page }) => {
  problems.length = 0
  page.on('console', (m) => ['error', 'warning'].includes(m.type()) && problems.push(`${m.type()}: ${m.text()}`))
  page.on('pageerror', (e) => problems.push(`exception: ${e.message}`))
})
test.afterEach(() => {
  expect(problems, 'erreurs dans la console du navigateur').toEqual([])
})

async function noPageScroll(page: Page): Promise<void> {
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size).toEqual({ w: 1024, h: 600 })
}

// --- Musique -------------------------------------------------------------------------------------

test('Musique : aucun lecteur ouvert, la page l’explique et le mini-lecteur reste caché', async ({ page, stack }) => {
  await mockDock(page, { music: { players: [], active: null } }, { startPage: 'music' })
  await page.goto(stack.url)
  await expect(page.getByTestId('music-empty')).toContainText('Aucun lecteur ouvert')
  await expect(page.getByTestId('mini-player')).toHaveCount(0)
  await shot(page, '07-musique-vide', 'b')
})

test('Musique : bus D-Bus injoignable, le message de l’agent est affiché', async ({ page, stack }) => {
  const message = 'Bus de session D-Bus injoignable (FileNotFoundError). Nouvelle tentative dans 10 s.'
  await mockDock(page, { music: { unavailable: message } }, { startPage: 'music' })
  await page.goto(stack.url)
  await expect(page.getByTestId('music-unavailable')).toContainText(message)
  await expect(page.getByTestId('mini-player')).toHaveCount(0)
})

test('Musique : un titre très long reste sur deux lignes, sans déborder de l’écran', async ({ page, stack }) => {
  const title = 'Concerto pour piano et orchestre no 2 en do mineur, opus 18 : Moderato — version intégrale enregistrée en public'
  await mockDock(
    page,
    { music: { players: [player({ title, artist: 'Orchestre philharmonique de la ville de Lyon, Sergueï Rachmaninov et invités', album: 'Les grands concertos' })], active: 'spotify' } },
    { startPage: 'music' },
  )
  await page.goto(stack.url)
  const heading = page.getByTestId('music-title')
  await expect(heading).toContainText('Concerto pour piano')
  const lines = await heading.evaluate((el) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight)))
  expect(lines).toBeLessThanOrEqual(2)
  await noPageScroll(page)
  await expect(page.getByTestId('mini-title')).toContainText('Concerto pour piano')
  await shot(page, '08-musique-titre-long', 'b')
})

test('Musique : un lecteur sans durée ni position (navigateur) ne propose pas de barre glissable', async ({ page, stack }) => {
  const mock = await mockDock(
    page,
    {
      music: {
        players: [player({ id: 'brave.instance1', name: 'Brave', title: 'Direct', length_ms: null, position_ms: null, can: { play: true, pause: true, next: false, previous: false, seek: false } })],
        active: 'brave.instance1',
      },
    },
    { startPage: 'music' },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('time-elapsed')).toHaveText('–:––')
  await expect(page.getByTestId('progress')).toHaveAttribute('aria-disabled', 'true')
  const box = (await page.getByTestId('progress').boundingBox())!
  await page.getByTestId('progress').tap({ position: { x: box.width / 2, y: box.height / 2 }, force: true })
  await page.waitForTimeout(300)
  expect(mock.acts.filter((a) => a.a === 'music.seek')).toEqual([])
  await expect(page.getByTestId('music-next')).toBeDisabled()
  await expect(page.getByTestId('music-prev')).toBeDisabled()
})

test('Musique : la pochette est demandée une fois, et son échec ne boucle pas', async ({ page, stack }) => {
  const mock = await mockDock(
    page,
    { music: { players: [player({ art: 'aaaaaaaaaaaaaaaa' })], active: 'spotify' } },
    {
      startPage: 'music',
      onAct: (act) =>
        act.a === 'music.art' ? { ok: false, r: undefined, e: { code: 'art_failed', msg: 'Pochette injoignable.' } } : undefined,
    },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('music-title')).toHaveText('Morceau')
  // La pochette est introuvable : le titre reste affiché, avec une image de remplacement.
  await expect(page.getByTestId('music-cover-img')).toHaveCount(0)
  for (let i = 0; i < 3; i++) {
    mock.push('music', { players: [player({ art: 'aaaaaaaaaaaaaaaa', position_ms: 31_000 + i })], active: 'spotify' })
    await page.waitForTimeout(120)
  }
  expect(mock.acts.filter((a) => a.a === 'music.art')).toHaveLength(1)
})

test('Musique : une pochette reçue s’affiche dans la page et dans le mini-lecteur', async ({ page, stack }) => {
  await mockDock(
    page,
    { music: { players: [player({ art: 'bbbbbbbbbbbbbbbb' })], active: 'spotify' } },
    { startPage: 'music', onAct: (act) => (act.a === 'music.art' ? { r: { mime: 'image/png', data: PIXEL } } : undefined) },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('music-cover-img')).toHaveAttribute('src', /^data:image\/png;base64,/)
  await expect(page.getByTestId('mini-player').locator('img')).toHaveAttribute('src', /^data:image\/png;base64,/)
})

test('Musique : le lecteur qui joue est mis en avant, deux instances du même navigateur sont numérotées', async ({ page, stack }) => {
  await mockDock(
    page,
    {
      music: {
        players: [
          player({ id: 'brave.instance1', name: 'Brave', title: 'Vidéo A' }),
          player({ id: 'brave.instance2', name: 'Brave', title: 'Vidéo B', status: 'paused' }),
          player({ id: 'spotify', name: 'Spotify', status: 'paused' }),
        ],
        active: 'brave.instance1',
      },
    },
    { startPage: 'music' },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('music-select-brave.instance1')).toHaveText('Brave 1')
  await expect(page.getByTestId('music-select-brave.instance2')).toHaveText('Brave 2')
  await expect(page.getByTestId('music-select-spotify')).toHaveText('Spotify')
  await expect(page.getByTestId('music-select-brave.instance1')).toHaveAttribute('aria-selected', 'true')
})

// --- Système -------------------------------------------------------------------------------------

test('Système : la carte NVIDIA éveillée montre sa charge, sa température et sa courbe', async ({ page, stack }) => {
  const gpus = [
    { id: '0000:00:02.0', vendor: 'intel', name: 'Intel UHD Graphics 770', state: 'active', util: null, mem_used: null, mem_total: null, temp_c: null, power_w: null, freq_mhz: 450 },
    { id: '0000:01:00.0', vendor: 'nvidia', name: 'NVIDIA RTX 4000 Ada Generation Laptop GPU', state: 'active', util: 63, mem_used: 2 * GIB, mem_total: 12 * GIB, temp_c: 58, power_w: 34.5, freq_mhz: 1650 },
  ]
  const gpuHistory = Array.from({ length: 60 }, (_, i) => (i < 20 ? null : 30 + (i % 20)))
  const base = systemData()
  await mockDock(page, { system: systemData({ gpus, history: { ...base.history, gpu: gpuHistory } }), procs: { count: 0, cores: 8, procs: [] } }, { startPage: 'system' })
  await page.goto(stack.url)
  await expect(page.getByTestId('gpu-value')).toHaveText(/^63\s%$/)
  await expect(page.getByTestId('tile-gpu')).toContainText('RTX 4000 Ada')
  await expect(page.getByTestId('tile-gpu')).toContainText('58')
  await expect(page.getByTestId('chart-card')).toContainText('GPU')
  // La carte dormait pendant les 20 premières secondes : la courbe est interrompue, pas un faux 0 %.
  expect(await page.getByTestId('chart-line').count()).toBeGreaterThanOrEqual(3)
  await shot(page, '09-systeme-gpu-actif', 'b')
})

test('Système : 24 cœurs, 4 disques, sans batterie ni carte graphique, tout tient dans l’écran', async ({ page, stack }) => {
  const cores = Array.from({ length: 24 }, (_, i) => (i * 7) % 100)
  const disks = ['/', '/home', '/media/mars/Photos de famille 2024', '/mnt/nas-bureau'].map((mount, i) => ({
    mount, device: `sd${i}`, fs: 'ext4', total: (500 + i * 500) * GIB, used: (100 + i * 150) * GIB, percent: 20 + i * 25,
  })) // prettier-ignore
  await mockDock(
    page,
    {
      system: systemData({ cpu: { percent: 91, cores, freq_mhz: 4900, temp_c: 88, load: [20, 18, 15] }, disks, gpus: [], battery: null }),
      procs: { count: 612, cores: 24, procs: [] },
    },
    { startPage: 'system' },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('cores')).toContainText('Cœurs (24)')
  await expect(page.getByTestId('tile-gpu')).toContainText('Aucune détectée')
  await expect(page.getByTestId('battery')).toHaveCount(0)
  await expect(page.getByTestId('procs')).toContainText('Processus (612)')
  await expect(page.getByTestId('disk-Photos de famille 2024')).toBeVisible()
  await noPageScroll(page)
})

test('Système : les mesures indisponibles sont expliquées', async ({ page, stack }) => {
  await mockDock(page, { system: { unavailable: 'Lecture des capteurs impossible.' } }, { startPage: 'system' })
  await page.goto(stack.url)
  await expect(page.getByTestId('system-unavailable')).toContainText('Lecture des capteurs impossible.')
})

test('Système : un processus qui ignore la demande polie se force en deux temps', async ({ page, stack }) => {
  const procs = {
    count: 3,
    cores: 8,
    procs: [
      { pid: 321, name: 'zoom-bloque', cpu: 90, mem: 700 * 1024 ** 2, user: 'mars', started: 1000.5, killable: true },
      { pid: 5, name: 'gnome-shell', cpu: 4, mem: 500 * 1024 ** 2, user: 'mars', started: 900.1, killable: false },
    ],
  }
  let stubborn = true
  const mock = await mockDock(
    page,
    { system: systemData(), procs },
    {
      startPage: 'system',
      onAct: (act) => {
        if (act.a !== 'system.kill') return undefined
        const label = act.p.force ? "Forcer l'arrêt de « zoom-bloque » (PID 321)" : 'Terminer « zoom-bloque » (PID 321)'
        if (!act.confirm) {
          return { ok: false, r: undefined, e: { code: 'confirm_required', msg: 'Confirmation requise' }, confirm: { token: 'tok', ttl: 10, label } }
        }
        const gone = Boolean(act.p.force) || !stubborn
        if (gone) stubborn = false
        return { r: { pid: 321, name: 'zoom-bloque', signal: act.p.force ? 'SIGKILL' : 'SIGTERM', gone } }
      },
    },
  )
  await page.goto(stack.url)

  await page.getByTestId('kill-321').tap()
  await expect(page.getByTestId('confirm')).toContainText('Terminer « zoom-bloque » (PID 321)')
  await page.getByTestId('confirm-ok').tap()
  await expect(page.getByTestId('toast')).toContainText('ne s’arrête pas')
  await expect(page.getByTestId('kill-321')).toHaveCount(0)
  await expect(page.getByTestId('force-321')).toBeVisible()
  await shot(page, '10-processus-tetu', 'b')

  await page.getByTestId('force-321').tap()
  await expect(page.getByTestId('confirm')).toContainText("Forcer l'arrêt de « zoom-bloque »")
  await page.getByTestId('confirm-ok').tap()
  await expect(page.getByTestId('toast')).toContainText('« zoom-bloque » est arrêté')
  expect(mock.acts.filter((a) => a.a === 'system.kill').map((a) => [Boolean(a.p.force), Boolean(a.confirm)])).toEqual([
    [false, false],
    [false, true],
    [true, false],
    [true, true],
  ])
  // Le processus du système n'a jamais eu de bouton d'arrêt.
  await expect(page.getByTestId('kill-5')).toHaveCount(0)
})

test('Système : un refus de l’agent est affiché tel quel', async ({ page, stack }) => {
  const procs = { count: 1, cores: 8, procs: [{ pid: 7, name: 'appli', cpu: 1, mem: 1_000_000, user: 'mars', started: 1.5, killable: true }] }
  await mockDock(page, { system: systemData(), procs }, {
    startPage: 'system',
    onAct: (act) =>
      act.a === 'system.kill' && act.confirm
        ? { ok: false, r: undefined, e: { code: 'changed', msg: 'La liste a changé : ce n’est plus le même processus.' } }
        : act.a === 'system.kill'
          ? { ok: false, r: undefined, e: { code: 'confirm_required', msg: '…' }, confirm: { token: 't', ttl: 10, label: 'Terminer « appli » (PID 7)' } }
          : undefined,
  })
  await page.goto(stack.url)
  await page.getByTestId('kill-7').tap()
  await page.getByTestId('confirm-ok').tap()
  await expect(page.getByTestId('toast')).toContainText('ce n’est plus le même processus')
})
