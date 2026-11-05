// Étape h : l'écran d'accueil en gros boutons (comme les touches d'un Stream Deck), le retour par un balayage de
// gauche à droite, et le texte doublé pour un écran de 7". Vrai Chromium 1024×600 tactile -> vrai dock -> vrai agent
// de démonstration ; quelques états que la pile ne produit pas (jeton refusé…) passent par un faux dock.

import type { Page, WebSocketRoute } from '@playwright/test'
import { clearKeyboard, expect, goHome, openPage, shot, swipe, test, tileOrder, typeOnKeyboard } from './fixtures'

const PAGES: [id: string, label: string, shotName: string][] = [
  ['music', 'Musique', '04-musique'],
  ['sound', 'Son', '05-son'],
  ['apps', 'Applis', '06-applis'],
  ['steam', 'Steam', '07-steam'],
  ['office', 'Office', '08-office'],
  ['markets', 'Bourse', '09-bourse'],
  ['system', 'Système', '10-systeme'],
  ['power', 'Alimentation', '11-alimentation'],
  ['settings', 'Réglages', '12-reglages'],
]

const DEFAULT_ORDER = PAGES.map(([id]) => id)

type Box = { x: number; y: number; width: number; height: number }

/** Le cadre d'un élément une fois immobile : l'accueil et les pages glissent en entrant (170 à 190 ms). */
async function boxOf(page: Page, testId: string): Promise<Box> {
  let previous = ''
  for (let i = 0; i < 25; i++) {
    const box = await page.getByTestId(testId).boundingBox()
    if (!box) throw new Error(`${testId} est introuvable à l'écran`)
    const key = JSON.stringify(box)
    if (key === previous) return box
    previous = key
    await page.waitForTimeout(60)
  }
  throw new Error(`${testId} bouge encore`)
}

/** Le cadre est entièrement dans l'écran de 1024×600. */
function expectOnScreen(box: Box, what: string): void {
  expect(box.x, `${what} : bord gauche`).toBeGreaterThanOrEqual(-0.5)
  expect(box.y, `${what} : bord haut`).toBeGreaterThanOrEqual(-0.5)
  expect(box.x + box.width, `${what} : bord droit`).toBeLessThanOrEqual(1024.5)
  expect(box.y + box.height, `${what} : bord bas`).toBeLessThanOrEqual(600.5)
}

/** Éléments d'une page qui dépassent à droite de l'écran (le texte doublé ne doit rien couper). */
async function overflowing(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bad: string[] = []
    for (const el of document.querySelectorAll('main *')) {
      const box = el.getBoundingClientRect()
      if (box.width > 0 && box.right > window.innerWidth + 1) {
        bad.push(`${el.tagName.toLowerCase()}[${el.getAttribute('data-testid') ?? String((el as HTMLElement).className).slice(0, 24)}] → ${Math.round(box.right)} px`)
      }
    }
    return bad.slice(0, 5)
  })
}

const inPage = (page: Page) => page.locator('main.page')

/**
 * Un point de la page, à l'abscisse `x`, qui n'est sur aucune commande : un curseur de volume garde son geste (et sa
 * zone de toucher dépasse sa piste), un balayage de navigation part donc d'ailleurs.
 */
async function freeSpot(page: Page, x: number): Promise<{ x: number; y: number }> {
  const y = await page.evaluate((x) => {
    for (let y = 130; y < 590; y += 8) {
      const el = document.elementFromPoint(x, y)
      if (el?.closest('main') && !el.closest('[role=slider], button, input, textarea, select, [data-noswipe]')) return y
    }
    return null
  }, x)
  if (y === null) throw new Error(`aucun point libre à x=${x}`)
  return { x, y }
}

// --- l'accueil ----------------------------------------------------------------------------------------------

test('Accueil : neuf gros boutons dans l’ordre, en grille 3×3 qui remplit l’écran', async ({ dock }) => {
  expect(await tileOrder(dock)).toEqual(DEFAULT_ORDER)
  const grid = dock.getByTestId('home-grid')
  await expect(grid).toHaveAttribute('data-cols', '3')
  await expect(grid).toHaveAttribute('data-rows', '3')

  for (const [id, label] of PAGES) {
    const tile = dock.getByTestId(`tile-${id}`)
    await expect(tile).toContainText(label)
    const box = await boxOf(dock, `tile-${id}`)
    expectOnScreen(box, `bouton ${label}`)
    expect(box.width, `largeur du bouton ${label}`).toBeGreaterThanOrEqual(300)
    expect(box.height, `hauteur du bouton ${label}`).toBeGreaterThanOrEqual(110)
  }
  const size = await dock.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
  expect(size).toEqual({ w: 1024, h: 600 })
  await expect(dock.getByTestId('home-banner')).toHaveCount(0) // un PC qui va bien : rien que les boutons
  await shot(dock, '01-accueil', 'h')
})

