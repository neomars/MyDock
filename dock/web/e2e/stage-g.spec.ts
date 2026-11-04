// Étape g : le Pi sert une page web d'où le PC prend son paquet, puis s'associe avec un code affiché
// sur l'écran du dock. Deux familles de tests :
//   - un faux dock (WebSocket factice) pour les états que la pile ne produit pas facilement : portail
//     en panne, code qui s'épuise, dock plus ancien, jeton refusé… et pour les captures du rapport ;
//   - la vraie pile (Chromium -> dock -> portail -> agent de démonstration) pour le chemin complet :
//     un dock neuf affiche un code, un « PC » le présente au portail, le dock se connecte.

import type { Page, WebSocketRoute } from '@playwright/test'
import { DEMO_TOKEN, expect, openPage, shot, test } from './fixtures'

type Topics = Record<string, unknown>

const PORTAL = {
  enabled: true,
  port: 80,
  urls: ['http://192.168.1.60', 'http://raspberrypi.local'],
  error: null,
  client: { file: 'mydock-agent_1.1.0_all.deb', version: '1.1.0' },
}

const openCode = (extra: Record<string, unknown> = {}) => ({
  open: true,
  code: 'K7QM-2XPD',
  expires_at: Date.now() + 9 * 60_000 + 41_000,
  wrong_left: 5,
  last: null,
  ...extra,
})

const NO_CODE = { open: false, last: null }

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

function link(state: string, detail: string) {
  return {
    state,
    detail,
    since: Date.now(),
    attempt: 0,
    next_retry_at: null,
    resolved: state === 'connected' ? '192.168.1.3' : null,
    agent: state === 'connected' ? { version: '1.1.0', demo: false } : null,
    host: '192.168.1.3',
    port: 8765,
  }
}

const NOT_CONFIGURED = link('not_configured', "Aucun PC n'est associé : installe l'agent depuis la page du dock.")
const CONNECTED = link('connected', 'Connecté.')

function dockInfo(overrides: Record<string, unknown> = {}) {
  return {
    version: '1.1.0',
    proto: 1,
    hostname: 'raspberrypi',
    ips: ['192.168.1.60'],
    pc: { host: '192.168.1.3', port: 8765, token_set: true, name: 'portable' },
    wol: { mac: null, source: null, iface: null, kind: null, interfaces: [] },
    markets: { count: 2, currency: 'EUR', refresh: 15, finnhub: false, demo: false },
    portal: PORTAL,
    pairing: NO_CODE,
    ...overrides,
  }
}

const UNPAIRED = { pc: { host: '', port: 8765, token_set: false, name: null } }

interface Mock {
  acts: { a: string; p: Record<string, unknown> }[]
  /** Remplace l'instantané d'un thème local et l'envoie au navigateur. */
  push(topic: 'link' | 'dock' | 'ui', data: unknown): void
}

/** Un faux dock : `onAct` joue le rôle du serveur (il pousse les nouveaux instantanés). */
async function mockDock(
  page: Page,
  initial: { link: unknown; dock: unknown; startPage?: string; topics?: Topics },
  onAct?: (act: Mock['acts'][number], mock: Mock) => void,
): Promise<Mock> {
  // Le dock repart toujours de l'accueil : pour arriver sur une page (Système par défaut), on touche son bouton une
  // fois la page chargée. `startPage: 'home'` laisse l'accueil.
  const start = initial.startPage ?? 'system'
  if (start !== 'home') {
    const goto = page.goto.bind(page)
    page.goto = (async (url: string, gotoOptions?: Parameters<Page['goto']>[1]) => {
      const response = await goto(url, gotoOptions)
      await openPage(page, start)
      return response
    }) as Page['goto']
  }

  const acts: Mock['acts'] = []
  const state: Record<string, unknown> = {
    link: initial.link,
    dock: initial.dock,
    ui: { last_page: start === 'home' ? 'system' : start, order: [], hidden: [], pinned_apps: [], screen_sleep: 0 },
  }
  const topics: Topics = { host: HOST, ...initial.topics }
  let current: WebSocketRoute | null = null
  const send = (ws: WebSocketRoute, topic: string, data: unknown) =>
    ws.send(JSON.stringify({ t: 'snap', topic, rev: 1, data }))

  const mock: Mock = {
    acts,
    push(topic, data) {
      state[topic] = data
      if (current) send(current, topic, data)
    },
  }

  await page.routeWebSocket(/\/ws$/, (ws) => {
    current = ws
    ws.send(JSON.stringify({ t: 'hello', proto: 1, agent: { role: 'dock', version: '1.1.0' } }))
    for (const topic of ['link', 'dock', 'ui']) send(ws, topic, state[topic])
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw))
      if (message.t === 'sub') {
        for (const topic of message.topics as string[]) if (topics[topic] !== undefined) send(ws, topic, topics[topic])
      } else if (message.t === 'act') {
        const act = { a: message.a as string, p: (message.p ?? {}) as Record<string, unknown> }
        acts.push(act)
        ws.send(JSON.stringify({ t: 'ack', id: message.id, ok: true, r: {} }))
        onAct?.(act, mock)
      } else if (message.t === 'ping') {
        ws.send(JSON.stringify({ t: 'pong', n: message.n, ts: Date.now() }))
      }
    })
  })
  return mock
}

