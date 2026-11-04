import { describe, expect, it, vi } from 'vitest'
import { entryOf, interfaceChanged, mayAutoReload, RELOAD_GUARD_MS, reloadIfAllowed, reloadNow, runningEntry, takeReopen } from './build'

const page = (entry: string) =>
  `<!doctype html><html><head><meta charset="UTF-8"><title>MyDock</title>` +
  `<script type="module" crossorigin src="${entry}"></script>` +
  `<link rel="modulepreload" crossorigin href="/assets/toast.svelte-CO2xHjwj.js">` +
  `<link rel="stylesheet" crossorigin href="/assets/index-ZNNgjOGk.css"></head><body><div id="app"></div></body></html>`

describe('entryOf', () => {
  it("trouve le script d'entrée de la page compilée", () => {
    expect(entryOf(page('/assets/index-D5XASdAc.js'))).toBe('/assets/index-D5XASdAc.js')
  })
  it('comprend les adresses relatives, comme les écrit la compilation (« ./assets/… »)', () => {
    expect(entryOf(page('./assets/index-D4NnB0ZJ.js'))).toBe('/assets/index-D4NnB0ZJ.js')
  })
  it("ne confond pas l'entrée avec les préchargements ni avec une adresse complète", () => {
    expect(entryOf(page('http://127.0.0.1:8080/assets/index-D5XASdAc.js'))).toBe('/assets/index-D5XASdAc.js')
  })
  it('accepte les attributs dans un autre ordre et les apostrophes', () => {
    expect(entryOf(`<script src='/assets/a.js' crossorigin type='module'></script>`)).toBe('/assets/a.js')
  })
  it("n'invente rien quand il n'y a pas de script d'entrée", () => {
    expect(entryOf('x')).toBeNull()
    expect(entryOf('<script src="/a.js"></script>')).toBeNull() // un script classique n'est pas l'entrée
    expect(entryOf('<script type="module">1</script>')).toBeNull()
  })
})

describe('runningEntry', () => {
  it("lit le script d'entrée de la page qui tourne", () => {
    const doc = {
      baseURI: 'http://127.0.0.1:8080/',
      querySelector: (selector: string) =>
        selector === 'script[type="module"][src]' ? { getAttribute: () => '/assets/index-OLD.js' } : null,
    } as unknown as Document
    expect(runningEntry(doc)).toBe('/assets/index-OLD.js')
  })
  it('vaut null sans script (serveur de développement, page étrangère)', () => {
    const doc = { baseURI: 'http://x/', querySelector: () => null } as unknown as Document
    expect(runningEntry(doc)).toBeNull()
  })
})

describe('interfaceChanged', () => {
  const serving = (entry: string, ok = true) =>
    vi.fn(async () => ({ ok, text: async () => page(entry) }) as unknown as Response)

  it("est vrai quand le dock sert une autre interface (mise à jour du paquet pendant que l'écran était ouvert)", async () => {
    const fetcher = serving('/assets/index-NEW.js')
    expect(await interfaceChanged(fetcher, '/assets/index-OLD.js')).toBe(true)
    expect(fetcher).toHaveBeenCalledWith('/', { cache: 'no-store' }) // jamais une copie du cache
  })
  it('est faux quand rien n’a changé', async () => {
    expect(await interfaceChanged(serving('/assets/index-OLD.js'), '/assets/index-OLD.js')).toBe(false)
  })
  it('est faux au moindre doute : dock injoignable, erreur du serveur, réponse sans entrée, page sans entrée', async () => {
    expect(await interfaceChanged(vi.fn().mockRejectedValue(new Error('hors ligne')), '/assets/a.js')).toBe(false)
    expect(await interfaceChanged(serving('/assets/index-NEW.js', false), '/assets/a.js')).toBe(false)
    const empty = vi.fn(async () => ({ ok: true, text: async () => 'Bad gateway' }) as unknown as Response)
    expect(await interfaceChanged(empty, '/assets/a.js')).toBe(false)
    const never = serving('/assets/index-NEW.js')
    expect(await interfaceChanged(never, null)).toBe(false)
    expect(never).not.toHaveBeenCalled() // sans entrée connue, on ne demande rien
  })
})