test('Accueil : le texte est doublé (courant 40 px, libellés des boutons 36 px, barre du haut 88 px)', async ({ dock }) => {
  const sizes = await dock.evaluate(() => ({
    scale: getComputedStyle(document.documentElement).getPropertyValue('--ts').trim(),
    body: getComputedStyle(document.body).fontSize,
    label: getComputedStyle(document.querySelector('[data-testid=tile-music] .label')!).fontSize,
    topbar: document.querySelector('[data-testid=pc-label]')!.closest('header')?.getBoundingClientRect().height,
  }))
  expect(sizes.scale).toBe('2')
  expect(sizes.body).toBe('40px')
  expect(sizes.label).toBe('36px')
  expect(sizes.topbar).toBe(88)
})

test('Accueil : le bouton Musique montre le morceau en cours', async ({ dock }) => {
  await expect(dock.getByTestId('tile-music-sub')).toHaveText('Nuit blanche')
  await dock.getByTestId('mini-next').tap()
  await expect(dock.getByTestId('tile-music-sub')).toHaveText('Sous la pluie de Kyoto')
  await dock.getByTestId('mini-prev').tap()
  await expect(dock.getByTestId('tile-music-sub')).toHaveText('Nuit blanche')
})

test('Accueil : le bouton de la barre du haut n’apparaît que sur une page, et y ramène', async ({ dock }) => {
  await expect(dock.getByTestId('home-back')).toHaveCount(0)
  await openPage(dock, 'power')
  const back = await boxOf(dock, 'home-back')
  expect(Math.min(back.width, back.height)).toBeGreaterThanOrEqual(72) // une vraie cible tactile
  await goHome(dock)
  await expect(dock.getByTestId('home-back')).toHaveCount(0)
})

// --- le balayage de gauche à droite ----------------------------------------------------------------------------

test('Balayage : de gauche à droite, chaque page revient à l’accueil', async ({ dock }) => {
  for (const [id] of PAGES) {
    await openPage(dock, id)
    await dock.waitForTimeout(450) // la page a fini de glisser, ses données sont arrivées
    const spot = await freeSpot(dock, 10) // le bord gauche de la page, hors de toute commande
    await swipe(dock, spot, { x: spot.x + 630, y: spot.y + 8 })
    await expect(dock.getByTestId('home'), `depuis la page ${id}`).toBeVisible()
    await expect(inPage(dock)).toHaveCount(0)
  }
})

test('Balayage : il part de n’importe où, même au milieu d’une page', async ({ dock }) => {
  await openPage(dock, 'system')
  await expect(dock.getByTestId('system-host')).toBeVisible()
  await dock.waitForTimeout(450)
  const spot = await freeSpot(dock, 300) // au milieu de la page, sur un texte ou une carte : pas sur une commande
  await swipe(dock, spot, { x: spot.x + 450, y: spot.y + 12 })
  await expect(dock.getByTestId('home')).toBeVisible()
})

test('Balayage : trop court, vers la gauche ou en défilement vertical, il ne quitte pas la page', async ({ dock }) => {
  await openPage(dock, 'system')
  await dock.waitForTimeout(250)
  await swipe(dock, { x: 300, y: 300 }, { x: 380, y: 300 }) // 80 px : un geste hésitant
  await swipe(dock, { x: 700, y: 300 }, { x: 150, y: 310 }) // de droite à gauche
  await swipe(dock, { x: 400, y: 520 }, { x: 560, y: 120 }) // surtout vertical : c'est un défilement
  await dock.waitForTimeout(200)
  await expect(dock.getByTestId('page-system')).toBeVisible()
  await expect(dock.getByTestId('home')).toHaveCount(0)
})