const problems: string[] = []
test.beforeEach(async ({ page }) => {
  problems.length = 0
  page.on('console', (m) => ['error', 'warning'].includes(m.type()) && problems.push(`${m.type()}: ${m.text()}`))
  page.on('pageerror', (e) => problems.push(`exception: ${e.message}`))
})
test.afterEach(() => {
  expect(problems, 'erreurs ou avertissements dans la console du navigateur').toEqual([])
})

// --- l'écran d'accueil d'un dock neuf --------------------------------------------------------------------

test('un dock neuf dit où aller sur le PC et montre le code à y taper', async ({ page, stack }) => {
  await mockDock(page, { link: NOT_CONFIGURED, dock: dockInfo({ ...UNPAIRED, pairing: openCode() }) })
  await page.goto(stack.url)

  await expect(page.getByTestId('pairing-title')).toHaveText('Associer un PC')
  await expect(page.getByTestId('pairing-urls')).toContainText('http://192.168.1.60')
  await expect(page.getByTestId('pairing-urls')).toContainText('http://raspberrypi.local')
  await expect(page.getByTestId('pairing-code')).toHaveText('K7QM-2XPD')
  await expect(page.getByTestId('pairing-timer')).toContainText(/Valable encore 9 min \d\d s/)
  await expect(page.getByTestId('pairing-attempts')).toHaveCount(0) // aucune erreur : rien à signaler
  await expect(page.getByTestId('offline-title')).toHaveCount(0) // le code remplace le diagnostic

  // Un dock neuf ne se « ferme » pas : son code reviendrait aussitôt. La saisie à la main reste possible.
  await expect(page.getByTestId('pairing-close')).toHaveCount(0)
  await expect(page.getByTestId('open-settings')).toContainText('Réglages') // la saisie à la main y reste possible
  await shot(page, '01-association-ecran-accueil', 'g')
})

test('l’écran d’accueil tient dans la page sans défilement, avec des boutons assez grands pour le doigt', async ({
  page,
  stack,
}) => {
  await mockDock(page, { link: NOT_CONFIGURED, dock: dockInfo({ ...UNPAIRED, pairing: openCode() }) })
  await page.goto(stack.url)
  await expect(page.getByTestId('pairing-code')).toBeVisible()

  const main = page.getByTestId('page-system')
  const { scroll, client } = await main.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }))
  expect(scroll).toBeLessThanOrEqual(client + 1)
  for (const id of ['pairing-renew', 'open-settings']) {
    const box = await page.getByTestId(id).boundingBox()
    expect(box!.height, id).toBeGreaterThanOrEqual(48)
  }
})

test('« Nouveau code » en demande un autre au dock, qui remplace l’ancien à l’écran', async ({ page, stack }) => {
  const mock = await mockDock(
    page,
    { link: NOT_CONFIGURED, dock: dockInfo({ ...UNPAIRED, pairing: openCode() }) },
    (act, m) => {
      if (act.a === 'dock.pairing.open') {
        m.push('dock', dockInfo({ ...UNPAIRED, pairing: openCode({ code: 'ABCD-EFGH' }) }))
      }
    },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('pairing-code')).toHaveText('K7QM-2XPD')

  await page.getByTestId('pairing-renew').tap()
  await expect(page.getByTestId('pairing-code')).toHaveText('ABCD-EFGH')
  expect(mock.acts.map((a) => a.a)).toEqual(['dock.pairing.open'])
})

