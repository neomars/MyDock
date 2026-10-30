<script lang="ts">
  import ArrowDown from '@lucide/svelte/icons/arrow-down'
  import ArrowUp from '@lucide/svelte/icons/arrow-up'
  import Check from '@lucide/svelte/icons/check'
  import Plus from '@lucide/svelte/icons/plus'
  import Search from '@lucide/svelte/icons/search'
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import X from '@lucide/svelte/icons/x'
  import { editText } from '../lib/keyboard.svelte'
  import { moved } from '../lib/launcher'
  import {
    KIND_LABELS,
    MAX_WATCHED,
    SOURCE_LABELS,
    SUGGESTIONS,
    appended,
    cleanSymbol,
    formatPrice,
    watchedId,
  } from '../lib/markets'
  import { marketsUi } from '../lib/markets.svelte'
  import type { MarketCheck, MarketKind, MarketsData, Watched } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'

  let kind = $state<MarketKind>('crypto')
  let symbol = $state('')
  let checking = $state(false)
  let found = $state<MarketCheck | null>(null)
  let problem = $state<string | null>(null)

  // La fenêtre suit la liste et les cotations tant qu'elle est ouverte.
  $effect(() => {
    if (!marketsUi.editor) return
    return sock.subscribe(['markets'])
  })

  const data = $derived(sock.topics.markets as MarketsData | undefined)
  const items = $derived(data?.items ?? [])
  const list = $derived<Watched[]>(items.map((i) => ({ kind: i.kind, symbol: i.symbol })))
  const full = $derived(list.length >= MAX_WATCHED)
  const currency = $derived(data?.currency ?? 'EUR')

  function reset(): void {
    symbol = ''
    found = null
    problem = null
  }

  function setKind(next: MarketKind): void {
    if (next === kind) return
    kind = next
    reset()
  }

  async function save(next: Watched[]): Promise<boolean> {
    const ack = await sock.act('dock.markets.set', { watchlist: next })
    if (!ack.ok) notify(ack.e?.msg ?? 'Modification impossible.', 'bad', 6_000)
    return ack.ok
  }

  async function setCurrency(next: 'EUR' | 'USD'): Promise<void> {
    if (next === currency) return
    const ack = await sock.act('dock.markets.set', { currency: next })
    if (!ack.ok) notify(ack.e?.msg ?? 'Modification impossible.', 'bad', 6_000)
  }

  async function verify(): Promise<void> {
    const wanted = cleanSymbol(kind, symbol)
    if (!wanted) {
      problem = 'Saisis d’abord un symbole.'
      return
    }
    symbol = wanted
    checking = true
    found = null
    problem = null
    const ack = await sock.act('dock.markets.check', { kind, symbol: wanted }, { timeoutMs: 50_000 })
    checking = false
    const reply = ack.r as MarketCheck | undefined
    if (ack.ok && typeof reply?.id === 'string' && typeof reply.price === 'number') found = reply
    else problem = (ack.ok ? undefined : ack.e?.msg) ?? 'Vérification impossible.'
  }

  async function typeSymbol(): Promise<void> {
    const value = await editText({
      title: kind === 'crypto' ? 'Symbole de la crypto' : 'Symbole de la valeur',
      hint: kind === 'crypto' ? 'Par exemple BTC, ETH, SOL' : 'Par exemple MSTR, ^FCHI, CW8.PA',
      kind: 'symbol',
      value: symbol,
    })
    if (value === null) return
    symbol = cleanSymbol(kind, value)
    if (symbol) await verify()
    else reset()
  }

  async function suggest(candidate: string): Promise<void> {
    symbol = candidate
    await verify()
  }

  async function add(): Promise<void> {
    if (!found) return
    const next = appended(list, { kind: found.kind, symbol: found.symbol })
    if (next === null) {
      notify(
        full
          ? `La liste contient déjà ${MAX_WATCHED} valeurs : retires-en une d’abord.`
          : `« ${found.name ?? found.symbol} » est déjà dans la liste.`,
        'info',
        5_000,
      )
      return
    }
    const label = found.name ?? found.symbol
    if (await save(next)) {
      notify(`« ${label} » ajouté.`, 'good', 2_500)
      reset()
    }
  }

  const inList = (check: MarketCheck | null): boolean => !!check && list.some((w) => watchedId(w) === check.id)
</script>