test('Balayage : un curseur de volume garde son geste, la page ne se ferme pas', async ({ dock }) => {
  await openPage(dock, 'sound')
  await expect(dock.getByTestId('out-slider')).toBeVisible()
  const slider = await boxOf(dock, 'out-slider')
  const y = slider.y + slider.height / 2
  await swipe(dock, { x: slider.x + 40, y }, { x: slider.x + 460, y })
  await dock.waitForTimeout(200)
  await expect(dock.getByTestId('page-sound')).toBeVisible()
  await expect(dock.getByTestId('home')).toHaveCount(0)
})

test('Balayage : le premier appui qui suit un balayage rapide ouvre bien le bouton touché', async ({ dock }) => {
  await openPage(dock, 'power')
  await dock.waitForTimeout(450)
  const spot = await freeSpot(dock, 10)
  await swipe(dock, spot, { x: spot.x + 630, y: spot.y + 8 }) // ~4 000 px/s : Chromium croit à une inertie à arrêter
  await expect(dock.getByTestId('home')).toBeVisible()
  await dock.getByTestId('tile-sound').tap() // tout de suite : le navigateur en « mange » le clic, l'interface le rattrape
  await expect(dock.getByTestId('page-sound')).toBeVisible()
})

test('Balayage : sur l’accueil il ne fait rien', async ({ dock }) => {
  await swipe(dock, { x: 20, y: 300 }, { x: 700, y: 300 })
  await dock.waitForTimeout(250)
  await expect(dock.getByTestId('home')).toBeVisible()
  await expect(inPage(dock)).toHaveCount(0)
})

test('Balayage : le toucher qui suit un balayage ne déclenche pas la commande sous le doigt', async ({ dock }) => {
  await openPage(dock, 'power')
  await dock.waitForTimeout(250)
  // Le balayage finit sur la page d'accueil, juste au-dessus d'un bouton : il ne doit pas l'ouvrir.
  await swipe(dock, { x: 10, y: 330 }, { x: 640, y: 330 })
  await expect(dock.getByTestId('home')).toBeVisible()
  await dock.waitForTimeout(150)
  await expect(inPage(dock)).toHaveCount(0)
})

// --- le PC éteint, vu depuis l'accueil -------------------------------------------------------------------------------

test('Accueil : PC éteint, le bandeau le dit, propose de l’allumer et grise les pages du PC', async ({ dock, stack }) => {
  stack.pcOff()
  const banner = dock.getByTestId('home-banner')
  await expect(banner).toHaveAttribute('data-kind', 'unreachable', { timeout: 20_000 })
  await expect(dock.getByTestId('banner-title')).toHaveText('PC hors ligne')
  await expect(dock.getByTestId('tile-system')).toHaveAttribute('data-dim', 'true')
  await expect(dock.getByTestId('tile-music')).toHaveAttribute('data-dim', 'true')
  await expect(dock.getByTestId('tile-markets')).not.toHaveAttribute('data-dim', 'true') // la Bourse ne vient pas du PC
  await expect(dock.getByTestId('tile-settings')).not.toHaveAttribute('data-dim', 'true')
  expectOnScreen(await boxOf(dock, 'home-banner'), 'bandeau')
  expectOnScreen(await boxOf(dock, 'tile-settings'), 'dernier bouton')
  await shot(dock, '02-accueil-pc-eteint', 'h')

  await dock.getByTestId('banner-wake').tap()
  await expect(dock.getByTestId('toast')).toContainText('Paquet de réveil envoyé')
  await expect(dock.getByTestId('banner-waking')).toBeVisible()
  await expect.poll(() => stack.wolPackets.length).toBe(1)

  // Un bouton grisé reste touchable : la page explique pourquoi elle est vide.
  await dock.getByTestId('tile-system').tap()
  await expect(dock.getByTestId('offline')).toBeVisible()
  expectOnScreen(await boxOf(dock, 'offline-title'), 'titre de l’écran hors ligne')
  expectOnScreen(await boxOf(dock, 'wake'), 'bouton « Allumer le PC » (atteignable sans défiler)')
  await shot(dock, '14-ecran-hors-ligne', 'h')
  await goHome(dock)

  stack.pcOn()
  await expect(dock.getByTestId('home-banner')).toHaveCount(0, { timeout: 40_000 })
  await expect(dock.getByTestId('tile-system')).not.toHaveAttribute('data-dim', 'true')
})