test('les erreurs de code sont comptées à l’écran, et la fin de validité se voit', async ({ page, stack }) => {
  await mockDock(page, {
    link: NOT_CONFIGURED,
    dock: dockInfo({ ...UNPAIRED, pairing: openCode({ wrong_left: 2, expires_at: Date.now() + 40_000 }) }),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('pairing-attempts')).toHaveText('2 essais restants sur ce code')
  await expect(page.getByTestId('pairing-timer')).toHaveClass(/soon/) // moins d'une minute : en orange
  await shot(page, '02-association-code-presque-use', 'g')
})

test('le compte à rebours avance sans rien demander au dock', async ({ page, stack }) => {
  await mockDock(page, {
    link: NOT_CONFIGURED,
    dock: dockInfo({ ...UNPAIRED, pairing: openCode({ expires_at: Date.now() + 70_000 }) }),
  })
  await page.goto(stack.url)
  const timer = page.getByTestId('pairing-timer')
  await expect(timer).toContainText('1 min')
  await expect(timer).toContainText(/\d{2} s/)
  const before = await timer.innerText()
  await expect.poll(async () => timer.innerText(), { timeout: 5_000 }).not.toBe(before)
})

test('si le portail ne tourne pas, l’écran le dit et renvoie vers la saisie à la main', async ({ page, stack }) => {
  await mockDock(page, {
    link: NOT_CONFIGURED,
    dock: dockInfo({
      ...UNPAIRED,
      portal: { ...PORTAL, port: null, urls: [], error: 'Le port 80 est déjà utilisé par un autre programme.' },
    }),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('offline-title')).toHaveText('Aucun PC associé')
  await expect(page.getByTestId('portal-problem')).toContainText('Le port 80 est déjà utilisé')
  await expect(page.getByTestId('pair-open')).toHaveCount(0) // pas de code sans portail pour le recevoir
  await expect(page.getByTestId('pairing-code')).toHaveCount(0)
  await expect(page.getByTestId('open-settings')).toBeVisible()
  await shot(page, '03-association-portail-indisponible', 'g')
})

test('un dock plus ancien (sans portail ni association) garde l’ancien écran, sans erreur', async ({ page, stack }) => {
  const old = dockInfo({ ...UNPAIRED })
  delete (old as Record<string, unknown>).portal
  delete (old as Record<string, unknown>).pairing
  await mockDock(page, { link: NOT_CONFIGURED, dock: old })
  await page.goto(stack.url)
  await expect(page.getByTestId('offline-title')).toHaveText('Aucun PC associé')
  await expect(page.getByTestId('pair-open')).toHaveCount(0)
  await expect(page.getByTestId('portal-problem')).toHaveCount(0)
  await expect(page.getByTestId('open-settings')).toBeVisible()
})

// --- jeton refusé : associer de nouveau --------------------------------------------------------------------

test('jeton refusé : « Associer à nouveau » affiche un code, « Annuler » revient au diagnostic', async ({
  page,
  stack,
}) => {
  const mock = await mockDock(
    page,
    {
      link: link('auth_failed', 'Le PC a refusé le jeton.'),
      dock: dockInfo(),
    },
    (act, m) => {
      if (act.a === 'dock.pairing.open') m.push('dock', dockInfo({ pairing: openCode() }))
      if (act.a === 'dock.pairing.close') m.push('dock', dockInfo({ pairing: { open: false, last: { result: 'closed', at: Date.now() } } }))
    },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('offline-title')).toHaveText('Connexion refusée')

  await page.getByTestId('pair-open').tap()
  await expect(page.getByTestId('pairing-title')).toBeVisible()
  await expect(page.getByTestId('pairing-code')).toHaveText('K7QM-2XPD')
  await shot(page, '04-association-jeton-refuse', 'g')

  await page.getByTestId('pairing-close').tap()
  await expect(page.getByTestId('offline-title')).toHaveText('Connexion refusée')
  expect(mock.acts.map((a) => a.a)).toEqual(['dock.pairing.open', 'dock.pairing.close'])
})

test('un PC éteint ne propose pas de nouvelle association : ce n’est pas le problème', async ({ page, stack }) => {
  await mockDock(page, { link: link('unreachable', 'Le PC ne répond pas.'), dock: dockInfo() })
  await page.goto(stack.url)
  await expect(page.getByTestId('offline-title')).toHaveText('PC hors ligne')
  await expect(page.getByTestId('pair-open')).toHaveCount(0)
})

// --- Réglages -------------------------------------------------------------------------------------------------

test('Réglages : le PC associé est nommé, un code s’ouvre et se ferme depuis la carte', async ({ page, stack }) => {
  const mock = await mockDock(
    page,
    { link: CONNECTED, dock: dockInfo(), startPage: 'settings' },
    (act, m) => {
      if (act.a === 'dock.pairing.open') m.push('dock', dockInfo({ pairing: openCode() }))
      if (act.a === 'dock.pairing.close') m.push('dock', dockInfo({ pairing: { open: false, last: { result: 'closed', at: Date.now() } } }))
    },
  )
  await page.goto(stack.url)
  const card = page.getByTestId('settings-pairing')
  await expect(card).toBeVisible()
  await expect(page.getByTestId('pairing-current')).toContainText('portable')
  await expect(page.getByTestId('pairing-current')).toContainText('192.168.1.3:8765')
  await expect(page.getByTestId('pairing-help')).toContainText('http://192.168.1.60')
  await expect(page.getByTestId('pair-open')).toContainText('Associer un autre PC')

  await page.getByTestId('pair-open').tap()
  await expect(card.getByTestId('pairing-code')).toHaveText('K7QM-2XPD')
  await expect(card.getByTestId('pairing-urls')).toContainText('http://192.168.1.60')
  await shot(page, '05-reglages-association', 'g')

  await card.getByTestId('pairing-close').tap()
  await expect(card.getByTestId('pairing-code')).toHaveCount(0)
  await expect(page.getByTestId('pair-open')).toBeVisible()
  expect(mock.acts.map((a) => a.a)).toEqual(['dock.pairing.open', 'dock.pairing.close'])
})

test('Réglages : un code expiré ou annulé est expliqué', async ({ page, stack }) => {
  const mock = await mockDock(page, {
    link: CONNECTED,
    dock: dockInfo({ pairing: { open: false, last: { result: 'expired', at: Date.now() } } }),
    startPage: 'settings',
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('pairing-notice')).toContainText('Le code a expiré')
  mock.push('dock', dockInfo({ pairing: { open: false, last: { result: 'locked', at: Date.now() } } }))
  await expect(page.getByTestId('pairing-notice')).toContainText('Trop de codes incorrects')
  mock.push('dock', dockInfo({ pairing: { open: false, last: { result: 'closed', at: Date.now() } } }))
  await expect(page.getByTestId('pairing-notice')).toHaveCount(0) // une fermeture voulue n'a rien à expliquer
})

test('Réglages : le portail indisponible grise le bouton et dit pourquoi', async ({ page, stack }) => {
  await mockDock(page, {
    link: CONNECTED,
    dock: dockInfo({ portal: { ...PORTAL, port: null, urls: [], error: 'Droit refusé sur le port 80.' } }),
    startPage: 'settings',
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('pair-open')).toBeDisabled()
  await expect(page.getByTestId('settings-pairing').getByTestId('portal-problem')).toContainText('Droit refusé')
  await expect(page.getByTestId('portal-urls')).toHaveText('indisponible')
})

test('Réglages : « À propos » donne la page d’installation et le paquet proposé', async ({ page, stack }) => {
  await mockDock(page, { link: CONNECTED, dock: dockInfo(), startPage: 'settings' })
  await page.goto(stack.url)
  await expect(page.getByTestId('portal-urls')).toContainText('http://192.168.1.60')
  await expect(page.getByTestId('portal-urls')).toContainText('http://raspberrypi.local')
  await expect(page.getByTestId('portal-client')).toHaveText('mydock-agent 1.1.0')
})

// --- l'annonce d'une association -------------------------------------------------------------------------------

test('quand un PC s’associe, le dock le dit en passant, où qu’on soit', async ({ page, stack }) => {
  const mock = await mockDock(page, {
    link: NOT_CONFIGURED,
    dock: dockInfo({ ...UNPAIRED, pairing: openCode() }),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('pairing-code')).toBeVisible()

  mock.push('link', CONNECTED)
  mock.push(
    'dock',
    dockInfo({ pairing: { open: false, last: { result: 'paired', name: 'portable', host: '192.168.1.3', at: Date.now() } } }),
  )
  await expect(page.getByTestId('toast')).toContainText('PC « portable » associé')
  await expect(page.getByTestId('pairing-screen')).toHaveCount(0)
})

test('une association ancienne n’est pas annoncée de nouveau quand la page se recharge', async ({ page, stack }) => {
  await mockDock(page, {
    link: CONNECTED,
    dock: dockInfo({
      pairing: { open: false, last: { result: 'paired', name: 'portable', host: '192.168.1.3', at: Date.now() - 5 * 60_000 } },
    }),
    startPage: 'settings',
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('pairing-current')).toContainText('portable') // le dock est arrivé
  await expect(page.getByTestId('toast')).toHaveCount(0)
})

// --- la vraie pile : dock neuf, portail, agent de démonstration ---------------------------------------

async function present(
  stack: { portal: string; agentPort: number },
  code: string,
  extra: Record<string, unknown> = {},
): Promise<{ status: number; body: Record<string, unknown> }> {
  const response = await fetch(`${stack.portal}/api/pair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, token: DEMO_TOKEN, port: stack.agentPort, name: 'pc-demo', ...extra }),
  })
  return { status: response.status, body: (await response.json()) as Record<string, unknown> }
}

test.describe('dock neuf (vraie pile)', () => {
  test.use({ unpaired: true })

  test('un code s’affiche, le PC le présente au portail et le dock se connecte tout seul', async ({ page, stack }) => {
    await page.goto(stack.url)
    // L'accueil montre déjà l'adresse et le code ; une page du PC en donne le détail (adresses, essais restants).
    await expect(page.getByTestId('banner-code')).toHaveText(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/)
    await expect(page.getByTestId('banner-url')).toContainText('http://')
    await openPage(page, 'system')
    await expect(page.getByTestId('pairing-code')).toHaveText(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/)
    await expect(page.getByTestId('pairing-urls')).toContainText('http://')
    const code = (await page.getByTestId('pairing-code').innerText()).trim()

    // Un code faux d'abord : refusé, compté à l'écran, et le vrai marche encore ensuite.
    const wrong = await present(stack, code === 'AAAA-AAAA' ? 'BBBB-BBBB' : 'AAAA-AAAA')
    expect(wrong.status).toBe(403)
    expect(wrong.body.error).toBe('bad_code')
    await expect(page.getByTestId('pairing-attempts')).toHaveText('4 essais restants sur ce code')

    const good = await present(stack, code.toLowerCase().replace('-', ' ')) // casse et séparateur libres
    expect(good.status).toBe(200)
    expect(good.body.ok).toBe(true)
    expect(JSON.stringify(good.body)).not.toContain(DEMO_TOKEN) // le jeton n'est jamais renvoyé

    await expect(page.getByTestId('toast')).toContainText('PC « pc-demo » associé')
    await expect(page.getByTestId('system-host')).toBeVisible({ timeout: 20_000 }) // les pages du PC arrivent
    await expect(page.getByTestId('pairing-screen')).toHaveCount(0)
    await expect(page.getByTestId('offline')).toHaveCount(0)

    // Le même code ne sert qu'une fois.
    const again = await present(stack, code)
    expect(again.status).toBeGreaterThanOrEqual(400)

    // Et la configuration est retenue : Réglages montre le PC associé.
    await openPage(page, 'settings')
    await expect(page.getByTestId('pairing-current')).toContainText('pc-demo')
    await expect(page.getByTestId('pc-host')).toHaveText('127.0.0.1')
  })

  test('cinq codes faux annulent le code, et un nouveau est affiché sans rien faire', async ({ page, stack }) => {
    await page.goto(stack.url)
    await openPage(page, 'system') // le détail du code est sur une page du PC (l'accueil n'en montre que l'essentiel)
    await expect(page.getByTestId('pairing-code')).toBeVisible()
    const first = (await page.getByTestId('pairing-code').innerText()).trim()
    const wrongCode = first === 'AAAA-AAAA' ? 'BBBB-BBBB' : 'AAAA-AAAA'

    let last: Awaited<ReturnType<typeof present>> | undefined
    for (let i = 0; i < 5; i++) last = await present(stack, wrongCode)
    expect(last!.status).toBeGreaterThanOrEqual(400)

    // Le code d'avant ne vaut plus rien, même présenté exactement.
    const refused = await present(stack, first)
    expect(refused.status).toBeGreaterThanOrEqual(400)
    // Un dock neuf ne reste pas sans code : un nouveau est affiché dans la seconde.
    await expect(page.getByTestId('pairing-code')).not.toHaveText(first, { timeout: 10_000 })
    await expect(page.getByTestId('pairing-code')).toHaveText(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/)
  })

  test('le code ne figure ni sur la page du portail ni dans son état public', async ({ page, stack }) => {
    await page.goto(stack.url)
    await openPage(page, 'system') // le détail du code est sur une page du PC (l'accueil n'en montre que l'essentiel)
    await expect(page.getByTestId('pairing-code')).toBeVisible()
    const code = (await page.getByTestId('pairing-code').innerText()).trim()
    const compact = code.replace('-', '')
    for (const path of ['/', '/api/portal']) {
      const text = await (await fetch(`${stack.portal}${path}`)).text()
      expect(text, path).not.toContain(code)
      expect(text, path).not.toContain(compact)
    }
  })

  test('le portail d’un dock neuf renvoie vers le code affiché sur son écran', async ({ browser, stack }) => {
    const context = await browser.newContext({ viewport: { width: 1100, height: 900 }, colorScheme: 'dark', locale: 'fr-FR' })
    const page = await context.newPage()
    await page.goto(stack.portal)
    await expect(page.getByText('Association en cours : saisis le code affiché sur l’écran du dock')).toBeVisible()
    await expect(page.getByRole('link', { name: /Télécharger mydock-agent_.*_all\.deb/ })).toBeVisible()
    await shot(page, '07-portail-dock-neuf', 'g')
    await context.close()
  })
})

test('Réglages (vraie pile) : associer un autre PC ouvre un code, le présenter l’associe', async ({ dock, stack }) => {
  await openPage(dock, 'settings')
  await expect(dock.getByTestId('pairing-current')).toContainText('pc-demo')
  await dock.getByTestId('pair-open').tap()
  const card = dock.getByTestId('settings-pairing')
  await expect(card.getByTestId('pairing-code')).toHaveText(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/)
  const code = (await card.getByTestId('pairing-code').innerText()).trim()

  const result = await present(stack, code, { name: 'autre-pc' })
  expect(result.status).toBe(200)
  await expect(dock.getByTestId('toast')).toContainText('PC « autre-pc » associé')
  await expect(card.getByTestId('pairing-code')).toHaveCount(0) // la session est terminée
  // Le nom affiché est celui que l'agent donne de lui-même une fois connecté (ici « pc-demo »).
  await expect(dock.getByTestId('pairing-current')).toContainText('pc-demo')
})

test('le portail vu depuis le PC : la page propose le paquet et les deux commandes', async ({ browser, stack }) => {
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 }, colorScheme: 'dark', locale: 'fr-FR' })
  const page = await context.newPage()
  const seen: string[] = []
  page.on('console', (m) => ['error', 'warning'].includes(m.type()) && seen.push(m.text()))
  page.on('pageerror', (e) => seen.push(e.message))

  await page.goto(stack.portal)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  const download = page.getByRole('link', { name: /Télécharger mydock-agent_.*_all\.deb/ })
  await expect(download).toHaveAttribute('href', /^\/clients\/mydock-agent_[\d.]+_all\.deb$/)
  await expect(page.locator('#c1')).toContainText(/^sudo apt install \.\/mydock-agent_[\d.]+_all\.deb$/)
  await expect(page.locator('#c2')).toContainText(/^mydock-agent setup --pi \S+( --portal-port \d+)? --open-firewall$/)
  await shot(page, '06-portail-sur-le-pc', 'g')

  // Le lien télécharge bien le paquet servi par le dock.
  const [file] = await Promise.all([page.waitForEvent('download'), download.click()])
  expect(file.suggestedFilename()).toMatch(/^mydock-agent_[\d.]+_all\.deb$/)

  expect(seen, 'la page respecte sa propre politique de sécurité (CSP)').toEqual([])
  await context.close()
})