describe('mayAutoReload', () => {
  it('permet un premier rechargement, puis attend la fin de la fenêtre', () => {
    expect(mayAutoReload(1_000, null)).toBe(true)
    expect(mayAutoReload(1_000 + RELOAD_GUARD_MS, 1_000)).toBe(false)
    expect(mayAutoReload(1_001 + RELOAD_GUARD_MS, 1_000)).toBe(true)
  })
  it("refuse si l'horloge a reculé (pas de boucle sur une heure fausse)", () => {
    expect(mayAutoReload(500, 1_000)).toBe(false)
  })
})

describe('reloadIfAllowed', () => {
  const memory = (initial?: string) => {
    const data = new Map<string, string>(initial === undefined ? [] : [['mydock.reloaded', initial]])
    return {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => void data.set(key, value),
      data,
    }
  }

  it('recharge et le note', () => {
    const reload = vi.fn()
    const storage = memory()
    expect(reloadIfAllowed({ now: 5_000, storage, reload })).toBe(true)
    expect(reload).toHaveBeenCalledOnce()
    expect(storage.data.get('mydock.reloaded')).toBe('5000')
  })
  it('ne recharge pas deux fois de suite : si le problème persiste, il est affiché', () => {
    const reload = vi.fn()
    const storage = memory('5000')
    expect(reloadIfAllowed({ now: 5_000 + 10_000, storage, reload })).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })
  it('recharge de nouveau une fois la fenêtre passée', () => {
    const reload = vi.fn()
    expect(reloadIfAllowed({ now: 5_000 + RELOAD_GUARD_MS + 1, storage: memory('5000'), reload })).toBe(true)
    expect(reload).toHaveBeenCalledOnce()
  })
  it("ne recharge pas si la mémoire de l'onglet est inutilisable : un message vaut mieux qu'une boucle", () => {
    const reload = vi.fn()
    const broken = {
      getItem: () => {
        throw new Error('stockage bloqué')
      },
      setItem: () => {
        throw new Error('stockage bloqué')
      },
    }
    expect(reloadIfAllowed({ now: 1, storage: broken, reload })).toBe(false)
    const readOnly = { getItem: () => null, setItem: () => { throw new Error('plein') } }
    expect(reloadIfAllowed({ now: 1, storage: readOnly, reload })).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })
})

describe('rouvrir la page touchée après un rechargement', () => {
  const memory = () => {
    const data = new Map<string, string>()
    return {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => void data.set(key, value),
      removeItem: (key: string) => void data.delete(key),
      data,
    }
  }

  it('reloadIfAllowed note la page, takeReopen la rend une seule fois', () => {
    const storage = memory()
    expect(reloadIfAllowed({ now: 1_000, storage, reload: vi.fn(), reopen: 'music' })).toBe(true)
    expect(takeReopen(storage)).toBe('music')
    expect(takeReopen(storage)).toBeNull() // lue une fois : un rechargement à la main repart de l'accueil
  })
  it("sans page demandée, rien n'est noté", () => {
    const storage = memory()
    reloadIfAllowed({ now: 1_000, storage, reload: vi.fn() })
    expect(takeReopen(storage)).toBeNull()
  })
  it('un rechargement refusé par le garde-fou ne note rien', () => {
    const storage = memory()
    storage.setItem('mydock.reloaded', '1000')
    expect(reloadIfAllowed({ now: 2_000, storage, reload: vi.fn(), reopen: 'music' })).toBe(false)
    expect(takeReopen(storage)).toBeNull()
  })
  it('reloadNow recharge toujours (geste de la personne) et note la page', () => {
    const storage = memory()
    const reload = vi.fn()
    reloadNow('sound', { storage, reload })
    expect(reload).toHaveBeenCalledOnce()
    expect(takeReopen(storage)).toBe('sound')
  })
  it('reloadNow recharge même si la mémoire est bloquée', () => {
    const reload = vi.fn()
    const broken = { getItem: () => null, setItem: () => { throw new Error('plein') } }
    reloadNow('sound', { storage: broken, reload })
    expect(reload).toHaveBeenCalledOnce()
  })
  it("takeReopen ne casse pas si la mémoire de l'onglet est inutilisable", () => {
    const broken = { getItem: () => { throw new Error('bloqué') }, removeItem: () => { throw new Error('bloqué') } }
    expect(takeReopen(broken)).toBeNull()
  })
})