test('Accueil : agent arrêté seul, le bandeau le dit sans proposer de réveil', async ({ dock, stack }) => {
  stack.agentDown()
  await expect(dock.getByTestId('home-banner')).toHaveAttribute('data-kind', 'host_up_agent_down', { timeout: 20_000 })
  await expect(dock.getByTestId('banner-title')).toHaveText('PC allumé, agent arrêté')
  await expect(dock.getByTestId('banner-wake')).toHaveCount(0)
  stack.pcOn()
  await expect(dock.getByTestId('home-banner')).toHaveCount(0, { timeout: 30_000 })
})

test.describe('dock neuf', () => {
  test.use({ unpaired: true })

  test('Accueil : le code d’association et l’adresse à ouvrir sont là, boutons du PC grisés', async ({ dock }) => {
    const banner = dock.getByTestId('home-banner')
    await expect(banner).toHaveAttribute('data-kind', 'pairing')
    await expect(dock.getByTestId('banner-code')).toHaveText(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/)
    await expect(dock.getByTestId('banner-url')).toContainText('http://')
    await expect(dock.getByTestId('banner-close')).toHaveCount(0) // sans PC associé on ne ferme pas la porte
    await expect(dock.getByTestId('tile-system')).toHaveAttribute('data-dim', 'true')
    expectOnScreen(await boxOf(dock, 'home-banner'), 'bandeau')
    const code = await boxOf(dock, 'banner-code')
    expect(code.height, 'le code tient sur une seule ligne').toBeLessThan(80)
    await shot(dock, '03-accueil-association', 'h')
  })
})

// --- le texte doublé : chaque page tient dans l'écran ----------------------------------------------------------------

test('Texte doublé : chaque page tient en largeur, sans défilement de l’écran, et se laisse photographier', async ({ dock }) => {
  for (const [id, label, shotName] of PAGES) {
    await openPage(dock, id)
    await dock.waitForTimeout(700) // pochettes et mesures arrivent
    expect(await overflowing(dock), `${label} : éléments coupés à droite`).toEqual([])
    const widths = await inPage(dock).evaluate((main) => ({ scroll: main.scrollWidth, client: main.clientWidth }))
    expect(widths.scroll, `${label} : défilement horizontal`).toBeLessThanOrEqual(widths.client + 1)
    const size = await dock.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }))
    expect(size, `${label} : défilement de l’écran`).toEqual({ w: 1024, h: 600 })
    await shot(dock, shotName, 'h')
  }
})

test('Texte doublé : clavier, confirmation et fenêtres tiennent dans l’écran, boutons de sortie compris', async ({ dock }) => {
  // Clavier tactile
  await openPage(dock, 'settings')
  await dock.getByTestId('edit-host').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  for (const id of ['keyboard', 'keyboard-value', 'keyboard-ok', 'keyboard-cancel']) expectOnScreen(await boxOf(dock, id), id)
  await shot(dock, '20-clavier', 'h')
  await dock.getByTestId('keyboard-cancel').tap()

  // Confirmation d'une action sensible : les deux boutons doivent rester atteignables.
  await openPage(dock, 'power')
  await expect(dock.getByTestId('power-reboot')).toBeEnabled()
  await dock.getByTestId('power-reboot').tap()
  await expect(dock.getByTestId('confirm')).toBeVisible()
  for (const id of ['confirm', 'confirm-ok', 'confirm-cancel']) expectOnScreen(await boxOf(dock, id), id)
  await shot(dock, '21-confirmation', 'h')
  await dock.getByTestId('confirm-cancel').tap()

  // Pages du dock et leur ordre
  await openPage(dock, 'settings')
  await dock.getByTestId('choose-pages').tap()
  await expect(dock.getByTestId('pages-editor')).toBeVisible()
  for (const id of ['pages-editor', 'pages-done', 'pages-reset']) expectOnScreen(await boxOf(dock, id), id)
  await shot(dock, '22-pages-et-ordre', 'h')
  await dock.getByTestId('pages-done').tap()

  // Applications du lanceur : deux volets
  await openPage(dock, 'apps')
  await dock.getByTestId('apps-edit').or(dock.getByTestId('apps-choose')).tap()
  await expect(dock.getByTestId('picker')).toBeVisible()
  for (const id of ['picker', 'picker-done', 'picker-tab-all', 'picker-tab-chosen']) expectOnScreen(await boxOf(dock, id), id)
  await expect(dock.getByTestId('picker-all')).toBeVisible()
  await shot(dock, '23-applications', 'h')
  await dock.getByTestId('picker-tab-chosen').tap()
  await expect(dock.getByTestId('picker-chosen')).toBeVisible()
  await expect(dock.getByTestId('picker-all')).toHaveCount(0)
  await dock.getByTestId('picker-done').tap()

  // Valeurs de la Bourse : deux volets
  await openPage(dock, 'markets')
  await dock.getByTestId('markets-edit').tap()
  await expect(dock.getByTestId('mk-editor')).toBeVisible()
  for (const id of ['mk-editor', 'mk-done', 'mk-tab-add', 'mk-tab-list']) expectOnScreen(await boxOf(dock, id), id)
  await expect(dock.getByTestId('mk-add')).toBeVisible()
  await shot(dock, '24-bourse-ajouter', 'h')
  await dock.getByTestId('mk-tab-list').tap()
  await expect(dock.getByTestId('mk-chosen')).toBeVisible()
  await shot(dock, '25-bourse-liste', 'h')
  await dock.getByTestId('mk-done').tap()
})