{#if marketsUi.editor}
  <div class="overlay" role="dialog" aria-modal="true" aria-label="Choisir les valeurs de la Bourse" data-testid="mk-editor">
    <header>
      <h1>Valeurs suivies</h1>
      <div class="money" role="group" aria-label="Devise des cryptos">
        <span class="dim">Cryptos en</span>
        {#each [{ id: 'EUR', label: 'Euro' }, { id: 'USD', label: 'Dollar' }] as option (option.id)}
          <button
            class="seg"
            class:on={currency === option.id}
            aria-pressed={currency === option.id}
            data-testid="mk-currency-{option.id}"
            onclick={() => setCurrency(option.id as 'EUR' | 'USD')}
          >
            {option.label}
          </button>
        {/each}
      </div>
      <button class="btn primary" data-testid="mk-done" onclick={() => marketsUi.done()}>
        <Check size={26} aria-hidden="true" />Terminé
      </button>
    </header>

    <div class="cols">
      <section class="card" data-testid="mk-add">
        <h2>Ajouter une valeur</h2>
        <div class="kinds" role="group" aria-label="Type de valeur">
          {#each ['crypto', 'stock'] as const as option (option)}
            <button class="seg wide" class:on={kind === option} aria-pressed={kind === option} data-testid="mk-kind-{option}" onclick={() => setKind(option)}>
              {KIND_LABELS[option]}
            </button>
          {/each}
        </div>

        <div class="entry">
          <button class="btn field" data-testid="mk-symbol" onclick={typeSymbol}>
            <Search size={24} aria-hidden="true" />
            <span class:dim={!symbol}>{symbol || (kind === 'crypto' ? 'Symbole : BTC, ETH…' : 'Symbole : MSTR, ^FCHI…')}</span>
          </button>
          <button class="btn" data-testid="mk-check" disabled={checking || !symbol} onclick={verify}>
            {checking ? 'Vérification…' : 'Vérifier'}
          </button>
        </div>

        <div class="chips" data-testid="mk-suggestions">
          {#each SUGGESTIONS[kind] as candidate (candidate)}
            <button class="chip-btn" data-testid="mk-suggest-{candidate}" disabled={checking} onclick={() => suggest(candidate)}>{candidate}</button>
          {/each}
        </div>

        <div class="result" aria-live="polite">
          {#if checking}
            <p class="muted" data-testid="mk-checking">Interrogation des fournisseurs…</p>
          {:else if found}
            <div class="found" data-testid="mk-found">
              <span class="who">
                <strong>{found.name ?? found.symbol}</strong>
                <small>{found.symbol} · {formatPrice(found.price, found.currency)} · {SOURCE_LABELS[found.source] ?? found.source}</small>
              </span>
              <button class="btn primary" data-testid="mk-confirm" disabled={inList(found) || full} onclick={add}>
                {#if inList(found)}Déjà suivie{:else}<Plus size={24} aria-hidden="true" />Ajouter{/if}
              </button>
            </div>
          {:else if problem}
            <p class="problem" data-testid="mk-error"><TriangleAlert size={22} aria-hidden="true" />{problem}</p>
          {/if}
        </div>
      </section>

      <section class="card" data-testid="mk-chosen">
        <h2>Dans la liste ({list.length}/{MAX_WATCHED})</h2>
        <div class="list">
          {#if data && items.length === 0}
            <p class="muted" data-testid="mk-none">Rien pour l’instant : ajoute une crypto ou une valeur à gauche.</p>
          {/if}
          {#each items as item, index (item.id)}
            <div class="row" data-testid="mk-item-{item.id}">
              <span class="who">
                <strong>{item.name ?? item.symbol}</strong>
                <small>{item.symbol} · {KIND_LABELS[item.kind]}</small>
              </span>
              <button class="step" aria-label="Monter {item.symbol}" data-testid="mk-up-{item.id}" disabled={index === 0} onclick={() => save(moved(list, index, -1))}>
                <ArrowUp size={24} aria-hidden="true" />
              </button>
              <button class="step" aria-label="Descendre {item.symbol}" data-testid="mk-down-{item.id}" disabled={index === items.length - 1} onclick={() => save(moved(list, index, 1))}>
                <ArrowDown size={24} aria-hidden="true" />
              </button>
              <button class="step remove" aria-label="Retirer {item.symbol}" data-testid="mk-remove-{item.id}" onclick={() => save(list.filter((_, i) => i !== index))}>
                <X size={24} aria-hidden="true" />
              </button>
            </div>
          {/each}
        </div>
      </section>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: var(--topbar) 0 0 0;
    z-index: 30;
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 10px 20px 14px;
    background: var(--bg);
  }
  header {
    display: flex;
    align-items: center;
    gap: 16px;
  }
  h1 {
    flex: 1 1 0;
    font-size: var(--fs-title);
    font-weight: 700;
  }
  .money {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .money .dim {
    margin-right: 4px;
    font-size: var(--fs-small);
  }
  .cols {
    flex: 1 1 0;
    min-height: 0;
    display: grid;
    grid-template-columns: 1.15fr 1fr;
    gap: 12px;
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-height: 0;
    padding: 12px 14px 10px;
  }
  .card > h2 {
    margin-bottom: 0;
  }
  .seg {
    height: 48px;
    min-width: 76px;
    padding: 0 16px;
    border-radius: 12px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    font-weight: 600;
    text-align: center;
  }
  .seg.wide {
    flex: 1 1 0;
  }
  .seg.on {
    background: var(--accent-soft);
    border-color: var(--accent);
    color: var(--accent);
  }
  .kinds {
    display: flex;
    gap: 8px;
  }
  .entry {
    display: flex;
    gap: 8px;
  }
  .field {
    flex: 1 1 0;
    min-width: 0;
    justify-content: flex-start;
    font-weight: 500;
  }
  .field span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .chip-btn {
    min-width: 72px;
    height: 48px;
    padding: 0 14px;
    border-radius: 24px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    font-weight: 600;
    text-align: center;
  }
  .chip-btn:active:not(:disabled) {
    background: var(--surface-3);
  }
  .chip-btn:disabled {
    opacity: 0.4;
  }
  .result {
    min-height: 64px;
  }
  .found {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 6px 6px 12px;
    border-radius: var(--radius-s);
    background: var(--accent-soft);
    border: 1px solid var(--accent);
  }
  .who {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    line-height: 1.2;
  }
  .who strong {
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .who small {
    font-size: var(--fs-cap);
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .problem {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    color: var(--warn);
    font-size: var(--fs-small);
    line-height: 1.3;
  }
  .problem :global(svg) {
    flex: none;
    margin-top: 2px;
  }
  .list {
    flex: 1 1 0;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 60px;
    border-top: 1px solid var(--line);
  }
  .row:first-child {
    border-top: 0;
  }
  .step {
    flex: none;
    display: grid;
    place-items: center;
    width: 48px;
    height: 48px;
    border-radius: 12px;
    background: var(--surface-3);
  }
  .step:active:not(:disabled) {
    background: var(--line);
  }
  .step:disabled {
    opacity: 0.3;
  }
  .step.remove:active {
    background: var(--bad-soft);
    color: var(--bad);
  }
</style>
