<script lang="ts">
  import ArrowDownRight from '@lucide/svelte/icons/arrow-down-right'
  import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right'
  import ChevronLeft from '@lucide/svelte/icons/chevron-left'
  import Clock from '@lucide/svelte/icons/clock'
  import Minus from '@lucide/svelte/icons/minus'
  import Pencil from '@lucide/svelte/icons/pencil'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import TrendingUp from '@lucide/svelte/icons/trending-up'
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import { onMount, untrack } from 'svelte'
  import PriceChart from '../components/PriceChart.svelte'
  import Sparkline from '../components/Sparkline.svelte'
  import {
    RANGES,
    SESSION_LABELS,
    SOURCE_LABELS,
    formatChange,
    formatPercent,
    formatPrice,
    freshness,
    isStale,
    providerProblems,
    sourcesOf,
    toneOf,
  } from '../lib/markets'
  import { history, marketsUi, ticker } from '../lib/markets.svelte'
  import type { MarketItem, MarketRange, MarketsData } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'

  // Les cotations ne sont redemandées par le dock que pendant que cette page est ouverte.
  onMount(() => {
    const stop = sock.subscribe(['markets'])
    const stopTicker = ticker.start()
    return () => {
      stop()
      stopTicker()
      marketsUi.close()
    }
  })

  const sourceLabel = (id: string | null) => (id ? (SOURCE_LABELS[id] ?? id) : '')
  const data = $derived(sock.topics.markets as MarketsData | undefined)
  const items = $derived(data?.items ?? [])
  const now = $derived(ticker.now)
  const problems = $derived(data ? providerProblems(data.providers, now) : [])
  const priced = $derived(items.filter((i) => i.price !== null))
  const detail = $derived(items.find((i) => i.id === marketsUi.selected) ?? null)

  let range = $state<MarketRange>('1d')
  let refreshing = $state(false)

  // La valeur ouverte disparaît de la liste (retirée dans la fenêtre de modification) : retour à la liste.
  $effect(() => {
    if (marketsUi.selected && data && !detail) marketsUi.close()
  })

  // Le graphique de détail suit la valeur et la période choisies. `untrack` : c'est le choix qui déclenche la
  // demande, pas le résultat (sinon un échec se redemanderait en boucle).
  $effect(() => {
    const id = detail?.id
    const chosen = range
    if (id) untrack(() => void history.load(id, chosen))
  })

  const entry = $derived(detail ? history.get(detail.id, range) : undefined)

  const statusText = $derived.by(() => {
    if (!data) return 'Lecture des cotations…'
    if (priced.length === 0 && items.length > 0) {
      return problems.length ? 'Cotations indisponibles' : 'Chargement des cotations…'
    }
    const sources = sourcesOf(priced)
    return `Mis à jour ${freshness(data.fetched_at, now)}${sources ? ` · ${sources}` : ''}`
  })

  const metaText = $derived(
    detail
      ? [freshness(detail.fetched, now), sourceLabel(detail.source), detail.error].filter(Boolean).join(' · ')
      : '',
  )

  async function refresh(): Promise<void> {
    if (refreshing) return
    refreshing = true
    const ack = await sock.act('dock.markets.refresh', {}, { timeoutMs: 45_000 })
    refreshing = false
    if (!ack.ok) notify(ack.e?.msg ?? 'Actualisation impossible.', 'bad', 6_000)
    else if (ack.r?.refreshed === false) notify('Les cotations viennent d’être actualisées.', 'info', 2_500)
  }

  const toneOfItem = (item: MarketItem) => toneOf(item.change_pct)
  const nameOf = (item: MarketItem) => item.name ?? item.symbol
</script>