test('Clavier : un texte plus long que le champ montre toujours sa fin, curseur compris', async ({ dock }) => {
  await openPage(dock, 'settings')
  await dock.getByTestId('edit-host').tap()
  await expect(dock.getByTestId('keyboard')).toBeVisible()
  await clearKeyboard(dock)
  const long = 'abcdefghijklmnopqrstuvwxyz.abcdefghijklmnopqrstuvwxyz'
  await typeOnKeyboard(dock, long)
  await expect(dock.getByTestId('keyboard-value')).toHaveText(long)
  const field = await boxOf(dock, 'keyboard-value')
  const caret = (await dock.locator('[data-testid=keyboard-value] .caret').boundingBox())!
  expect(caret.x, 'le curseur reste dans le champ (bord gauche)').toBeGreaterThanOrEqual(field.x)
  expect(caret.x + caret.width, 'le curseur reste dans le champ (bord droit)').toBeLessThanOrEqual(field.x + field.width + 1)
  await dock.getByTestId('keyboard-cancel').tap()
})

// --- l'accueil vu avec un faux dock (états que la pile ne produit pas) ----------------------------------------------

const HOST = {
  hostname: 'portable',
  os: 'Ubuntu 26.04 LTS',
  kernel: '7.0.0-38-generic',
  arch: 'x86_64',
  python: '3.14.4',
  agent: { version: '1.2.0', proto: 1, demo: false },
  boot_time: Math.floor(Date.now() / 1000) - 3 * 3600,
  session: { type: 'wayland', desktop: 'GNOME' },
  interfaces: [],
}

const PORTAL = {
  enabled: true,
  port: 80,
  urls: ['http://192.168.1.60', 'http://raspberrypi.local'],
  error: null,
  client: { file: 'mydock-agent_1.2.0_all.deb', version: '1.2.0' },
}

const link = (state: string, detail: string) => ({
  state,
  detail,
  since: Date.now(),
  attempt: 0,
  next_retry_at: null,
  resolved: state === 'connected' ? '192.168.1.3' : null,
  agent: state === 'connected' ? { version: '1.2.0', demo: false } : null,
  host: '192.168.1.3',
  port: 8765,
})

const dockInfo = (extra: Record<string, unknown> = {}) => ({
  version: '1.2.0',
  proto: 1,
  hostname: 'raspberrypi',
  ips: ['192.168.1.60'],
  pc: { host: '192.168.1.3', port: 8765, token_set: true, name: 'portable' },
  wol: { mac: null, source: null, iface: null, kind: null, interfaces: [] },
  markets: { count: 2, currency: 'EUR', refresh: 15, finnhub: false, demo: false },
  portal: PORTAL,
  pairing: { open: false, last: null },
  ...extra,
})

const openCode = () => ({ open: true, code: 'K7QM-2XPD', expires_at: Date.now() + 9 * 60_000 + 41_000, wrong_left: 5, last: null })

type Push = (topic: 'link' | 'dock' | 'ui', data: unknown) => void

