// États limites des pages Système, Musique et Spotify : on simule le dock (WebSocket factice) pour produire des
// cas que la démonstration ne montre pas : aucun lecteur, bus D-Bus absent, carte NVIDIA éveillée,
// 24 cœurs, processus têtu, textes très longs. Le vrai chemin (dock + agent) est couvert par stage-b.spec.ts.

import type { Page, WebSocketRoute } from '@playwright/test'
import { expect, openPage, shot, test, THUMB, tileOrder, typeOnKeyboard } from './fixtures'

type Topics = Record<string, unknown>
interface Mock {
  /** Messages `act` reçus du navigateur, dans l'ordre. */
  acts: { a: string; p: Record<string, unknown>; confirm?: string }[]
  /** Tous les messages reçus du navigateur (abonnements compris). */
  received: { t: string; topics?: string[] }[]
  /** Remplace l'instantané d'un thème et le renvoie aux abonnés. */
  push(topic: string, data: unknown): void
}

const HOST = {
  hostname: 'portable',
  os: 'Ubuntu 26.04 LTS',
  kernel: '7.0.0-38-generic',
  arch: 'x86_64',
  python: '3.14.4',
  agent: { version: '1.1.0', proto: 1, demo: false },
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
    /** La page ouverte au chargement (on touche son bouton sur l'accueil) ; « home » : on reste sur l'accueil. */
    startPage: string
    pinned?: string[]
    /** Pages cachées, ordre choisi, page de départ : surcharge de l'état d'interface du dock. */
    ui?: { order?: string[]; hidden?: string[]; last_page?: string }
    /** Résumé de la bourse du thème « dock » (Réglages). */
    markets?: { count: number; currency: 'EUR' | 'USD'; refresh: 15 | 30 | 60 | 300; finnhub: boolean; demo: boolean }
    onAct?: (act: Mock['acts'][number], ws: WebSocketRoute) => unknown
  },
): Promise<Mock> {
  // Le dock repart toujours de l'accueil : pour arriver sur une page, on touche son bouton une fois la page chargée.
  if (options.startPage !== 'home') {
    const goto = page.goto.bind(page)
    page.goto = (async (url: string, gotoOptions?: Parameters<Page['goto']>[1]) => {
      const response = await goto(url, gotoOptions)
      await openPage(page, options.startPage)
      return response
    }) as Page['goto']
  }

  const subscribed = new Set<string>()
  const acts: Mock['acts'] = []
  const received: Mock['received'] = []
  let current: WebSocketRoute | null = null
  const data: Topics = { host: HOST, ...topics }
  const snap = (ws: WebSocketRoute, topic: string, payload: unknown) =>
    ws.send(JSON.stringify({ t: 'snap', topic, rev: 1, data: payload }))

  await page.routeWebSocket(/\/ws$/, (ws) => {
    current = ws
    ws.send(JSON.stringify({ t: 'hello', proto: 1, agent: { role: 'dock', version: '1.1.0' } }))
    snap(ws, 'link', {
      state: 'connected', detail: 'Connecté.', since: Date.now(), attempt: 0, next_retry_at: null,
      resolved: '127.0.0.1', agent: { version: '1.1.0', demo: false }, host: '127.0.0.1', port: 8765,
    }) // prettier-ignore
    snap(ws, 'dock', {
      version: '1.1.0', proto: 1, hostname: 'raspberrypi', ips: ['192.168.1.60'],
      pc: { host: '127.0.0.1', port: 8765, token_set: true, name: 'portable' },
      wol: { mac: null, source: null, iface: null, kind: null, interfaces: [] },
      markets: options.markets ?? { count: 2, currency: 'EUR', refresh: 15, finnhub: false, demo: false },
    }) // prettier-ignore
    snap(ws, 'ui', { last_page: options.startPage === 'home' ? 'system' : options.startPage, order: [], hidden: [], pinned_apps: options.pinned ?? [], ...options.ui })
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw))
      received.push(message)
      if (message.t === 'unsub') {
        for (const topic of message.topics as string[]) subscribed.delete(topic)
      } else if (message.t === 'sub') {
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
    received,
    push(topic, payload) {
      data[topic] = payload
      if (current && (['link', 'dock', 'ui', 'player'].includes(topic) || subscribed.has(topic))) snap(current, topic, payload)
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
  await expect(page.getByTestId('music-empty')).toContainText('Spotify n’est pas ouvert')
  await expect(page.getByTestId('sp-launch')).toBeVisible() // et le bouton qui le lance
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
  expect(thumb.x + thumb.width / 2).toBeCloseTo(track.x + track.width - THUMB.lg / 2, 0) // bout de course, pas au-delà
  await shot(page, '08-son-amplifie', 'c')
})

test('Son : beaucoup d’applications, des noms longs et des homonymes : la page défile dans son cadre', async ({ page, stack }) => {
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
  const main = page.getByTestId('page-sound')
  expect(await main.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true) // texte doublé : on fait glisser la page
  await shot(page, '07-son-beaucoup-d-applications', 'c')
  // Le nom trop long est coupé, il ne pousse ni le curseur ni le bouton hors de la ligne.
  const row = (await page.getByTestId('app-4').boundingBox())!
  const mute = (await page.getByTestId('app-4-mute').boundingBox())!
  expect(mute.x + mute.width).toBeLessThanOrEqual(row.x + row.width + 1)
  // Au bout de la page on atteint la dernière application, et l'écran n'a pas bougé.
  await main.evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await expect(page.getByTestId('app-19')).toBeInViewport()
  await noPageScroll(page)
})

test('Son : glisser un curseur n’envoie que quelques réglages, et le dernier est la valeur finale', async ({ page, stack }) => {
  const mock = await mockDock(page, { audio: audioData() }, { startPage: 'sound' })
  await page.goto(stack.url)
  const box = (await page.getByTestId('out-slider').boundingBox())!
  const y = box.y + box.height / 2
  const at = (value: number) => box.x + THUMB.lg / 2 + (box.width - THUMB.lg) * (value / 100)
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
  // La page se charge à la demande : on attend la grille au lieu de la lire à l'instant où la navigation se termine.
  await expect(page.locator('[data-testid="apps-grid"] .name')).toHaveText(['Firefox', 'org.disparue.App', 'Calculatrice'])
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
  await expect(page.getByTestId('picker-tab-chosen')).toContainText('24/24')
  for (const id of ['a24.desktop', 'a29.desktop']) await expect(page.getByTestId(`pick-${id}`)).toBeDisabled()
  await expect(page.getByTestId('pick-a00.desktop')).toBeEnabled() // retirer reste possible
  await shot(page, '09-selecteur-plein', 'd')
  await page.getByTestId('pick-a00.desktop').tap()
  await expect(page.getByTestId('picker-tab-chosen')).toContainText('23/24')
  await expect(page.getByTestId('pick-a24.desktop')).toBeEnabled()
  await page.getByTestId('pick-a24.desktop').tap()
  await expect(page.getByTestId('picker-tab-chosen')).toContainText('24/24')
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
  expect(first).toBeGreaterThanOrEqual(4) // texte doublé : une jaquette fait 354 px de haut, on voit une rangée et demie
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

test('Office : des noms de document démesurés sont coupés et la page défile dans son cadre', async ({ page, stack }) => {
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
  const main = page.getByTestId('page-office')
  expect(await main.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true) // douze documents : on fait glisser la page
  expect(await main.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true) // jamais de côté
  const row = (await page.getByTestId(`recent-${recent[0].id}`).boundingBox())!
  const when = (await page.getByTestId(`recent-${recent[0].id}`).locator('.when').boundingBox())!
  expect(when.x + when.width).toBeLessThanOrEqual(row.x + row.width + 1)
})

// --- Bourse ---------------------------------------------------------------------------------------

const nowSeconds = () => Math.floor(Date.now() / 1000)
const sparkOf = (base: number) => Array.from({ length: 48 }, (_, i) => base + Math.sin(i / 5) * base * 0.01 + i * base * 0.0004)

const quote = (id: string, extra: Record<string, unknown> = {}) => {
  const [kind, symbol] = id.split(':')
  return {
    id, kind, symbol,
    name: symbol === 'BTC' ? 'Bitcoin' : symbol === 'ETH' ? 'Ethereum' : symbol,
    price: 61250.5, currency: 'EUR', change: 850.5, change_pct: 1.41, high: 61800, low: 60100,
    state: null, traded_at: nowSeconds(), spark: sparkOf(61000), source: 'binance',
    fetched: nowSeconds(), error: null,
    ...extra,
  }
} // prettier-ignore

const provider = (id: string, label: string, state = 'ok', message: string | null = null, retry_at: number | null = null) => ({ id, label, state, message, retry_at })
const OK_PROVIDERS = [provider('binance', 'Binance'), provider('yahoo', 'Yahoo Finance')]
const marketsData = (items: unknown[], extra: Record<string, unknown> = {}) => ({
  currency: 'EUR',
  demo: false,
  items,
  fetched_at: nowSeconds(),
  providers: OK_PROVIDERS,
  ...extra,
})
const history = (count: number) => ({
  id: 'crypto:BTC',
  range: '1d',
  currency: 'EUR',
  source: 'binance',
  points: Array.from({ length: count }, (_, i) => [nowSeconds() - (count - i) * 900, 60000 + Math.sin(i / 6) * 900 + i * 12]),
  fetched: nowSeconds(),
}) // prettier-ignore

test('Bourse : aucune valeur suivie, la page propose d’en choisir', async ({ page, stack }) => {
  await mockDock(page, { markets: marketsData([]) }, { startPage: 'markets' })
  await page.goto(stack.url)
  await expect(page.getByTestId('markets-empty')).toContainText('Aucune valeur suivie')
  await expect(page.getByTestId('markets-refresh')).toBeDisabled()
  await page.getByTestId('markets-choose').tap()
  await expect(page.getByTestId('mk-editor')).toBeVisible()
  await page.getByTestId('mk-tab-list').tap() // la liste est sur son volet
  await expect(page.getByTestId('mk-none')).toBeVisible()
})

test('Bourse : avant la première cotation, « … » et « Chargement », sans fausse alerte', async ({ page, stack }) => {
  const loading = (id: string) => quote(id, { price: null, change: null, change_pct: null, high: null, low: null, spark: [], source: null, fetched: null })
  await mockDock(page, { markets: marketsData([loading('crypto:BTC'), loading('stock:MSTR')], { fetched_at: null }) }, { startPage: 'markets' })
  await page.goto(stack.url)
  await expect(page.getByTestId('markets-statustext')).toHaveText('Chargement des cotations…')
  await expect(page.getByTestId('price-crypto:BTC')).toHaveText('…')
  await expect(page.getByTestId('markets-problem')).toHaveCount(0)
  await expect(page.getByTestId('old-crypto:BTC')).toHaveCount(0)
  await shot(page, '11-chargement', 'e')
})

test('Bourse : une cotation ancienne garde son prix, son âge et la raison de l’échec', async ({ page, stack }) => {
  const threeHoursAgo = nowSeconds() - 3 * 3600
  const stale = quote('crypto:BTC', { fetched: threeHoursAgo, error: 'Binance : Binance n’est pas accessible depuis ce pays (HTTP 451).' })
  const fresh = quote('stock:MSTR', { name: 'Strategy Inc', price: 182.4, currency: 'USD', change_pct: -2.3, state: 'closed', source: 'yahoo' })
  await mockDock(
    page,
    {
      markets: marketsData([stale, fresh], {
        fetched_at: nowSeconds(),
        providers: [
          provider('binance', 'Binance', 'paused', 'Binance n’est pas accessible depuis ce pays (HTTP 451).', nowSeconds() + 120),
          provider('yahoo', 'Yahoo Finance'),
        ],
      }),
    },
    { startPage: 'markets' },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('price-crypto:BTC')).toContainText('61') // la dernière cotation reste affichée
  await expect(page.getByTestId('old-crypto:BTC')).toContainText('il y a 3')
  await expect(page.getByTestId('quote-crypto:BTC')).toHaveClass(/stale/)
  await expect(page.getByTestId('markets-problem')).toContainText('Binance n’est pas accessible')
  await expect(page.getByTestId('markets-problem')).toContainText('Nouvel essai dans')
  await expect(page.getByTestId('markets-problem')).toContainText('derniers connus')
  await expect(page.getByTestId('old-stock:MSTR')).toHaveCount(0) // la valeur à jour n'a pas d'avertissement
  await expect(page.getByTestId('session-stock:MSTR')).toContainText('Marché fermé')
  await shot(page, '10-cotations-anciennes', 'e')
  // Le détail le redit : âge et raison.
  await page.getByTestId('quote-crypto:BTC').tap()
  await expect(page.getByTestId('detail-meta')).toContainText('il y a 3')
  await expect(page.getByTestId('detail-meta')).toContainText('HTTP 451')
})

test('Bourse : aucun fournisseur ne répond et aucune cotation n’est connue', async ({ page, stack }) => {
  const failed = (id: string) =>
    quote(id, { price: null, change: null, change_pct: null, high: null, low: null, spark: [], source: null, fetched: null, error: 'Yahoo Finance : Le nom query2.finance.yahoo.com est introuvable.' })
  await mockDock(
    page,
    {
      markets: marketsData([failed('stock:MSTR'), failed('stock:AAPL')], {
        fetched_at: null,
        providers: [
          provider('binance', 'Binance', 'paused', 'Le nom api.binance.com est introuvable.', nowSeconds() + 40),
          provider('yahoo', 'Yahoo Finance', 'paused', 'Le nom query2.finance.yahoo.com est introuvable : le Pi n’a pas d’accès à Internet ou de DNS.', nowSeconds() + 40),
        ],
      }),
    },
    {
      startPage: 'markets',
      onAct: (act) =>
        act.a === 'dock.markets.diagnose'
          ? {
              r: {
                checks: [
                  { label: 'Heure du Pi', ok: true, detail: '09/11/2026 17:43 UTC' },
                  { label: 'query2.finance.yahoo.com', ok: true, detail: 'IPv4 0,04 s · IPv6 : pas de réponse en 3 s' },
                  { label: 'Yahoo Finance', ok: false, detail: 'Yahoo Finance refuse la demande (HTTP 429).' },
                ],
                verdict: 'Les serveurs répondent mais refusent les demandes : lis le message de chaque fournisseur.',
              },
            }
          : undefined,
    },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('markets-statustext')).toHaveText('Cotations indisponibles')
  await expect(page.getByTestId('price-stock:MSTR')).toHaveText('—')
  await expect(page.getByTestId('error-stock:MSTR')).toContainText('introuvable')
  await expect(page.getByTestId('markets-problem')).toContainText("pas d’accès à Internet")
  await expect(page.getByTestId('markets-problem')).not.toContainText('derniers connus') // il n'y en a pas
  await shot(page, '12-fournisseurs-en-panne', 'e')

  // « Tester la connexion » dit où ça coince : heure, serveurs (IPv4 / IPv6), chaque fournisseur, et quoi faire.
  await page.getByTestId('markets-diagnose').tap()
  await expect(page.getByTestId('markets-diag-row')).toHaveCount(3)
  await expect(page.getByTestId('markets-diag-row').nth(1)).toContainText('IPv6 : pas de réponse')
  await expect(page.getByTestId('markets-diag-row').nth(2)).toContainText('HTTP 429')
  await expect(page.getByTestId('markets-diag-verdict')).toContainText('refusent les demandes')
  await page.getByTestId('markets-diag').scrollIntoViewIfNeeded()
  await shot(page, '15-bourse-test-de-connexion', 'j')
  await page.getByTestId('markets-diag-close').tap()
  await expect(page.getByTestId('markets-diag')).toHaveCount(0)
})

test('Bourse : devise inconnue, variation absente, noms démesurés et vingt valeurs sans débordement', async ({ page, stack }) => {
  const long = 'Un fonds indiciel mondial aux frais réduits, capitalisation et distribution, classe de parts institutionnelle'
  const items = [
    quote('stock:VOD', { name: 'Vodafone', price: 71.34, currency: 'GBp', change: null, change_pct: null, source: 'yahoo' }),
    quote('stock:LONG', { name: long, price: 489.2, currency: 'EUR', source: 'yahoo' }),
    ...Array.from({ length: 18 }, (_, i) => quote(`stock:S${i}`, { name: `Valeur ${i}`, price: 10 + i, change_pct: i % 2 ? -1.2 : 0, source: 'yahoo' })),
  ]
  await mockDock(page, { markets: marketsData(items) }, { startPage: 'markets' })
  await page.goto(stack.url)
  await expect(page.locator('[data-testid^="quote-"]')).toHaveCount(20)
  await expect(page.getByTestId('price-stock:VOD')).toContainText('GBp')
  await expect(page.getByTestId('pct-stock:VOD')).toContainText('—')
  await expect(page.getByTestId('pct-stock:S2')).toContainText('0,00') // zéro n'est ni vert ni rouge
  await expect(page.getByTestId('pct-stock:S2')).toHaveClass(/flat/)
  await noPageScroll(page)
  const card = (await page.getByTestId('quote-stock:LONG').boundingBox())!
  const name = (await page.getByTestId('quote-stock:LONG').locator('.name').boundingBox())!
  expect(name.x + name.width).toBeLessThanOrEqual(card.x + card.width) // le nom est coupé, il ne déborde pas
  const grid = page.getByTestId('markets-list')
  const main = page.getByTestId('page-markets')
  expect(await main.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true) // la liste défile dans sa zone
  await expect(grid).toBeVisible()
})

test('Bourse : les cotations se mettent à jour en direct, sans recharger', async ({ page, stack }) => {
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, { startPage: 'markets' })
  await page.goto(stack.url)
  await expect(page.getByTestId('price-crypto:BTC')).toContainText('61')
  mock.push('markets', marketsData([quote('crypto:BTC', { price: 62100.2, change_pct: -0.5, change: -310 })]))
  await expect(page.getByTestId('price-crypto:BTC')).toContainText('62')
  await expect(page.getByTestId('pct-crypto:BTC')).toHaveClass(/down/)
  await expect(page.getByTestId('pct-crypto:BTC')).toContainText('−0,50')
})

test('Bourse : actualiser demande au dock, et son refus est affiché', async ({ page, stack }) => {
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'markets',
    onAct: (act) => (act.a === 'dock.markets.refresh' ? refuse('Les fournisseurs ne répondent pas.') : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('markets-refresh').tap()
  await expect(page.getByTestId('toast')).toContainText('Les fournisseurs ne répondent pas.')
  expect(mock.acts.filter((a) => a.a === 'dock.markets.refresh')).toHaveLength(1)
})

test('Bourse : la page s’abonne au thème « markets » en arrivant et s’en désabonne en partant', async ({ page, stack }) => {
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, { startPage: 'markets' })
  await page.goto(stack.url)
  await expect(page.getByTestId('price-crypto:BTC')).toContainText('61')
  const topicsOf = (kind: string) => mock.received.filter((m) => m.t === kind).flatMap((m) => m.topics ?? [])
  expect(topicsOf('sub')).toContain('markets')
  expect(topicsOf('unsub')).not.toContain('markets')
  await openPage(page, 'settings')
  await expect(page.getByTestId('settings-markets')).toBeVisible()
  await expect.poll(() => topicsOf('unsub')).toContain('markets') // plus personne ne regarde : le dock cesse de demander
})

// --- détail ---------------------------------------------------------------------------------------

test('Détail : l’historique en erreur est expliqué, « Réessayer » le redemande', async ({ page, stack }) => {
  let calls = 0
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'markets',
    onAct: (act) => {
      if (act.a !== 'dock.markets.history') return undefined
      calls += 1
      return calls === 1 ? refuse('Historique indisponible : Binance : Binance ne répond pas.') : { r: history(60) }
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('quote-crypto:BTC').tap()
  await expect(page.getByTestId('detail-error')).toContainText('Binance ne répond pas')
  await page.getByTestId('detail-retry').tap()
  await expect(page.getByTestId('price-chart')).toBeVisible()
  await expect(page.getByTestId('detail-error')).toHaveCount(0)
  expect(mock.acts.filter((a) => a.a === 'dock.markets.history').map((a) => a.p)).toEqual([
    { id: 'crypto:BTC', range: '1d' },
    { id: 'crypto:BTC', range: '1d' },
  ])
})

test('Détail : trop peu de points pour tracer, la page le dit', async ({ page, stack }) => {
  await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'markets',
    onAct: (act) => (act.a === 'dock.markets.history' ? { r: history(1) } : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('quote-crypto:BTC').tap()
  await expect(page.getByTestId('chart-none')).toContainText('Pas assez de points')
})

test('Détail : changer de période redemande, revenir à une période déjà vue ne redemande pas', async ({ page, stack }) => {
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'markets',
    onAct: (act) => (act.a === 'dock.markets.history' ? { r: { ...history(80), range: act.p.range } } : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('quote-crypto:BTC').tap()
  await expect(page.getByTestId('price-chart')).toBeVisible()
  await page.getByTestId('range-1mo').tap()
  await expect(page.getByTestId('range-1mo')).toHaveAttribute('aria-pressed', 'true')
  await page.getByTestId('range-1d').tap()
  await expect(page.getByTestId('range-1d')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('price-chart')).toBeVisible()
  expect(mock.acts.filter((a) => a.a === 'dock.markets.history').map((a) => a.p.range)).toEqual(['1d', '1mo'])
})

test('Détail : une valeur retirée pendant qu’on la regarde ramène à la liste', async ({ page, stack }) => {
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC'), quote('crypto:ETH')]) }, {
    startPage: 'markets',
    onAct: (act) => (act.a === 'dock.markets.history' ? { r: history(40) } : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('quote-crypto:BTC').tap()
  await expect(page.getByTestId('detail')).toBeVisible()
  mock.push('markets', marketsData([quote('crypto:ETH')]))
  await expect(page.getByTestId('detail')).toHaveCount(0)
  await expect(page.getByTestId('quote-crypto:ETH')).toBeVisible()
})

// --- fenêtre de modification -----------------------------------------------------------------------

test('Modifier : la liste complète part dans l’ordre, la devise seule part seule', async ({ page, stack }) => {
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC'), quote('crypto:ETH')]) }, {
    startPage: 'markets',
    onAct: (act) => {
      if (act.a === 'dock.markets.check') return { r: { id: 'stock:MSTR', kind: 'stock', symbol: 'MSTR', name: 'Strategy Inc', price: 182.4, currency: 'USD', source: 'yahoo' } }
      return undefined
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('markets-edit').tap()
  await page.getByTestId('mk-kind-stock').tap()
  await page.getByTestId('mk-suggest-MSTR').tap()
  await expect(page.getByTestId('mk-found')).toContainText('Strategy Inc')
  await page.getByTestId('mk-confirm').tap()
  await page.getByTestId('mk-tab-list').tap() // la devise et l'ordre sont sur le volet « Liste »
  await page.getByTestId('mk-currency-USD').tap()
  await page.getByTestId('mk-down-crypto:BTC').tap()
  const sets = mock.acts.filter((a) => a.a === 'dock.markets.set').map((a) => a.p)
  expect(sets).toEqual([
    { watchlist: [{ kind: 'crypto', symbol: 'BTC' }, { kind: 'crypto', symbol: 'ETH' }, { kind: 'stock', symbol: 'MSTR' }] },
    { currency: 'USD' },
    { watchlist: [{ kind: 'crypto', symbol: 'ETH' }, { kind: 'crypto', symbol: 'BTC' }] },
  ])
  expect(mock.acts.filter((a) => a.a === 'dock.markets.check').map((a) => a.p)).toEqual([{ kind: 'stock', symbol: 'MSTR' }])
})

test('Modifier : une vérification refusée est expliquée et ne change rien', async ({ page, stack }) => {
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'markets',
    onAct: (act) => (act.a === 'dock.markets.check' ? refuse('Impossible de vérifier ce symbole : Yahoo Finance : Yahoo Finance ne répond pas.') : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('markets-edit').tap()
  await page.getByTestId('mk-suggest-ETH').tap()
  await expect(page.getByTestId('mk-error')).toContainText('Impossible de vérifier')
  await expect(page.getByTestId('mk-found')).toHaveCount(0)
  expect(mock.acts.filter((a) => a.a === 'dock.markets.set')).toEqual([])
})

test('Modifier : à vingt valeurs on ne peut plus qu’en retirer', async ({ page, stack }) => {
  const items = Array.from({ length: 20 }, (_, i) => quote(`stock:S${i}`, { name: `Valeur ${i}`, source: 'yahoo' }))
  const mock = await mockDock(page, { markets: marketsData(items) }, {
    startPage: 'markets',
    onAct: (act) => (act.a === 'dock.markets.check' ? { r: { id: 'crypto:BTC', kind: 'crypto', symbol: 'BTC', name: 'Bitcoin', price: 61000, currency: 'EUR', source: 'binance' } } : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('markets-edit').tap()
  await expect(page.getByTestId('mk-tab-list')).toContainText('20/20')
  await page.getByTestId('mk-suggest-BTC').tap()
  await expect(page.getByTestId('mk-found')).toBeVisible()
  await expect(page.getByTestId('mk-confirm')).toBeDisabled()
  expect(mock.acts.filter((a) => a.a === 'dock.markets.set')).toEqual([])
  await page.getByTestId('mk-tab-list').tap()
  await page.getByTestId('mk-remove-stock:S19').tap()
  expect(mock.acts.filter((a) => a.a === 'dock.markets.set')).toHaveLength(1)
})

test('Modifier : une réponse sans prix du dock n’affiche pas une carte vide', async ({ page, stack }) => {
  await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, { startPage: 'markets' })
  await page.goto(stack.url)
  await page.getByTestId('markets-edit').tap()
  await page.getByTestId('mk-suggest-ETH').tap() // réponse par défaut du faux dock : {}
  await expect(page.getByTestId('mk-error')).toContainText('Vérification impossible')
  await expect(page.getByTestId('mk-found')).toHaveCount(0)
})

// --- Réglages -------------------------------------------------------------------------------------

test('Réglages : la carte Bourse au singulier, en dollars, avec la clé Finnhub signalée', async ({ page, stack }) => {
  await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'settings',
    markets: { count: 1, currency: 'USD', refresh: 15, finnhub: true, demo: false },
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('markets-count')).toContainText('1 valeur suivie sur 20')
  await expect(page.getByTestId('markets-count')).toContainText('en dollars')
  await expect(page.getByTestId('markets-note')).toContainText('Clé Finnhub de secours : configurée')
  await expect(page.getByTestId('markets-note')).not.toContainText('démonstration')
})

test('Réglages : sans valeur suivie, la carte le dit', async ({ page, stack }) => {
  await mockDock(page, {}, { startPage: 'settings', markets: { count: 0, currency: 'EUR', refresh: 15, finnhub: false, demo: false } })
  await page.goto(stack.url)
  await expect(page.getByTestId('markets-count')).toHaveText('Aucune valeur suivie.')
  await expect(page.getByTestId('markets-note')).toContainText('aucune (facultative)')
})

// --- Bourse : recherche, fréquence, dernier échange ----------------------------------------------

const hit = (symbol: string, name: string, extra: Record<string, unknown> = {}) => ({
  kind: 'stock', symbol, name, exchange: 'NASDAQ', label: 'Action', ...extra,
}) // prettier-ignore
const searchReply = (query: string, results: unknown[], source = 'yahoo') => ({ r: { query, kind: 'stock', source, results } })
const found = (symbol: string, name: string) => ({ r: { id: `stock:${symbol}`, kind: 'stock', symbol, name, price: 182.4, currency: 'USD', source: 'yahoo' } })

test('Recherche : un résultat est vérifié puis ajouté d’un seul toucher, la liste complète part dans l’ordre', async ({ page, stack }) => {
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'markets',
    onAct: (act) => {
      if (act.a === 'dock.markets.search') return searchReply(String(act.p.query), [hit('AAPL', 'Apple Inc.'), hit('APLE', 'Apple Hospitality REIT', { exchange: 'NYSE' })])
      if (act.a === 'dock.markets.check') return found(String(act.p.symbol), 'Apple Inc.')
      return undefined
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('markets-edit').tap()
  await page.getByTestId('mk-kind-stock').tap()
  await page.getByTestId('mk-search').tap()
  await typeOnKeyboard(page, 'apple')
  await page.getByTestId('keyboard-ok').tap()
  await expect(page.getByTestId('mk-results')).toContainText('Apple Hospitality REIT')
  await expect(page.getByTestId('mk-results')).toContainText('APLE · NYSE · Action')
  await page.getByTestId('mk-result-AAPL').tap()
  await expect(page.getByTestId('toast')).toContainText('« Apple Inc. » ajouté')
  expect(mock.acts.map((a) => [a.a, a.p])).toEqual([
    ['dock.markets.search', { kind: 'stock', query: 'apple' }],
    ['dock.markets.check', { kind: 'stock', symbol: 'AAPL' }],
    ['dock.markets.set', { watchlist: [{ kind: 'crypto', symbol: 'BTC' }, { kind: 'stock', symbol: 'AAPL' }] }],
  ])
})

test('Recherche : aucun résultat, ou fournisseurs en panne, on peut vérifier le texte tel quel', async ({ page, stack }) => {
  let searches = 0
  const mock = await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'markets',
    onAct: (act) => {
      if (act.a === 'dock.markets.search') {
        searches += 1
        return searches === 1 ? searchReply(String(act.p.query), []) : refuse('Recherche impossible : Yahoo Finance : Yahoo Finance ne répond pas.')
      }
      if (act.a === 'dock.markets.check') return found(String(act.p.symbol), 'Strategy Inc')
      return undefined
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('markets-edit').tap()
  await page.getByTestId('mk-kind-stock').tap()
  await page.getByTestId('mk-search').tap()
  await typeOnKeyboard(page, 'mstr')
  await page.getByTestId('keyboard-ok').tap()
  await expect(page.getByTestId('mk-noresult')).toContainText('Aucun résultat pour « mstr »')
  await page.getByTestId('mk-search').tap()
  await page.getByTestId('keyboard-ok').tap() // même texte : on le cherche encore, cette fois sans réseau
  await expect(page.getByTestId('mk-error')).toContainText('Recherche impossible')
  await page.getByTestId('mk-asis').tap()
  await expect(page.getByTestId('mk-found')).toContainText('Strategy Inc')
  await page.getByTestId('mk-confirm').tap()
  await expect.poll(() => mock.acts.filter((a) => a.a === 'dock.markets.set').length).toBe(1)
  expect(mock.acts.find((a) => a.a === 'dock.markets.check')?.p).toEqual({ kind: 'stock', symbol: 'MSTR' }) // devenu majuscules
})

test('Recherche : une phrase n’est pas un symbole, « tel quel » n’est pas proposé', async ({ page, stack }) => {
  await mockDock(page, { markets: marketsData([quote('crypto:BTC')]) }, {
    startPage: 'markets',
    onAct: (act) => (act.a === 'dock.markets.search' ? searchReply(String(act.p.query), []) : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('markets-edit').tap()
  await page.getByTestId('mk-kind-stock').tap()
  await page.getByTestId('mk-search').tap()
  await typeOnKeyboard(page, 'air liquide')
  await page.getByTestId('keyboard-ok').tap()
  await expect(page.getByTestId('mk-noresult')).toBeVisible()
  await expect(page.getByTestId('mk-asis')).toHaveCount(0)
})

test('Recherche : les résultats déjà suivis sont grisés, et la liste pleine empêche tout ajout', async ({ page, stack }) => {
  const items = Array.from({ length: 20 }, (_, i) => quote(`stock:S${i}`, { name: `Valeur ${i}`, source: 'yahoo' }))
  await mockDock(page, { markets: marketsData(items) }, {
    startPage: 'markets',
    onAct: (act) => (act.a === 'dock.markets.search' ? searchReply(String(act.p.query), [hit('S3', 'Valeur 3'), hit('NEW', 'Nouvelle valeur')]) : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('markets-edit').tap()
  await page.getByTestId('mk-kind-stock').tap()
  await page.getByTestId('mk-search').tap()
  await typeOnKeyboard(page, 'valeur')
  await page.getByTestId('keyboard-ok').tap()
  await expect(page.getByTestId('mk-result-S3')).toHaveText('Suivie')
  await expect(page.getByTestId('mk-result-S3')).toBeDisabled()
  await expect(page.getByTestId('mk-result-NEW')).toBeDisabled() // 20/20
})

test('Dernier échange : une action en séance dont le cours est ancien le dit, pas une crypto ni un marché fermé', async ({ page, stack }) => {
  const long = (id: string, extra: Record<string, unknown>) => quote(id, { traded_at: nowSeconds() - 1_200, ...extra })
  await mockDock(page, {
    markets: marketsData([
      long('stock:AAPL', { state: 'open', source: 'yahoo' }),
      long('stock:MSTR', { state: 'closed', source: 'yahoo' }),
      long('crypto:BTC', {}),
      quote('stock:NVDA', { state: 'open', source: 'yahoo', traded_at: nowSeconds() - 60 }),
    ]),
  }, { startPage: 'markets' })
  await page.goto(stack.url)
  await expect(page.getByTestId('lag-stock:AAPL')).toContainText('dernier échange il y a 20 min')
  await expect(page.getByTestId('lag-stock:MSTR')).toHaveCount(0)
  await expect(page.getByTestId('lag-crypto:BTC')).toHaveCount(0)
  await expect(page.getByTestId('lag-stock:NVDA')).toHaveCount(0)
})

test('Réglages : la fréquence des mises à jour se choisit et part seule', async ({ page, stack }) => {
  const mock = await mockDock(page, {}, { startPage: 'settings', markets: { count: 3, currency: 'EUR', refresh: 30, finnhub: false, demo: false } })
  await page.goto(stack.url)
  await expect(page.getByTestId('refresh-30')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('refresh-15')).toHaveAttribute('aria-pressed', 'false')
  await page.getByTestId('refresh-300').tap()
  await expect.poll(() => mock.acts.filter((a) => a.a === 'dock.markets.set').map((a) => a.p)).toEqual([{ refresh: 300 }])
})

// --- Alimentation (étape f) ---------------------------------------------------------------------------

const POWER_FULL = {
  hostname: 'portable',
  controls: {
    lock: { ok: true, why: null },
    suspend: { ok: true, why: null },
    reboot: { ok: true, why: null },
    poweroff: { ok: true, why: null },
  },
  blockers: [],
}

/** Refuse la première demande de l'ordre avec une confirmation, comme le fait l'agent. */
function confirming(label: string, then: Record<string, unknown> = {}) {
  return (act: Mock['acts'][number]) => {
    if (!act.a.startsWith('power.') || act.a === 'power.lock') return undefined
    if (!act.confirm) {
      return { ok: false, r: undefined, e: { code: 'confirm_required', msg: 'Confirmation requise' }, confirm: { token: 'tok', ttl: 10, label } }
    }
    return then
  }
}

test('Alimentation : les droits du système grisent les boutons, avec leur raison', async ({ page, stack }) => {
  await mockDock(
    page,
    {
      power: {
        hostname: 'portable',
        controls: {
          lock: { ok: true, why: null },
          suspend: { ok: false, why: 'Indisponible sur ce PC' },
          reboot: { ok: false, why: 'Bloqué par « Mises à jour logicielles »' },
          poweroff: { ok: false, why: 'Bloqué par « Mises à jour logicielles »' },
        },
        blockers: [{ who: 'Mises à jour logicielles', why: 'Installation des mises à jour en cours', scope: ['shutdown'] }],
      },
    },
    { startPage: 'power' },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('power-card')).toContainText('Contrôler « portable »')
  await expect(page.getByTestId('power-lock')).toBeEnabled()
  for (const id of ['suspend', 'reboot', 'poweroff']) await expect(page.getByTestId(`power-${id}`)).toBeDisabled()
  await expect(page.getByTestId('power-why-suspend')).toHaveText('Indisponible sur ce PC')
  await expect(page.getByTestId('power-why-poweroff')).toHaveText('Bloqué par « Mises à jour logicielles »')
  await expect(page.getByTestId('power-blockers')).toContainText("« Mises à jour logicielles » empêche l'extinction (Installation des mises à jour en cours).")
  await shot(page, '05-droits-limites', 'f')
  // Rien ne déborde de l'écran, même avec toutes les raisons affichées.
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size).toEqual({ w: 1024, h: 600 })
})

test('Alimentation : un agent trop ancien pour connaître le thème ne casse pas la page', async ({ page, stack }) => {
  await mockDock(page, { power: { unavailable: "Pas encore disponible dans cette version de l'agent." } }, { startPage: 'power' })
  await page.goto(stack.url)
  await expect(page.getByTestId('power-unavailable')).toContainText('Pas encore disponible')
  for (const id of ['lock', 'suspend', 'reboot', 'poweroff']) {
    await expect(page.getByTestId(`power-${id}`)).toBeDisabled()
    await expect(page.getByTestId(`power-why-${id}`)).toHaveText('Indisponible')
  }
  await expect(page.getByTestId('wol-card')).toBeVisible()
  await shot(page, '06-agent-ancien', 'f')
})

test('Alimentation : un logind qui ne répond pas est annoncé, le verrouillage peut rester possible', async ({ page, stack }) => {
  const down = { ok: false, why: 'logind injoignable' }
  await mockDock(
    page,
    {
      power: {
        hostname: 'portable',
        controls: { lock: { ok: true, why: null }, suspend: down, reboot: down, poweroff: down },
        blockers: [],
        unavailable: 'Le service de session du PC (logind) ne répond pas.',
      },
    },
    { startPage: 'power' },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('power-unavailable')).toContainText('logind')
  await expect(page.getByTestId('power-lock')).toBeEnabled()
  await expect(page.getByTestId('power-why-poweroff')).toHaveText('logind injoignable')
})

test('Alimentation : le refus du système est affiché tel quel et le bouton se libère aussitôt', async ({ page, stack }) => {
  const mock = await mockDock(page, { power: POWER_FULL }, {
    startPage: 'power',
    onAct: confirming('Éteindre « portable » (les programmes ouverts seront fermés)', {
      ok: false,
      r: undefined,
      e: { code: 'forbidden', msg: "« Copie de fichiers » empêche l'extinction." },
    }),
  })
  await page.goto(stack.url)
  await page.getByTestId('power-poweroff').tap()
  await expect(page.getByTestId('confirm')).toContainText('Éteindre « portable »')
  await page.getByTestId('confirm-ok').tap()
  await expect(page.getByTestId('toast')).toContainText("« Copie de fichiers » empêche l'extinction.")
  await expect(page.getByTestId('power-poweroff')).toBeEnabled() // un échec ne verrouille rien
  expect(mock.acts.filter((a) => a.a === 'power.poweroff').map((a) => Boolean(a.confirm))).toEqual([false, true])
  await shot(page, '07-extinction-refusee', 'f')
})

test('Alimentation : si la liaison tombe juste après l’ordre, le message reste prudent', async ({ page, stack }) => {
  await mockDock(page, { power: POWER_FULL }, {
    startPage: 'power',
    onAct: confirming('Mettre « portable » en veille', { ok: false, r: undefined, e: { code: 'pc_offline', msg: 'Connexion au PC perdue.' } }),
  })
  await page.goto(stack.url)
  await page.getByTestId('power-suspend').tap()
  await page.getByTestId('confirm-ok').tap()
  await expect(page.getByTestId('toast')).toContainText('probablement partie')
})

test('Alimentation : un agent qui ne connaît pas la commande dit de mettre le paquet à jour', async ({ page, stack }) => {
  await mockDock(page, { power: POWER_FULL }, {
    startPage: 'power',
    onAct: (act) => (act.a === 'power.lock' ? { ok: false, r: undefined, e: { code: 'unknown_action', msg: 'Action inconnue' } } : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('power-lock').tap()
  await expect(page.getByTestId('toast')).toContainText('mets le paquet du PC à jour')
})

test('Alimentation : un deuxième appui pendant l’envoi n’envoie pas deux ordres', async ({ page, stack }) => {
  const mock = await mockDock(page, { power: POWER_FULL }, { startPage: 'power' })
  await page.goto(stack.url)
  await page.getByTestId('power-lock').tap()
  await page.getByTestId('power-lock').tap({ force: true }) // même si l'écran le laissait passer
  await expect(page.getByTestId('toast')).toContainText('verrouillé')
  expect(mock.acts.filter((a) => a.a === 'power.lock')).toHaveLength(1)
})

test('Alimentation : le dock ne demande les droits au PC que page ouverte', async ({ page, stack }) => {
  const mock = await mockDock(page, { power: POWER_FULL }, { startPage: 'power' })
  await page.goto(stack.url)
  await expect(page.getByTestId('power-lock')).toBeEnabled()
  const topicsOf = (kind: string) => mock.received.filter((m) => m.t === kind).flatMap((m) => m.topics ?? [])
  expect(topicsOf('sub')).toContain('power')
  await openPage(page, 'settings')
  await expect.poll(() => topicsOf('unsub')).toContain('power')
})

// --- Pages visibles et ordre (étape f) --------------------------------------------------------------------

const TAB_IDS = ['music', 'sound', 'apps', 'steam', 'office', 'markets', 'system', 'media', 'settings']

test('Pages : l’accueil suit l’ordre et les pages cachées du dock', async ({ page, stack }) => {
  await mockDock(page, {}, { startPage: 'home', ui: { order: ['media', 'markets'], hidden: ['steam', 'office'] } })
  await page.goto(stack.url)
  expect(await tileOrder(page)).toEqual(['media', 'markets', 'music', 'sound', 'apps', 'system', 'settings'])
  await openPage(page, 'settings')
  await expect(page.getByTestId('pages-count')).toHaveText('7 pages visibles sur 9 (cachées : Steam, Office).')
})

test('Pages : la fenêtre numérote les neuf pages et ne laisse pas cacher les Réglages', async ({ page, stack }) => {
  await mockDock(page, {}, { startPage: 'settings', ui: { hidden: ['steam'] } })
  await page.goto(stack.url)
  await page.getByTestId('choose-pages').tap()
  await expect(page.getByTestId('pages-editor')).toBeVisible()
  for (const id of TAB_IDS) await expect(page.getByTestId(`page-row-${id}`)).toBeVisible()
  await expect(page.getByTestId('page-row-steam')).toContainText('cachée')
  await expect(page.getByTestId('page-row-settings')).toContainText('toujours visible')
  await expect(page.getByTestId('page-toggle-settings')).toBeDisabled()
  await expect(page.getByTestId('page-up-music')).toBeDisabled()
  await expect(page.getByTestId('page-down-settings')).toBeDisabled()
  await expect(page.getByTestId('pages-summary')).toContainText('8 pages visibles sur 9')
  await shot(page, '08-pages-et-ordre', 'f')
  // Toutes les cibles tactiles font au moins 48 px, et rien ne déborde.
  const small = await page.evaluate(() =>
    [...document.querySelectorAll('[data-testid=pages-editor] button')]
      .map((el) => ({ el, box: el.getBoundingClientRect() }))
      .filter(({ box }) => box.width > 0 && Math.min(box.width, box.height) < 48)
      .map(({ el }) => el.getAttribute('data-testid')),
  )
  expect(small).toEqual([])
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size).toEqual({ w: 1024, h: 600 })
})

test('Pages : cacher, montrer, monter et descendre envoient la bonne liste au dock', async ({ page, stack }) => {
  const mock = await mockDock(page, {}, { startPage: 'settings', ui: { hidden: ['steam'] } })
  await page.goto(stack.url)
  await page.getByTestId('choose-pages').tap()

  await page.getByTestId('page-toggle-office').tap()
  await page.getByTestId('page-toggle-steam').tap()
  await page.getByTestId('page-up-media').tap()
  await page.getByTestId('page-down-music').tap()
  await expect.poll(() => mock.acts.filter((a) => a.a === 'dock.ui.set').length).toBe(4)
  const sent = mock.acts.filter((a) => a.a === 'dock.ui.set').map((a) => a.p)
  expect(sent[0]).toEqual({ hidden: ['steam', 'office'] })
  expect(sent[1]).toEqual({ hidden: [] })
  expect(sent[2]).toEqual({ order: ['music', 'sound', 'apps', 'steam', 'office', 'markets', 'media', 'system', 'settings'] })
  expect(sent[3]).toEqual({ order: ['sound', 'music', 'apps', 'steam', 'office', 'markets', 'system', 'media', 'settings'] })
})

test('Pages : « ordre d’origine » rétablit tout, et n’est proposé que s’il y a quelque chose à rétablir', async ({ page, stack }) => {
  const mock = await mockDock(page, {}, { startPage: 'settings', ui: { order: ['media'] } })
  await page.goto(stack.url)
  await page.getByTestId('choose-pages').tap()
  await page.getByTestId('pages-reset').tap()
  await expect.poll(() => mock.acts.filter((a) => a.a === 'dock.ui.set').map((a) => a.p)).toEqual([{ order: [], hidden: [] }])
  mock.push('ui', { last_page: 'settings', order: [], hidden: [], pinned_apps: [] })
  await expect(page.getByTestId('pages-reset')).toBeDisabled()
  await expect(page.getByTestId('pages-summary')).toContainText('9 pages visibles sur 9')
})

test('Pages : l’accueil suit aussitôt ce que le dock enregistre', async ({ page, stack }) => {
  const mock = await mockDock(page, {}, { startPage: 'home' })
  await page.goto(stack.url)
  expect(await tileOrder(page)).toEqual(TAB_IDS)
  mock.push('ui', { last_page: 'settings', order: ['system'], hidden: ['steam'], pinned_apps: [] })
  await expect.poll(() => tileOrder(page)).toEqual(['system', 'music', 'sound', 'apps', 'office', 'markets', 'media', 'settings'])
})

test('Pages : une modification refusée par le dock est expliquée', async ({ page, stack }) => {
  await mockDock(page, {}, {
    startPage: 'settings',
    onAct: (act) => (act.a === 'dock.ui.set' ? { ok: false, r: undefined, e: { code: 'invalid', msg: 'La page Réglages ne peut pas être cachée.' } } : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('choose-pages').tap()
  await page.getByTestId('page-toggle-music').tap()
  await expect(page.getByTestId('toast')).toContainText('ne peut pas être cachée')
})

test('Pages : une page cachée n’a pas de bouton sur l’accueil, même si le dock l’avait en dernière page', async ({ page, stack }) => {
  await mockDock(page, {}, { startPage: 'home', ui: { hidden: ['steam'], last_page: 'steam' } })
  await page.goto(stack.url)
  await expect(page.getByTestId('tile-music')).toBeVisible()
  await expect(page.getByTestId('tile-steam')).toHaveCount(0)
  await expect(page.locator('main.page')).toHaveCount(0) // le dock repart de l'accueil, jamais d'une page restée en mémoire
})

// --- Veille de l'écran (étape f) --------------------------------------------------------------------------

test('Veille : un dock sans réglage d’écran reste sur « Jamais » et ne casse rien', async ({ page, stack }) => {
  await mockDock(page, {}, { startPage: 'settings' }) // l'état d'interface n'a pas de « screen_sleep »
  await page.goto(stack.url)
  await expect(page.getByTestId('sleep-0')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('screen-note')).toHaveText("L'écran reste allumé.")
})

test('Veille : le choix de la durée part au dock tel quel, et un refus est expliqué', async ({ page, stack }) => {
  let refuse = false
  const mock = await mockDock(page, {}, {
    startPage: 'settings',
    onAct: (act) =>
      act.a === 'dock.ui.set' && refuse ? { ok: false, r: undefined, e: { code: 'invalid', msg: "Durée de veille de l'écran invalide." } } : undefined,
  })
  await page.goto(stack.url)
  await page.getByTestId('sleep-600').tap()
  await expect.poll(() => mock.acts.filter((a) => a.a === 'dock.ui.set').map((a) => a.p)).toEqual([{ screen_sleep: 600 }])
  refuse = true
  await page.getByTestId('sleep-1800').tap()
  await expect(page.getByTestId('toast')).toContainText('invalide')
})

test('Veille : un réglage fait depuis un autre navigateur est pris en compte aussitôt', async ({ page, stack }) => {
  const mock = await mockDock(page, {}, { startPage: 'settings' })
  await page.goto(stack.url)
  mock.push('ui', { last_page: 'settings', order: [], hidden: [], pinned_apps: [], screen_sleep: 1800 })
  await expect(page.getByTestId('sleep-1800')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('screen-note')).toContainText('30 minutes')
})

// --- Spotify : les états que la démonstration ne produit pas ---------------------------------------------------------

const ID = (letter: string): string => letter.repeat(22)

function spotifyTrack(extra: Record<string, unknown> = {}) {
  return {
    kind: 'track', id: ID('a'), uri: `spotify:track:${ID('a')}`, name: 'Morceau', artists: 'Artiste', album: 'Album',
    album_id: ID('b'), duration_ms: 200_000, number: 1, explicit: false, playable: true, art: null, ...extra,
  } // prettier-ignore
}

const STATUS_OK = { installed: true, running: true, configured: true, connected: true, user: 'Marie', premium: true, message: null }
const WITH_PLAYER = { music: { players: [player()], active: 'spotify' } }
const NO_PLAYER = { music: { players: [], active: null } }
const failed = (code: string, msg: string) => ({ ok: false, r: undefined, e: { code, msg } })

test('Spotify : sans compte connecté, la recherche et la bibliothèque disent comment le relier', async ({ page, stack }) => {
  const message = 'Spotify n’est pas connecté : sur le PC, lance « mydock-agent spotify login ».'
  const mock = await mockDock(page, WITH_PLAYER, {
    startPage: 'music',
    onAct: (act) => (act.a === 'spotify.status' ? { r: { ...STATUS_OK, configured: false, connected: false, user: null, premium: null, message } } : undefined),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('music-title')).toHaveText('Morceau') // le lecteur marche sans compte
  await page.getByTestId('sp-tab-search').tap()
  await expect(page.getByTestId('sp-connect')).toBeVisible()
  await expect(page.getByTestId('sp-connect-command')).toHaveText('mydock-agent spotify login')
  await page.getByTestId('sp-tab-library').tap()
  await expect(page.getByTestId('sp-connect')).toBeVisible()
  await noPageScroll(page)
  await shot(page, '09-sans-compte', 'i')
  expect(mock.acts.filter((a) => a.a === 'spotify.search' || a.a === 'spotify.library')).toEqual([]) // rien n'est demandé sans compte
})

test('Spotify : un compte refusé affiche la raison, et « Vérifier à nouveau » repose la question', async ({ page, stack }) => {
  const message = 'Ce compte Spotify n’est pas autorisé pour l’application : ajoute-le dans « User Management ».'
  const mock = await mockDock(page, WITH_PLAYER, {
    startPage: 'music',
    onAct: (act) => (act.a === 'spotify.status' ? { r: { ...STATUS_OK, connected: false, user: null, premium: null, message } } : undefined),
  })
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-search').tap()
  await expect(page.getByTestId('sp-refused-message')).toHaveText(message)
  const before = mock.acts.filter((a) => a.a === 'spotify.status').length
  await page.getByTestId('sp-status-retry').tap()
  await expect.poll(() => mock.acts.filter((a) => a.a === 'spotify.status').length).toBeGreaterThan(before)
  await shot(page, '10-compte-refuse', 'i')
})

test('Spotify : un agent plus ancien que le dock est reconnu ; le lecteur marche quand même', async ({ page, stack }) => {
  await mockDock(page, WITH_PLAYER, {
    startPage: 'music',
    onAct: (act) => (act.a === 'spotify.status' ? failed('unknown_action', 'Action inconnue') : undefined),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('music-title')).toHaveText('Morceau')
  // Un agent d'avant la 1.3.0 ne publie ni aléatoire ni répétition : pas de boutons qui ne feraient rien.
  await expect(page.getByTestId('music-shuffle')).toHaveCount(0)
  await expect(page.getByTestId('music-repeat')).toHaveCount(0)
  await page.getByTestId('sp-tab-search').tap()
  await expect(page.getByTestId('sp-status-error')).toContainText('plus ancien que le dock')
})

test('Spotify : fermé et installé, un bouton le lance ; non installé, la page le dit', async ({ page, stack }) => {
  const mock = await mockDock(page, NO_PLAYER, {
    startPage: 'music',
    onAct: (act) => (act.a === 'spotify.status' ? { r: { ...STATUS_OK, running: false, configured: false, connected: false, message: null } } : undefined),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('music-empty')).toContainText('Spotify n’est pas ouvert')
  await shot(page, '11-spotify-ferme', 'i')
  await page.getByTestId('sp-launch').tap()
  await expect.poll(() => mock.acts.filter((a) => a.a === 'spotify.launch').length).toBe(1)
  expect(mock.acts.find((a) => a.a === 'spotify.launch')!.p).toEqual({})
})

test('Spotify : une application non installée est annoncée, sans bouton qui ne ferait rien', async ({ page, stack }) => {
  await mockDock(page, NO_PLAYER, {
    startPage: 'music',
    onAct: (act) => (act.a === 'spotify.status' ? { r: { ...STATUS_OK, installed: false, running: false, configured: false, connected: false, message: null } } : undefined),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('sp-not-installed')).toContainText('n’est pas installé')
  await expect(page.getByTestId('sp-launch')).toHaveCount(0)
})

test('Spotify : une recherche qui échoue affiche la raison et se retente', async ({ page, stack }) => {
  let calls = 0
  await mockDock(page, WITH_PLAYER, {
    startPage: 'music',
    onAct: (act) => {
      if (act.a === 'spotify.status') return { r: STATUS_OK }
      if (act.a !== 'spotify.search') return undefined
      calls += 1
      return calls === 1
        ? failed('busy', 'Spotify demande de patienter 5 s avant la prochaine requête.')
        : { r: { query: 'mizu', tracks: [spotifyTrack({ name: 'Printemps' })], albums: [], artists: [], playlists: [] } }
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-search').tap()
  await page.getByTestId('sp-search-field').tap()
  await typeOnKeyboard(page, 'mizu')
  await page.getByTestId('keyboard-ok').tap()
  await expect(page.getByTestId('sp-search-error-message')).toContainText('patienter 5 s')
  await shot(page, '12-recherche-echec', 'i')
  await page.getByTestId('sp-search-retry').tap()
  await expect(page.getByTestId('sp-results')).toContainText('Printemps')
  expect(calls).toBe(2)
})

test('Spotify : une lecture refusée par Spotify affiche le message de l’agent', async ({ page, stack }) => {
  const message = 'Cette commande demande un compte Spotify Premium.'
  const mock = await mockDock(page, WITH_PLAYER, {
    startPage: 'music',
    onAct: (act) => {
      if (act.a === 'spotify.status') return { r: STATUS_OK }
      if (act.a === 'spotify.search') return { r: { query: 'a', tracks: [spotifyTrack()], albums: [], artists: [], playlists: [] } }
      if (act.a === 'spotify.play') return failed('premium', message)
      return undefined
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-search').tap()
  await page.getByTestId('sp-search-field').tap()
  await typeOnKeyboard(page, 'a')
  await page.getByTestId('keyboard-ok').tap()
  await page.locator('[data-testid^=sp-track-]').first().tap()
  await expect(page.getByTestId('toast')).toContainText(message)
  // Le morceau est demandé dans son album : l'agent sait alors le placer dans l'album entier.
  expect(mock.acts.find((a) => a.a === 'spotify.play')!.p).toEqual({ uri: `spotify:track:${ID('a')}`, context: `spotify:album:${ID('b')}` })
})

test('Spotify : les pochettes se demandent par identifiant, une fois chacune, jamais par adresse', async ({ page, stack }) => {
  const mock = await mockDock(page, WITH_PLAYER, {
    startPage: 'music',
    onAct: (act) => {
      if (act.a === 'spotify.status') return { r: STATUS_OK }
      if (act.a === 'spotify.search') {
        const tracks = ['0', '1', '2'].map((n) => spotifyTrack({ id: ID(n), uri: `spotify:track:${ID(n)}`, name: `Morceau ${n}`, art: `${n}`.repeat(16) }))
        return { r: { query: 'a', tracks, albums: [], artists: [], playlists: [] } }
      }
      if (act.a === 'spotify.image') return { r: { mime: 'image/png', data: PIXEL } }
      return undefined
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-search').tap()
  await page.getByTestId('sp-search-field').tap()
  await typeOnKeyboard(page, 'a')
  await page.getByTestId('keyboard-ok').tap()
  await expect(page.locator('[data-testid^=sp-track-] img')).toHaveCount(3)
  await expect(page.locator('[data-testid^=sp-track-] img').first()).toHaveAttribute('src', /^data:image\/png;base64,/)
  const asked = mock.acts.filter((a) => a.a === 'spotify.image').map((a) => a.p)
  expect(asked.map((p) => p.art).sort()).toEqual(['0000000000000000', '1111111111111111', '2222222222222222'])
  for (const p of asked) expect(Object.keys(p).sort()).toEqual(['art']) // rien d'autre : pas d'adresse, pas de chemin
  // Revenir sur la même liste ne redemande rien.
  await page.getByTestId('sp-tab-library').tap()
  await page.getByTestId('sp-tab-search').tap()
  await page.waitForTimeout(300)
  expect(mock.acts.filter((a) => a.a === 'spotify.image')).toHaveLength(3)
})

test('Spotify : une page qui ne se charge pas propose de réessayer ; une pochette en échec ne bloque rien', async ({ page, stack }) => {
  let views = 0
  await mockDock(page, WITH_PLAYER, {
    startPage: 'music',
    onAct: (act) => {
      if (act.a === 'spotify.status') return { r: STATUS_OK }
      if (act.a === 'spotify.search') {
        const album = { kind: 'album', id: ID('c'), uri: `spotify:album:${ID('c')}`, name: 'Saisons', artists: 'Mizu', year: '2021', type: 'album', tracks: 1, art: 'cccccccccccccccc' }
        return { r: { query: 'a', tracks: [], albums: [album], artists: [], playlists: [] } }
      }
      if (act.a === 'spotify.image') return failed('art_failed', 'Pochette injoignable.')
      if (act.a !== 'spotify.view') return undefined
      views += 1
      if (views === 1) return failed('not_found', 'Spotify ne trouve pas cet élément.')
      const item = { kind: 'album', id: ID('c'), uri: `spotify:album:${ID('c')}`, name: 'Saisons', artists: 'Mizu', year: '2021', type: 'album', tracks: 1, art: 'cccccccccccccccc' }
      return { r: { kind: 'album', item, tracks: [spotifyTrack({ name: 'Printemps' })] } }
    },
  })
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-search').tap()
  await page.getByTestId('sp-search-field').tap()
  await typeOnKeyboard(page, 'a')
  await page.getByTestId('keyboard-ok').tap()
  await page.locator('[data-testid^=sp-album-]').first().tap()
  await expect(page.getByTestId('sp-detail-error-message')).toHaveText('Spotify ne trouve pas cet élément.')
  await page.getByTestId('sp-detail-retry').tap()
  await expect(page.getByTestId('sp-detail-tracks')).toContainText('Printemps')
  await expect(page.getByTestId('sp-detail-play')).toBeVisible() // sans pochette, la page reste complète
})

test('Spotify : fermé et agent trop ancien, la page explique la mise à jour au lieu d’offrir un bouton inutile', async ({ page, stack }) => {
  await mockDock(page, NO_PLAYER, {
    startPage: 'music',
    onAct: (act) => (act.a === 'spotify.status' ? failed('unknown_action', 'Action inconnue') : undefined),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('sp-agent-old')).toContainText('mets-le à jour')
  await expect(page.getByTestId('sp-launch')).toHaveCount(0)
})

// --- Spotify : la sortie sonore ----------------------------------------------------------------------------------

const DEVICE = (letter: string, extra: Record<string, unknown> = {}) => ({
  id: letter.repeat(40), name: `Appareil ${letter}`, type: 'Speaker', active: false, restricted: false, volume: 30, can_volume: true, this_pc: false, ...extra,
}) // prettier-ignore
const PC_DEVICE = DEVICE('p', { name: 'portable', type: 'Computer', this_pc: true, active: true, volume: 54 })

/** Un faux dock où Spotify est connecté et liste ces appareils. */
async function outputsDock(page: Page, devices: unknown[] | (() => unknown), extra?: (act: Mock['acts'][number]) => unknown) {
  return mockDock(page, WITH_PLAYER, {
    startPage: 'music',
    onAct: (act) => {
      if (act.a === 'spotify.status') return { r: STATUS_OK }
      if (act.a === 'spotify.devices') {
        const value = typeof devices === 'function' ? devices() : devices
        return value instanceof Object && 'ok' in value ? value : { r: { devices: value } }
      }
      return extra?.(act)
    },
  })
}

test('Sortie sonore : une enceinte active ailleurs est dite sur le lecteur, avec son volume', async ({ page, stack }) => {
  const mock = await outputsDock(page, [PC_DEVICE.this_pc ? { ...PC_DEVICE, active: false } : PC_DEVICE, DEVICE('s', { name: 'Salon', active: true, volume: 45 })])
  await page.goto(stack.url)
  await expect(page.getByTestId('music-output')).toContainText('Salon')
  await expect(page.getByTestId('device-vol-value')).toHaveText('45 %')
  await expect(page.getByTestId('music-vol-slider')).toHaveCount(0)
  await page.getByTestId('music-output').tap() // le bouton mène à la liste
  await expect(page.getByTestId('sp-devices-title')).toBeVisible()
  await noPageScroll(page)
  expect(mock.acts.filter((a) => a.a === 'spotify.devices').length).toBeGreaterThanOrEqual(1)
})

test('Sortie sonore : un compte gratuit voit le refus de Spotify, et rien ne change', async ({ page, stack }) => {
  const message = 'Cette commande demande un compte Spotify Premium.'
  const mock = await outputsDock(page, [PC_DEVICE, DEVICE('s', { name: 'Salon' })], (act) =>
    act.a === 'spotify.transfer' ? failed('premium', message) : undefined,
  )
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-devices').tap()
  await page.locator('[data-testid^=sp-device-]', { hasText: 'Salon' }).tap()
  await expect(page.getByTestId('toast')).toContainText(message)
  await expect(page.locator('[data-testid^=sp-device-]', { hasText: 'Salon' })).not.toHaveAttribute('data-active', 'true')
  expect(mock.acts.find((a) => a.a === 'spotify.transfer')!.p).toEqual({ device: 's'.repeat(40), play: true })
  await shot(page, '11-sortie-refus-premium', 'j')
})

test('Sortie sonore : un appareil restreint est grisé, une liste vide ou en échec est expliquée', async ({ page, stack }) => {
  let reply: unknown = [PC_DEVICE, DEVICE('r', { name: 'Enceinte du voisin', restricted: true })]
  await outputsDock(page, () => reply)
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-devices').tap()
  const locked = page.locator('[data-testid^=sp-device-]', { hasText: 'Enceinte du voisin' })
  await expect(locked).toBeDisabled()
  await expect(locked).toContainText('commandes refusées')
  reply = []
  await page.getByTestId('sp-devices-refresh').tap()
  await expect(page.getByTestId('sp-devices-empty')).toContainText('Ouvre Spotify sur le PC')
  reply = failed('busy', 'Spotify demande de patienter 5 s avant la prochaine requête.')
  await page.getByTestId('sp-devices-refresh').tap()
  await expect(page.getByTestId('sp-devices-error-message')).toContainText('patienter 5 s')
  await noPageScroll(page)
})

test('Sortie sonore : un agent d’avant la 1.4.0 ne sait pas lister les appareils : la page demande la mise à jour', async ({ page, stack }) => {
  await outputsDock(page, () => failed('unknown_action', 'Action inconnue'))
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-devices').tap()
  await expect(page.getByTestId('sp-devices-error-message')).toContainText('plus ancien que le dock')
  await expect(page.getByTestId('sp-devices-error-message')).toContainText('mydock-agent 1.4.0')
  await page.getByTestId('sp-tab-player').tap() // le lecteur, lui, marche comme avant
  await expect(page.getByRole('slider', { name: 'Position dans le morceau' })).toBeVisible()
  await expect(page.getByTestId('music-output')).toHaveCount(0)
})

test('Sortie sonore : un appareil disparu pendant qu’on le touche relit la liste', async ({ page, stack }) => {
  let listed = 0
  const mock = await outputsDock(page, () => ((listed += 1), [PC_DEVICE, DEVICE('s', { name: 'Salon' })]), (act) =>
    act.a === 'spotify.transfer' ? failed('unknown_device', 'Cet appareil n’est plus visible : ouvre la liste pour l’actualiser.') : undefined,
  )
  await page.goto(stack.url)
  await page.getByTestId('sp-tab-devices').tap()
  const before = listed
  await page.locator('[data-testid^=sp-device-]', { hasText: 'Salon' }).tap()
  await expect(page.getByTestId('toast')).toContainText('n’est plus visible')
  await expect.poll(() => listed).toBeGreaterThan(before)
  expect(mock.acts.filter((a) => a.a === 'spotify.transfer')).toHaveLength(1)
})

// --- Divertissement : la télécommande et la lecture, avec un faux dock ----------------------------------------------------

const PLAYER_HELPER = { vlc: true, live555: false, ffmpeg: true, touch: true, demo: false }
const playerState = (extra: Record<string, unknown> = {}) => ({
  available: true, state: 'playing', kind: 'file', title: 'Un film', subtitle: 'Disque dur/Films', live: false,
  position: 120, length: 5400, volume: 70, front: 'video', error: null, helper: PLAYER_HELPER, ...extra,
}) // prettier-ignore

test('Divertissement : un toucher pendant la vidéo ramène la télécommande, quelle que soit la page affichée', async ({ page, stack }) => {
  const mock = await mockDock(page, {}, { startPage: 'home' })
  await page.goto(stack.url)
  mock.push('player', playerState({ front: 'video' })) // VLC joue en plein écran : le dock est derrière
  await expect(page.getByTestId('tile-media-sub')).toHaveText('Un film')
  await expect(page.getByTestId('home')).toBeVisible() // rien ne s'ouvre tant que la vidéo est devant

  await openPage(page, 'settings') // le dock était sur une autre page quand on a touché l'écran
  mock.push('player', playerState({ front: 'dock' })) // swayidle a vu le toucher : labwc remet le dock devant
  await expect(page.getByTestId('remote')).toBeVisible()
  await expect(page.getByTestId('page-media')).toBeVisible()
  await expect(page.getByTestId('remote-title')).toHaveText('Un film')
  await expect(page.getByTestId('remote-state')).toContainText('2:00 / 1:30:00')
  await noPageScroll(page)

  await page.getByTestId('remote-video').tap()
  expect(mock.acts.at(-1)).toMatchObject({ a: 'dock.player.front', p: { to: 'video' } })
  await page.getByTestId('remote-toggle').tap()
  expect(mock.acts.at(-1)).toMatchObject({ a: 'dock.player.pause' })
  await page.getByTestId('remote-fwd').tap()
  expect(mock.acts.at(-1)).toMatchObject({ a: 'dock.player.seek', p: { delta: 30 } })
  await page.getByTestId('remote-stop').tap()
  expect(mock.acts.at(-1)).toMatchObject({ a: 'dock.player.stop' })

  mock.push('player', playerState({ state: 'idle', title: null, subtitle: null, position: null, length: null, front: 'dock' }))
  await expect(page.getByTestId('remote')).toHaveCount(0) // la lecture est finie : on retrouve le menu
  await expect(page.getByTestId('media-tv')).toBeVisible()
})

test('Divertissement : une lecture qui échoue est dite dans le menu, et « Compris » l’efface', async ({ page, stack }) => {
  const mock = await mockDock(page, {}, { startPage: 'media' })
  await page.goto(stack.url)
  mock.push('player', playerState({ state: 'error', error: 'Accès refusé au fichier : vérifie l’identifiant du partage.', front: 'dock', title: null, position: null, length: null }))
  await expect(page.getByTestId('media-error')).toContainText('Accès refusé au fichier')
  await expect(page.getByTestId('remote')).toHaveCount(0)
  await page.getByTestId('media-error-ok').tap()
  expect(mock.acts.at(-1)).toMatchObject({ a: 'dock.player.dismiss' })
})

test('Divertissement : sans assistant de lecture, les listes disent ce qui manque au lieu de proposer des boutons inutiles', async ({ page, stack }) => {
  const mock = await mockDock(page, {}, { startPage: 'media' })
  await page.goto(stack.url)
  mock.push('player', { ...playerState({ state: 'idle', front: 'dock' }), available: false, helper: null })
  await page.getByTestId('media-tv').tap()
  await expect(page.getByTestId('tv-blocker')).toContainText('assistant de lecture')
  await expect(page.getByTestId('tv-count')).toContainText('aucune chaîne') // le faux dock ne donne aucune chaîne : pas de plantage
  await page.getByTestId('media-back').tap()
  mock.push('player', { ...playerState({ state: 'idle', front: 'dock' }), helper: { ...PLAYER_HELPER, vlc: false } })
  await page.getByTestId('media-files').tap()
  await expect(page.getByTestId('files-blocker')).toContainText('VLC est absent')
})
