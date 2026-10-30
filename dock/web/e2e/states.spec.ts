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
  agent: { version: '0.4.0', proto: 1, demo: false },
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
  options: {
    startPage: string
    pinned?: string[]
    onAct?: (act: Mock['acts'][number], ws: WebSocketRoute) => unknown
  },
): Promise<Mock> {
  const subscribed = new Set<string>()
  const acts: Mock['acts'] = []
  let current: WebSocketRoute | null = null
  const data: Topics = { host: HOST, ...topics }
  const snap = (ws: WebSocketRoute, topic: string, payload: unknown) =>
    ws.send(JSON.stringify({ t: 'snap', topic, rev: 1, data: payload }))

  await page.routeWebSocket(/\/ws$/, (ws) => {
    current = ws
    ws.send(JSON.stringify({ t: 'hello', proto: 1, agent: { role: 'dock', version: '0.4.0' } }))
    snap(ws, 'link', {
      state: 'connected', detail: 'Connecté.', since: Date.now(), attempt: 0, next_retry_at: null,
      resolved: '127.0.0.1', agent: { version: '0.4.0', demo: false }, host: '127.0.0.1', port: 8765,
    }) // prettier-ignore
    snap(ws, 'dock', {
      version: '0.4.0', proto: 1, hostname: 'raspberrypi', ips: ['192.168.1.60'],
      pc: { host: '127.0.0.1', port: 8765, token_set: true, name: 'portable' },
      wol: { mac: null, source: null, iface: null, kind: null, interfaces: [] },
    }) // prettier-ignore
    snap(ws, 'ui', { last_page: options.startPage, order: [], hidden: [], pinned_apps: options.pinned ?? [] })
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
      if (current && (['link', 'dock', 'ui'].includes(topic) || subscribed.has(topic))) snap(current, topic, payload)
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

// --- Son -----------------------------------------------------------------------------------------

function audioDevice(id: string, label: string, extra: Record<string, unknown> = {}) {
  return { id, label, detail: null, kind: 'speakers', volume: 50, muted: false, ...extra }
}

function audioData(extra: Record<string, unknown> = {}) {
  return {
    max: 100,
    default_sink: 'sortie',
    default_source: 'micro',
    sinks: [audioDevice('sortie', 'Haut-parleurs')],
    sources: [audioDevice('micro', 'Micro intégré', { kind: 'microphone', volume: 70 })],
    apps: [],
    recorders: [],
    ...extra,
  }
}

const audioApp = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id, name, volume: 60, muted: false, playing: true, sink: 'sortie', ...extra,
}) // prettier-ignore

const refuse = (msg: string) => ({ ok: false, r: undefined, e: { code: 'failed', msg } })

test('Son : pactl absent ou serveur de son arrêté, le message de l’agent est affiché', async ({ page, stack }) => {
  const message = 'pactl est introuvable : installe le paquet pulseaudio-utils (sudo apt install pulseaudio-utils). Nouvelle tentative dans 5 s.'
  await mockDock(page, { audio: { unavailable: message } }, { startPage: 'sound' })
  await page.goto(stack.url)
  await expect(page.getByTestId('sound-unavailable')).toContainText('Son indisponible')
  await expect(page.getByTestId('sound-unavailable')).toContainText('pulseaudio-utils')
  await expect(page.getByTestId('sound-page')).toHaveCount(0)
  await shot(page, '05-son-indisponible', 'c')
})

test('Son : ni sortie, ni micro, ni application : trois explications, rien de cassé', async ({ page, stack }) => {
  await mockDock(page, { audio: audioData({ default_sink: null, default_source: null, sinks: [], sources: [] }) }, { startPage: 'sound' })
  await page.goto(stack.url)
  await expect(page.getByTestId('out-none')).toContainText('Aucune sortie audio détectée')
  await expect(page.getByTestId('mic-none')).toContainText('Aucun micro détecté')
  await expect(page.getByTestId('apps-empty')).toContainText('Aucune application ne joue de son')
  await expect(page.getByTestId('out-slider')).toHaveCount(0)
  await noPageScroll(page)
  await shot(page, '06-son-vide', 'c')
})

test('Son : un volume amplifié ailleurs s’affiche tel quel et le curseur reste en bout de course', async ({ page, stack }) => {
  await mockDock(page, { audio: audioData({ sinks: [audioDevice('sortie', 'Haut-parleurs', { volume: 130 })] }) }, { startPage: 'sound' })
  await page.goto(stack.url)
  await expect(page.getByTestId('out-value')).toHaveText(/^130\s%$/)
  await expect(page.getByTestId('out-slider')).toHaveAttribute('aria-valuenow', '130')
  const track = (await page.getByTestId('out-slider').boundingBox())!
  const thumb = (await page.getByTestId('out-slider').locator('.thumb').boundingBox())!
  expect(thumb.x + thumb.width / 2).toBeCloseTo(track.x + track.width - 19, 0) // bout de course, pas au-delà
  await shot(page, '08-son-amplifie', 'c')
})

test('Son : beaucoup d’applications, des noms longs et des homonymes : la liste défile dans sa carte', async ({ page, stack }) => {
  const apps = [
    audioApp('1', 'Spotify'),
    audioApp('2', 'Brave'),
    audioApp('3', 'Brave', { playing: false }),
    audioApp('4', 'Une application au nom vraiment extraordinairement long pour un écran de sept pouces'),
    ...Array.from({ length: 10 }, (_, i) => audioApp(String(10 + i), `Lecteur ${i + 1}`)),
  ]
  await mockDock(page, { audio: audioData({ apps }) }, { startPage: 'sound' })
  await page.goto(stack.url)
  await expect(page.getByTestId('apps-card')).toContainText('Applications (14)')
  await expect(page.getByTestId('app-2-name')).toHaveText('Brave 1')
  await expect(page.getByTestId('app-3-name')).toHaveText('Brave 2')
  await noPageScroll(page)
  const list = page.locator('[data-testid="apps-card"] .list')
  expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)
  await shot(page, '07-son-beaucoup-d-applications', 'c')
  // Le nom trop long est coupé, il ne pousse ni le curseur ni le bouton hors de la ligne.
  const row = (await page.getByTestId('app-4').boundingBox())!
  const mute = (await page.getByTestId('app-4-mute').boundingBox())!
  expect(mute.x + mute.width).toBeLessThanOrEqual(row.x + row.width + 1)
  // Au bout de la liste on atteint la dernière application, et la page n'a pas bougé.
  await list.evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await expect(page.getByTestId('app-19')).toBeInViewport()
  await noPageScroll(page)
})