/** Un faux dock sur l'accueil : `onAct` joue le serveur (il pousse les nouveaux instantanés). */
async function mockDock(
  page: Page,
  initial: { link: unknown; dock: unknown },
  onAct?: (act: { a: string }, push: Push) => void,
): Promise<{ acts: { a: string }[]; push: Push }> {
  const acts: { a: string }[] = []
  const state: Record<string, unknown> = {
    link: initial.link,
    dock: initial.dock,
    ui: { last_page: 'system', order: [], hidden: [], pinned_apps: [], screen_sleep: 0 },
  }
  let current: WebSocketRoute | null = null
  const send = (ws: WebSocketRoute, topic: string, data: unknown) => ws.send(JSON.stringify({ t: 'snap', topic, rev: 1, data }))
  const push: Push = (topic, data) => {
    state[topic] = data
    if (current) send(current, topic, data)
  }
  await page.routeWebSocket(/\/ws$/, (ws) => {
    current = ws
    ws.send(JSON.stringify({ t: 'hello', proto: 1, agent: { role: 'dock', version: '1.2.0' } }))
    for (const topic of ['link', 'dock', 'ui']) send(ws, topic, state[topic])
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw))
      if (message.t === 'sub' && (message.topics as string[]).includes('host')) send(ws, 'host', HOST)
      else if (message.t === 'act') {
        const act = { a: message.a as string }
        acts.push(act)
        ws.send(JSON.stringify({ t: 'ack', id: message.id, ok: true, r: {} }))
        onAct?.(act, push)
      } else if (message.t === 'ping') ws.send(JSON.stringify({ t: 'pong', n: message.n, ts: Date.now() }))
    })
  })
  return { acts, push }
}

test('Accueil (faux dock) : un PC connecté n’a pas de bandeau, aucun bouton n’est grisé', async ({ page, stack }) => {
  await mockDock(page, { link: link('connected', 'Connecté.'), dock: dockInfo() })
  await page.goto(stack.url)
  await expect(page.getByTestId('home')).toBeVisible()
  await expect(page.getByTestId('home-banner')).toHaveCount(0)
  for (const [id] of PAGES) await expect(page.getByTestId(`tile-${id}`)).not.toHaveAttribute('data-dim', 'true')
})

test('Accueil (faux dock) : jeton refusé, « Associer à nouveau » affiche un code, qu’on peut refermer', async ({ page, stack }) => {
  const { acts } = await mockDock(
    page,
    { link: link('auth_failed', 'Le PC refuse le jeton : associe-le de nouveau.'), dock: dockInfo() },
    (act, push) => {
      if (act.a === 'dock.pairing.open') push('dock', dockInfo({ pairing: openCode() }))
      if (act.a === 'dock.pairing.close') push('dock', dockInfo())
    },
  )
  await page.goto(stack.url)
  await expect(page.getByTestId('home-banner')).toHaveAttribute('data-kind', 'auth_failed')
  await expect(page.getByTestId('banner-title')).toHaveText('Connexion refusée')
  await expect(page.getByTestId('banner-wake')).toHaveCount(0) // le PC répond : l'allumer n'avance à rien

  await page.getByTestId('banner-pair').tap()
  await expect.poll(() => acts.map((a) => a.a)).toContain('dock.pairing.open')
  await expect(page.getByTestId('home-banner')).toHaveAttribute('data-kind', 'pairing')
  await expect(page.getByTestId('banner-code')).toHaveText('K7QM-2XPD')
  await expect(page.getByTestId('banner-url')).toHaveText('http://192.168.1.60')
  await expect(page.getByTestId('home-banner')).toContainText('valable encore 9 min')
  await shot(page, '13-accueil-code-d-association', 'h')

  await page.getByTestId('banner-close').tap() // le PC est déjà associé : on peut renoncer
  await expect.poll(() => acts.map((a) => a.a)).toContain('dock.pairing.close')
  await expect(page.getByTestId('home-banner')).toHaveAttribute('data-kind', 'auth_failed')
})

test('Accueil (faux dock) : aucun PC associé, le bandeau mène aux Réglages', async ({ page, stack }) => {
  await mockDock(page, {
    link: link('not_configured', "Aucun PC n'est associé : installe l'agent depuis la page du dock."),
    dock: dockInfo({ pc: { host: '', port: 8765, token_set: false, name: null } }),
  })
  await page.goto(stack.url)
  await expect(page.getByTestId('home-banner')).toHaveAttribute('data-kind', 'not_configured')
  await expect(page.getByTestId('banner-title')).toHaveText('Aucun PC associé')
  await page.getByTestId('banner-settings').tap()
  await expect(page.getByTestId('page-settings')).toBeVisible()
})
