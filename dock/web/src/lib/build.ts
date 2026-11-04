// L'interface qui tourne dans cet écran est-elle encore celle que le dock sert ?
//
// Les pages (Musique, Son…) se chargent à la demande, depuis des fichiers dont le nom change à chaque
// compilation. Quand le paquet du dock est mis à jour pendant que l'écran est ouvert, l'ancienne
// interface, restée en mémoire, demande des fichiers qui n'existent plus : sans ce qui suit, on touche un
// bouton de l'accueil et la page reste vide. On compare donc le script d'entrée de la page qui tourne à celui que le
// dock sert maintenant ; s'ils diffèrent, on recharge (une fois : jamais de boucle).

/** Entre deux rechargements automatiques : si le problème persiste, on l'affiche au lieu de recharger sans fin. */
export const RELOAD_GUARD_MS = 30_000

const GUARD_KEY = 'mydock.reloaded'
const REOPEN_KEY = 'mydock.reopen'

/** Le script d'entrée d'une page index.html (« /assets/index-Cx3.js »), ou null s'il n'y en a pas. */
export function entryOf(html: string): string | null {
  const tag = /<script\b[^>]*\btype=["']module["'][^>]*>/i.exec(html)
  const src = tag ? /\bsrc=["']([^"']+)["']/i.exec(tag[0]) : null
  if (!src) return null
  try {
    return new URL(src[1], 'http://dock.invalid').pathname
  } catch {
    return null
  }
}

/** Le script d'entrée de la page qui tourne. */
export function runningEntry(doc: Document = document): string | null {
  const script = doc.querySelector('script[type="module"][src]')
  if (!script) return null
  try {
    return new URL(script.getAttribute('src') ?? '', doc.baseURI).pathname
  } catch {
    return null
  }
}

/**
 * Vrai quand le dock sert maintenant une autre interface que celle de cette page (mise à jour du paquet
 * pendant que l'écran était ouvert). Faux au moindre doute : dock injoignable, réponse étrangère…
 */
export async function interfaceChanged(
  fetcher: typeof fetch = (input, init) => fetch(input, init),
  running: string | null = runningEntry(),
): Promise<boolean> {
  if (running === null) return false
  try {
    const response = await fetcher('/', { cache: 'no-store' })
    if (!response.ok) return false
    const served = entryOf(await response.text())
    return served !== null && served !== running
  } catch {
    return false
  }
}

/** Un rechargement automatique est permis s'il n'y en a pas eu depuis moins de RELOAD_GUARD_MS. */
export function mayAutoReload(now: number, last: number | null): boolean {
  return last === null || now - last > RELOAD_GUARD_MS
}

export interface ReloadDeps {
  now?: number
  storage?: Pick<Storage, 'getItem' | 'setItem'>
  reload?: () => void
  /** La page que la personne vient de toucher : on la rouvre après le rechargement (l'écran repart sinon de l'accueil). */
  reopen?: string
}

/**
 * Recharge la page, une fois par fenêtre de RELOAD_GUARD_MS. Vrai si elle se recharge. Si la mémoire de
 * l'onglet est inutilisable on ne recharge pas : mieux vaut un message qu'une boucle.
 */
export function reloadIfAllowed(deps: ReloadDeps = {}): boolean {
  const now = deps.now ?? Date.now()
  const reload = deps.reload ?? (() => location.reload())
  try {
    const storage = deps.storage ?? sessionStorage
    const last = Number(storage.getItem(GUARD_KEY)) || null
    if (!mayAutoReload(now, last)) return false
    storage.setItem(GUARD_KEY, String(now))
    if (deps.reopen) storage.setItem(REOPEN_KEY, deps.reopen)
  } catch {
    return false
  }
  reload()
  return true
}

/**
 * Recharge à la demande de la personne (bouton « Recharger ») : pas de garde-fou, c'est son geste. La page
 * affichée se rouvre ensuite d'elle-même.
 */
export function reloadNow(reopen?: string, deps: Pick<ReloadDeps, 'storage' | 'reload'> = {}): void {
  const reload = deps.reload ?? (() => location.reload())
  try {
    if (reopen) (deps.storage ?? sessionStorage).setItem(REOPEN_KEY, reopen)
  } catch {
    // mémoire de l'onglet inutilisable : on recharge quand même, l'accueil s'affichera
  }
  reload()
}

/** La page à rouvrir après un rechargement (lue une seule fois), ou null : l'écran repart de l'accueil. */
export function takeReopen(storage: Pick<Storage, 'getItem' | 'removeItem'> = sessionStorage): string | null {
  try {
    const page = storage.getItem(REOPEN_KEY)
    storage.removeItem(REOPEN_KEY)
    return page || null
  } catch {
    return null
  }
}

/** À appeler quand la liaison avec le dock revient : si le dock a changé d'interface, on la recharge. */
export async function refreshIfOutdated(): Promise<boolean> {
  return (await interfaceChanged()) && reloadIfAllowed()
}