test('Son : glisser un curseur n’envoie que quelques réglages, et le dernier est la valeur finale', async ({ page, stack }) => {
  const mock = await mockDock(page, { audio: audioData() }, { startPage: 'sound' })
  await page.goto(stack.url)
  const box = (await page.getByTestId('out-slider').boundingBox())!
  const y = box.y + box.height / 2
  const at = (value: number) => box.x + 19 + (box.width - 38) * (value / 100)
  await page.mouse.move(at(10), y)
  await page.mouse.down()
  await page.mouse.move(at(90), y, { steps: 40 })
  await page.mouse.up()
  await page.waitForTimeout(500)
  const sent = mock.acts.filter((a) => a.a === 'audio.set_volume')
  expect(sent.length).toBeGreaterThanOrEqual(2)
  expect(sent.length).toBeLessThanOrEqual(8) // au plus dix par seconde, pas une salve de 40
  expect(sent.at(-1)!.p).toEqual({ kind: 'sink', id: 'sortie', percent: 90 })
  expect(sent.map((a) => (a.p.percent as number))).toEqual([...sent.map((a) => a.p.percent as number)].sort((x, y) => x - y))
})

test('Son : un refus du PC rétablit l’affichage et le dit à l’écran', async ({ page, stack }) => {
  await mockDock(page, { audio: audioData() }, { startPage: 'sound', onAct: (act) => (act.a.startsWith('audio.') ? refuse('Le serveur de son a refusé la commande.') : undefined) })
  await page.goto(stack.url)
  await page.getByTestId('out-mute').tap()
  await expect(page.getByTestId('toast')).toContainText('Le serveur de son a refusé la commande.')
  await expect(page.getByTestId('out-mute')).toHaveAttribute('aria-pressed', 'false') // l'affichage provisoire est annulé
  await expect(page.getByTestId('out-value')).toHaveText(/^50\s%$/)
})