{#snippet trend(item: MarketItem)}
  {@const tone = toneOfItem(item)}
  <span class="pct {tone}" data-testid="pct-{item.id}">
    {#if tone === 'up'}<ArrowUpRight size={22} aria-hidden="true" />
    {:else if tone === 'down'}<ArrowDownRight size={22} aria-hidden="true" />
    {:else}<Minus size={22} aria-hidden="true" />{/if}
    {formatPercent(item.change_pct)}
  </span>
{/snippet}

{#if detail}
  {@const tone = toneOfItem(detail)}
  {@const stale = isStale(detail, now)}
  <section class="detail" data-testid="detail">
    <header class="bar">
      <button class="btn" data-testid="detail-back" onclick={() => marketsUi.close()}>
        <ChevronLeft size={26} aria-hidden="true" />Liste
      </button>
      <div class="title">
        <h1 data-testid="detail-name">{nameOf(detail)}</h1>
        <span class="dim">
          {detail.symbol}{#if detail.state} · {SESSION_LABELS[detail.state]}{/if}
        </span>
      </div>
      <div class="ranges" role="group" aria-label="Période">
        {#each RANGES as option (option.id)}
          <button
            class="range"
            class:on={range === option.id}
            aria-pressed={range === option.id}
            data-testid="range-{option.id}"
            onclick={() => (range = option.id)}
          >
            {option.label}
          </button>
        {/each}
      </div>
    </header>

    <div class="figures">
      <b class="big" class:old={stale} data-testid="detail-price">{formatPrice(detail.price, detail.currency)}</b>
      {@render trend(detail)}
      {#if detail.change !== null}<span class="dim" data-testid="detail-change">{formatChange(detail.change, detail.currency)}</span>{/if}
      <span class="spacer"></span>
      {#if detail.high !== null && detail.low !== null}
        <span class="dim">Haut {formatPrice(detail.high, detail.currency)} · Bas {formatPrice(detail.low, detail.currency)}</span>
      {/if}
    </div>
    <p class="meta dim" class:warn={stale} data-testid="detail-meta">
      {#if stale}<Clock size={18} aria-hidden="true" />{/if}
      {metaText}
    </p>

    <div class="plot">
      {#if entry?.data?.points?.length}
        <div class="chart" class:reloading={entry.state === 'loading'}>
          <PriceChart points={entry.data.points} {range} currency={entry.data.currency} {tone} />
        </div>
      {:else if entry?.state === 'error'}
        <div class="center" data-testid="detail-error">
          <TriangleAlert size={40} aria-hidden="true" />
          <p>{entry.message}</p>
          <button class="btn" data-testid="detail-retry" onclick={() => history.load(detail.id, range, true)}>Réessayer</button>
        </div>
      {:else}
        <p class="muted center" data-testid="detail-loading">Chargement du graphique…</p>
      {/if}
      {#if entry?.state === 'error' && entry.data}
        <p class="note warn" data-testid="detail-error">{entry.message}</p>
      {/if}
    </div>
  </section>
{:else}
  <div class="top" data-testid="markets-status">
    <span class="dot" class:warn={problems.length > 0}></span>
    <span class="status" data-testid="markets-statustext">{statusText}</span>
    {#if data?.demo}<span class="chip accent" data-testid="markets-demo">Démonstration</span>{/if}
    <span class="spacer"></span>
    <button class="btn" class:busy={refreshing} data-testid="markets-refresh" disabled={items.length === 0} onclick={refresh}>
      <RefreshCw size={22} aria-hidden="true" />Actualiser
    </button>
    <button class="btn" data-testid="markets-edit" onclick={() => marketsUi.edit()}>
      <Pencil size={22} aria-hidden="true" />Modifier
    </button>
  </div>

  {#if problems.length}
    <div class="problem" role="status" data-testid="markets-problem">
      <TriangleAlert size={26} aria-hidden="true" />
      <div>
        {#each problems as problem (problem)}<p>{problem}</p>{/each}
        {#if priced.length}<p class="dim">Les cours affichés sont les derniers connus.</p>{/if}
      </div>
    </div>
  {/if}

  {#if data && items.length === 0}
    <section class="empty" data-testid="markets-empty">
      <TrendingUp size={64} aria-hidden="true" />
      <h1>Aucune valeur suivie</h1>
      <p class="muted">Choisis les cryptos et les actions que tu veux surveiller.</p>
      <button class="btn primary big" data-testid="markets-choose" onclick={() => marketsUi.edit()}>Choisir les valeurs</button>
    </section>
  {:else}
    <div class="grid" data-testid="markets-list">
      {#each items as item (item.id)}
        {@const tone = toneOfItem(item)}
        {@const stale = isStale(item, now)}
        <button
          class="quote"
          class:loading={item.price === null}
          class:stale
          aria-label="Détail de {nameOf(item)}"
          data-testid="quote-{item.id}"
          onclick={() => marketsUi.open(item.id)}
        >
          <span class="who">
            <strong class="name">{nameOf(item)}</strong>
            <small class="sub">
              {item.symbol}
              {#if item.state}<span class="session {item.state}" data-testid="session-{item.id}"> · {SESSION_LABELS[item.state]}</span>{/if}
              {#if item.price !== null && stale}<span class="old" data-testid="old-{item.id}"> · <Clock size={14} aria-hidden="true" />{freshness(item.fetched, now)}</span>{/if}
            </small>
            {#if item.price === null && item.error}<small class="err" data-testid="error-{item.id}">{item.error}</small>{/if}
          </span>
          <span class="figures-s">
            <b class="price" data-testid="price-{item.id}">{item.price === null ? (item.error ? '—' : '…') : formatPrice(item.price, item.currency)}</b>
            {#if item.price !== null}{@render trend(item)}{/if}
          </span>
          <Sparkline values={item.spark} {tone} />
        </button>
      {/each}
    </div>
  {/if}
{/if}

<style>
  .top {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 56px;
    margin-bottom: 8px;
  }
  .dot {
    flex: none;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--good);
  }
  .dot.warn {
    background: var(--warn);
  }
  .status {
    font-size: var(--fs-small);
    color: var(--text-2);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .spacer {
    flex: 1 1 0;
  }
  .btn.busy :global(svg) {
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .problem {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    margin-bottom: 8px;
    padding: 10px 14px;
    border-radius: var(--radius-s);
    background: var(--warn-soft);
    border: 1px solid var(--warn);
    color: var(--warn);
    font-size: var(--fs-small);
    line-height: 1.3;
  }
  .problem :global(svg) {
    flex: none;
    margin-top: 2px;
  }
  .problem p + p {
    margin-top: 2px;
  }
  .problem .dim {
    color: var(--text-2);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .quote {
    position: relative;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 112px;
    align-items: center;
    column-gap: 14px;
    min-height: 84px;
    padding: 8px 14px;
    border-radius: var(--radius);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .quote:active {
    background: var(--surface-3);
  }
  .quote.loading .price {
    color: var(--text-3);
  }
  .quote.stale .price {
    color: var(--text-2);
  }
  .who {
    min-width: 0;
    display: flex;
    flex-direction: column;
    line-height: 1.2;
  }
  .name {
    font-size: var(--fs);
    font-weight: 650;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .sub {
    font-size: var(--fs-cap);
    color: var(--text-3);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .session.closed {
    color: var(--text-3);
  }
  .session.open {
    color: var(--good);
  }
  .figures-s {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    line-height: 1.2;
    max-width: 190px;
  }
  .price {
    font-size: var(--fs-large);
    font-weight: 700;
    white-space: nowrap;
  }
  .pct {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    font-size: var(--fs-small);
    font-weight: 600;
    white-space: nowrap;
  }
  .pct.up {
    color: var(--good);
  }
  .pct.down {
    color: var(--bad);
  }
  .pct.flat {
    color: var(--text-3);
  }
  .err {
    margin-top: 2px;
    font-size: 14px;
    line-height: 1.2;
    color: var(--warn);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .old {
    color: var(--warn);
  }
  .old :global(svg) {
    display: inline-block;
    vertical-align: -2px;
  }
  .empty {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    text-align: center;
    color: var(--text-3);
  }
  .empty h1 {
    font-size: var(--fs-title);
    color: var(--text);
  }

  /* --- détail ------------------------------------------------------------------------------ */
  .detail {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 14px;
    min-height: 56px;
  }
  .title {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    line-height: 1.15;
  }
  .title h1 {
    font-size: var(--fs-large);
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ranges {
    display: flex;
    gap: 6px;
  }
  .range {
    min-width: 72px;
    height: 48px;
    padding: 0 14px;
    border-radius: 12px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    font-weight: 600;
    text-align: center;
  }
  .range.on {
    background: var(--accent-soft);
    border-color: var(--accent);
    color: var(--accent);
  }
  .figures {
    display: flex;
    align-items: baseline;
    gap: 14px;
    margin-top: 2px;
  }
  .big {
    font-size: 40px;
    font-weight: 750;
    letter-spacing: -0.01em;
    white-space: nowrap;
  }
  .big.old {
    color: var(--text-2);
  }
  .figures .pct {
    font-size: var(--fs-large);
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 6px;
    min-height: 26px;
    font-size: var(--fs-cap);
  }
  .meta.warn {
    color: var(--warn);
  }
  .plot {
    flex: 1 1 0;
    min-height: 140px;
    display: flex;
    flex-direction: column;
    margin-top: 6px;
  }
  .chart {
    flex: 1 1 0;
    min-height: 0;
    transition: opacity 0.15s;
  }
  .chart.reloading {
    opacity: 0.55;
  }
  .center {
    flex: 1 1 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    text-align: center;
    color: var(--text-3);
  }
  .note {
    font-size: var(--fs-cap);
    padding-top: 4px;
  }
  .note.warn {
    color: var(--warn);
  }
</style>