test('Son : un réglage jamais confirmé par l’agent ne reste pas affiché indéfiniment', async ({ page, stack }) => {
  // Le PC accuse réception mais l'état publié ne change pas : après 2 s on revient à ce que dit l'agent.
  await mockDock(page, { audio: audioData() }, { startPage: 'sound' })
  await page.goto(stack.url)
  await page.getByTestId('out-mute').tap()
  await expect(page.getByTestId('out-value')).toHaveText('Muet')
  await expect(page.getByTestId('out-value')).toHaveText(/^50\s%$/, { timeout: 4_000 })
})

test('Son : l’agent confirme un réglage, l’état publié prend le relais', async ({ page, stack }) => {
  const mock = await mockDock(page, { audio: audioData({ apps: [audioApp('7', 'Spotify')] }) }, {
    startPage: 'sound',
    onAct: (act) => {
      if (act.a === 'audio.set_mute') {
        mock.push('audio', audioData({ apps: [audioApp('7', 'Spotify', { muted: act.p.muted })] }))
      }
      return undefined
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('app-7-mute').tap()
  await expect(page.getByTestId('app-7-value')).toHaveText('Muet')
  await page.waitForTimeout(2_500)
  await expect(page.getByTestId('app-7-value')).toHaveText('Muet') // confirmé par l'agent : ça dure
  expect(mock.acts.filter((a) => a.a === 'audio.set_mute').map((a) => a.p)).toEqual([{ kind: 'app', id: '7', muted: true }])
})

test('Son : le micro utilisé par plusieurs applications les nomme toutes', async ({ page, stack }) => {
  const recorders = ['Discord', 'Brave', 'OBS Studio', 'Zoom'].map((name, i) => ({ id: String(i), name, source: 'micro' }))
  await mockDock(page, { audio: audioData({ recorders }) }, { startPage: 'sound' })
  await page.goto(stack.url)
  await expect(page.getByTestId('mic-recording')).toHaveText('Micro utilisé par Discord, Brave et 2 autres')
})

test('Musique : le volume reste utilisable quand la sortie est coupée ou absente', async ({ page, stack }) => {
  await mockDock(
    page,
    { music: { players: [player()], active: 'spotify' }, audio: audioData({ sinks: [audioDevice('sortie', 'Haut-parleurs', { muted: true })] }) },
    { startPage: 'music' },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('music-vol-value')).toHaveText('Muet')
  await expect(page.getByTestId('music-vol-mute')).toHaveAttribute('aria-pressed', 'true')
  await noPageScroll(page)
})

test('Musique : sans sortie audio, la ligne de volume n’est pas affichée', async ({ page, stack }) => {
  await mockDock(
    page,
    { music: { players: [player()], active: 'spotify' }, audio: audioData({ default_sink: null, sinks: [] }) },
    { startPage: 'music' },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('music-title')).toHaveText('Morceau')
  await expect(page.getByTestId('music-volume')).toHaveCount(0)
})

// --- Applis, Steam, Office ------------------------------------------------------------------------

const appInfo = (id: string, name: string, extra: Record<string, unknown> = {}) => ({ id, name, generic: null, icon: null, ...extra })
const appsTopic = (apps: ReturnType<typeof appInfo>[]) => ({ count: apps.length, apps })
const ui = (page: string, pinned: string[]) => ({ last_page: page, order: [], hidden: [], pinned_apps: pinned })

test('Applis : une application désinstallée reste à sa place, grisée, sans pouvoir la lancer', async ({ page, stack }) => {
  const mock = await mockDock(
    page,
    { apps: appsTopic([appInfo('a.desktop', 'Firefox'), appInfo('c.desktop', 'Calculatrice')]) },
    { startPage: 'apps', pinned: ['a.desktop', 'org.disparue.App.desktop', 'c.desktop'] },
  )
  await page.goto(stack.url)
  expect(await page.locator('[data-testid="apps-grid"] .name').allInnerTexts()).toEqual(['Firefox', 'org.disparue.App', 'Calculatrice'])
  const gone = page.getByTestId('launch-org.disparue.App.desktop')
  await expect(gone).toBeDisabled()
  await expect(gone).toContainText('Introuvable')
  await page.getByTestId('launch-c.desktop').tap()
  await expect(page.getByTestId('toast')).toContainText('se lance')
  expect(mock.acts.filter((a) => a.a === 'apps.launch').map((a) => a.p)).toEqual([{ id: 'c.desktop' }])
})

test('Applis : un refus de l’agent est affiché et la tuile redevient disponible', async ({ page, stack }) => {
  await mockDock(page, { apps: appsTopic([appInfo('a.desktop', 'Firefox')]) }, {
    startPage: 'apps',
    pinned: ['a.desktop'],
    onAct: (act) => (act.a === 'apps.launch' ? refuse('Le lancement de « Firefox » a échoué (code 1).') : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('launch-a.desktop').tap()
  await expect(page.getByTestId('toast')).toContainText('a échoué (code 1)')
  await expect(page.getByTestId('launching-a.desktop')).toHaveCount(0)
})

test('Applis : deux appuis rapprochés ne lancent l’application qu’une fois', async ({ page, stack }) => {
  const mock = await mockDock(page, { apps: appsTopic([appInfo('a.desktop', 'Firefox')]) }, { startPage: 'apps', pinned: ['a.desktop'] })
  await page.goto(stack.url)
  const tile = page.getByTestId('launch-a.desktop')
  await tile.tap()
  await tile.tap({ force: true })
  await tile.tap({ force: true })
  await page.waitForTimeout(400)
  expect(mock.acts.filter((a) => a.a === 'apps.launch')).toHaveLength(1)
})

test('Applis : vingt-quatre applications aux noms démesurés tiennent sans déborder', async ({ page, stack }) => {
  const apps = Array.from({ length: 24 }, (_, i) => appInfo(`a${i}.desktop`, `Application numéro ${i + 1} au nom vraiment très long pour une tuile`))
  await mockDock(page, { apps: appsTopic(apps) }, { startPage: 'apps', pinned: apps.map((a) => a.id) })
  await page.goto(stack.url)
  await expect(page.getByTestId('apps-count')).toHaveText('24 applications')
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size).toEqual({ w: 1024, h: 600 })
  const lines = await page.locator('[data-testid="launch-a0.desktop"] .name').evaluate((el) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight)))
  expect(lines).toBeLessThanOrEqual(2)
  await shot(page, '08-applis-24', 'd')
})

test('Applis : une icône qui manque ou qui échoue laisse la pastille, sans redemander en boucle', async ({ page, stack }) => {
  const mock = await mockDock(page, { apps: appsTopic([appInfo('a.desktop', 'Firefox', { icon: 'aaaaaaaaaaaaaaaa' }), appInfo('b.desktop', 'Brave', { icon: 'bbbbbbbbbbbbbbbb' })]) }, {
    startPage: 'apps',
    pinned: ['a.desktop', 'b.desktop'],
    onAct: (act) => {
      if (act.a !== 'apps.icon') return undefined
      return act.p.icon === 'aaaaaaaaaaaaaaaa' ? { r: { mime: 'image/png', data: PIXEL } } : refuse('Icône introuvable dans le thème.')
    },
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('launch-a.desktop').locator('img')).toHaveAttribute('src', /^data:image\/png/)
  await expect(page.getByTestId('launch-b.desktop').locator('b')).toHaveText('B')
  await page.waitForTimeout(500)
  expect(mock.acts.filter((a) => a.a === 'apps.icon').map((a) => a.p.icon).sort()).toEqual(['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb'])
})

test('Sélecteur : à vingt-quatre applications on ne peut plus qu’en retirer', async ({ page, stack }) => {
  const apps = Array.from({ length: 30 }, (_, i) => appInfo(`a${String(i).padStart(2, '0')}.desktop`, `Appli ${String(i).padStart(2, '0')}`))
  const pinned = apps.slice(0, 24).map((a) => a.id)
  const mock = await mockDock(page, { apps: appsTopic(apps) }, {
    startPage: 'apps',
    pinned,
    onAct: (act, _ws) => {
      if (act.a === 'dock.ui.set') mock.push('ui', ui('apps', act.p.pinned_apps as string[]))
      return act.a === 'dock.ui.set' ? { r: {} } : undefined
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('apps-edit').tap()
  await expect(page.getByTestId('picker-chosen')).toContainText('24/24')
  for (const id of ['a24.desktop', 'a29.desktop']) await expect(page.getByTestId(`pick-${id}`)).toBeDisabled()
  await expect(page.getByTestId('pick-a00.desktop')).toBeEnabled() // retirer reste possible
  await shot(page, '09-selecteur-plein', 'd')
  await page.getByTestId('pick-a00.desktop').tap()
  await expect(page.getByTestId('picker-chosen')).toContainText('23/24')
  await expect(page.getByTestId('pick-a24.desktop')).toBeEnabled()
  await page.getByTestId('pick-a24.desktop').tap()
  await expect(page.getByTestId('picker-chosen')).toContainText('24/24')
  expect(mock.acts.filter((a) => a.a === 'dock.ui.set').map((a) => (a.p.pinned_apps as string[]).length)).toEqual([23, 24])
})

const game = (appid: number, name: string, played: number | null = null) => ({ appid, name, size: 1_000_000_000, played })

test('Steam : pas installé, la page le dit au lieu de rester vide', async ({ page, stack }) => {
  const message = "Steam n'est pas installé sur ce PC (aucun dossier Steam trouvé)."
  await mockDock(page, { steam: { unavailable: message, games: [], client: false, running: null } }, { startPage: 'steam' })
  await page.goto(stack.url)
  await expect(page.getByTestId('steam-unavailable')).toContainText('Steam indisponible')
  await expect(page.getByTestId('steam-unavailable')).toContainText(message)
  await shot(page, '10-steam-absent', 'd')
})

test('Steam : installé mais sans jeu, un jeu en cours inconnu de la liste reste signalé', async ({ page, stack }) => {
  await mockDock(page, { steam: { games: [], client: true, running: 123 } }, { startPage: 'steam' })
  await page.goto(stack.url)
  await expect(page.getByTestId('steam-empty')).toContainText('Aucun jeu installé')
  await expect(page.getByTestId('steam-running')).toContainText('jeu n° 123')
})

test('Steam : cent cinquante jeux, les jaquettes ne sont demandées qu’au fil du défilement', async ({ page, stack }) => {
  const games = Array.from({ length: 150 }, (_, i) => game(1000 + i, `Jeu ${String(i).padStart(3, '0')}`, 1_700_000_000 - i * 1000))
  const mock = await mockDock(page, { steam: { games, client: false, running: null } }, {
    startPage: 'steam',
    onAct: (act) => (act.a === 'steam.cover' ? { r: { mime: 'image/png', data: PIXEL } } : undefined),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('steam-count')).toHaveText('150 jeux')
  await page.waitForTimeout(800)
  const first = mock.acts.filter((a) => a.a === 'steam.cover').length
  expect(first).toBeGreaterThan(5)
  expect(first).toBeLessThan(40) // pas 150 d'un coup : seulement ce qui est visible, avec une marge
  await page.getByTestId('page-steam').evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await page.waitForTimeout(800)
  const later = mock.acts.filter((a) => a.a === 'steam.cover').map((a) => a.p.appid)
  expect(later.length).toBeGreaterThan(first)
  expect(later).toContain(1149) // le dernier jeu, arrivé à l'écran
  expect(new Set(later).size).toBe(later.length) // jamais deux fois la même jaquette
})

test('Steam : un jeu sans jaquette garde son nom, et on ne la redemande pas', async ({ page, stack }) => {
  const mock = await mockDock(page, { steam: { games: [game(1, 'Sans pochette'), game(2, 'Avec pochette')], client: false, running: null } }, {
    startPage: 'steam',
    onAct: (act) => {
      if (act.a !== 'steam.cover') return undefined
      return act.p.appid === 2 ? { r: { mime: 'image/png', data: PIXEL } } : { ok: false, e: { code: 'no_cover', msg: 'Pas de jaquette dans le cache de Steam.' } }
    },
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('game-2').locator('img')).toHaveAttribute('src', /^data:image\/png/)
  await expect(page.getByTestId('game-1').locator('.plain')).toHaveText('Sans pochette')
  await page.waitForTimeout(600)
  expect(mock.acts.filter((a) => a.a === 'steam.cover' && a.p.appid === 1)).toHaveLength(1)
})

test('Steam : le jeu en cours passe en tête, un refus de lancement est affiché', async ({ page, stack }) => {
  await mockDock(page, { steam: { games: [game(1, 'Premier', 900), game(2, 'Deuxième', 100), game(3, 'Troisième', 500)], client: true, running: 2 } }, {
    startPage: 'steam',
    onAct: (act) => (act.a === 'steam.launch' ? refuse('Impossible de démarrer Steam : « steam » est introuvable.') : undefined),
  })
  await page.goto(stack.url)
  const order = await page.locator('[data-testid="steam-grid"] .game').evaluateAll((els) => els.map((el) => el.getAttribute('title')))
  expect(order).toEqual(['Deuxième', 'Premier', 'Troisième']) // celui qui tourne d'abord, puis les plus récents
  await expect(page.getByTestId('badge-2')).toHaveText('En cours')
  await page.getByTestId('game-1').tap()
  await expect(page.getByTestId('toast')).toContainText('« steam » est introuvable')
  await expect(page.getByTestId('badge-1')).toHaveCount(0)
})

test('Office : LibreOffice absent, la page le dit', async ({ page, stack }) => {
  await mockDock(page, { office: { unavailable: "LibreOffice n'est pas installé sur ce PC.", kinds: [], recent: [] } }, { startPage: 'office' })
  await page.goto(stack.url)
  await expect(page.getByTestId('office-unavailable')).toContainText('LibreOffice indisponible')
  await expect(page.getByTestId('office-new')).toHaveCount(0)
  await shot(page, '11-office-absent', 'd')
})

test('Office : sans document récent, les trois boutons restent utilisables', async ({ page, stack }) => {
  const mock = await mockDock(page, { office: { kinds: ['writer', 'calc', 'impress'], recent: [] } }, { startPage: 'office' })
  await page.goto(stack.url)
  await expect(page.getByTestId('office-none')).toContainText('Aucun document récent')
  for (const kind of ['writer', 'calc', 'impress']) await page.getByTestId(`new-${kind}`).tap()
  expect(mock.acts.filter((a) => a.a === 'office.new').map((a) => a.p.kind)).toEqual(['writer', 'calc', 'impress'])
})

test('Office : des noms de document démesurés sont coupés et la liste défile dans sa carte', async ({ page, stack }) => {
  const recent = Array.from({ length: 12 }, (_, i) => ({
    id: i.toString(16).padStart(16, '0'),
    name: `Document ${i + 1} — compte rendu détaillé de la réunion annuelle des copropriétaires de la résidence.odt`,
    kind: ['writer', 'calc', 'impress', 'draw', 'other'][i % 5],
    where: '~/Documents/Administratif/Copropriété/2026/Assemblées générales',
    modified: i === 3 ? null : Math.floor(Date.now() / 1000) - 3600 * (i + 1),
  }))
  await mockDock(page, { office: { kinds: ['writer', 'calc', 'impress'], recent } }, { startPage: 'office' })
  await page.goto(stack.url)
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size).toEqual({ w: 1024, h: 600 })
  const list = page.locator('[data-testid="office-recent"] .list')
  expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)
  const row = (await page.getByTestId(`recent-${recent[0].id}`).boundingBox())!
  const when = (await page.getByTestId(`recent-${recent[0].id}`).locator('.when').boundingBox())!
  expect(when.x + when.width).toBeLessThanOrEqual(row.x + row.width + 1)
})
