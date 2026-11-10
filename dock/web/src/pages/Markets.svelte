<script lang="ts">
  import ArrowDownRight from '@lucide/svelte/icons/arrow-down-right'
  import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right'
  import ChevronLeft from '@lucide/svelte/icons/chevron-left'
  import CircleCheck from '@lucide/svelte/icons/circle-check'
  import CircleX from '@lucide/svelte/icons/circle-x'
  import Clock from '@lucide/svelte/icons/clock'
  import Minus from '@lucide/svelte/icons/minus'
  import Pencil from '@lucide/svelte/icons/pencil'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import TrendingUp from '@lucide/svelte/icons/trending-up'
  import Stethoscope from '@lucide/svelte/icons/stethoscope'
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
    lagLabel,
    providerProblems,
    sourcesOf,
    toneOf,
    tradeLag,
  } from '../lib/markets'
  import { history, marketsUi, ticker } from '../lib/markets.svelte'
  import type { MarketItem, MarketRange, MarketsData, MarketsDiagnosis } from '../lib/protocol'
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
      ? [
          freshness(detail.fetched, now),
          sourceLabel(detail.source),
          tradeLag(detail, now) !== null ? lagLabel(tradeLag(detail, now) ?? 0) : '',
          detail.error,
        ]
          .filter(Boolean)
          .join(' · ')
      : '',
  )

  // « Tester la connexion » : ce que le Pi voit vraiment (heure, noms, IPv4/IPv6, chaque fournisseur).
  let diagnosis = $state<MarketsDiagnosis | null>(null)
  let diagnosing = $state(false)

  async function diagnose(): Promise<void> {
    if (diagnosing) return
    diagnosing = true
    const ack = await sock.act('dock.markets.diagnose', {}, { timeoutMs: 50_000 })
    diagnosing = false
    if (ack.ok && ack.r) diagnosis = ack.r as MarketsDiagnosis
    else notify(ack.e?.msg ?? 'Le test n’a pas pu se faire.', 'bad', 6_000)
  }

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
    {#if tone === 'up'}<ArrowUpRight size={32} aria-hidden="true" />
    {:else if tone === 'down'}<ArrowDownRight size={32} aria-hidden="true" />
    {:else}<Minus size={32} aria-hidden="true" />{/if}
    {formatPercent(item.change_pct)}
  </span>
{/snippet}

{#if detail}
  {@const tone = toneOfItem(detail)}
  {@const stale = isStale(detail, now)}
  <section class="detail" data-testid="detail">
    <header class="bar">
      <button class="btn" data-testid="detail-back" onclick={() => marketsUi.close()}>
        <ChevronLeft size={36} aria-hidden="true" />Liste
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
      {#if stale}<Clock size={26} aria-hidden="true" />{/if}
      {metaText}
    </p>

    <div class="plot">
      {#if entry?.data?.points?.length}
        <div class="chart" class:reloading={entry.state === 'loading'}>
          <PriceChart points={entry.data.points} {range} currency={entry.data.currency} {tone} />
        </div>
      {:else if entry?.state === 'error'}
        <div class="center" data-testid="detail-error">
          <TriangleAlert size={56} aria-hidden="true" />
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
      <RefreshCw size={30} aria-hidden="true" />Actualiser
    </button>
    <button class="btn" data-testid="markets-edit" onclick={() => marketsUi.edit()}>
      <Pencil size={30} aria-hidden="true" />Modifier
    </button>
  </div>

  {#if problems.length}
    <div class="problem" role="status" data-testid="markets-problem">
      <TriangleAlert size={38} aria-hidden="true" />
      <div>
        {#each problems as problem (problem)}<p>{problem}</p>{/each}
        {#if priced.length}<p class="dim">Les cours affichés sont les derniers connus.</p>{/if}
        <button class="btn test" class:busy={diagnosing} data-testid="markets-diagnose" onclick={diagnose}>
          <Stethoscope size={30} aria-hidden="true" />{diagnosing ? 'Test en cours…' : 'Tester la connexion'}
        </button>
      </div>
    </div>
  {/if}

  {#if diagnosis}
    <section class="diag" data-testid="markets-diag">
      {#each diagnosis.checks as check, index (index)}
        <div class="check" class:bad={!check.ok} data-testid="markets-diag-row">
          {#if check.ok}<CircleCheck size={32} aria-hidden="true" />{:else}<CircleX size={32} aria-hidden="true" />{/if}
          <span><strong>{check.label}</strong><small>{check.detail}</small></span>
        </div>
      {/each}
      {#if diagnosis.verdict}<p class="verdict" data-testid="markets-diag-verdict">{diagnosis.verdict}</p>{/if}
      <button class="btn" data-testid="markets-diag-close" onclick={() => (diagnosis = null)}>Fermer</button>
    </section>
  {/if}

  {#if data && items.length === 0}
    <section class="empty" data-testid="markets-empty">
      <TrendingUp size={84} aria-hidden="true" />
      <h1>Aucune valeur suivie</h1>
      <p class="muted">Choisis les cryptos et les actions que tu veux surveiller.</p>
      <button class="btn primary big" data-testid="markets-choose" onclick={() => marketsUi.edit()}>Choisir les valeurs</button>
    </section>
  {:else}
    <div class="grid" data-testid="markets-list">
      {#each items as item (item.id)}
        {@const tone = toneOfItem(item)}
        {@const stale = isStale(item, now)}
        {@const lag = tradeLag(item, now)}
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
            </small>
            {#if item.price === null && item.error}<small class="err" data-testid="error-{item.id}">{item.error}</small>{/if}
          </span>
          <span class="figures-s">
            <b class="price" data-testid="price-{item.id}">{item.price === null ? (item.error ? '—' : '…') : formatPrice(item.price, item.currency)}</b>
            {#if item.price !== null}{@render trend(item)}{/if}
          </span>
          <Sparkline values={item.spark} {tone} />
          {#if (item.price !== null && stale) || lag !== null}
            <span class="foot">
              {#if item.price !== null && stale}
                <span class="old" data-testid="old-{item.id}"><Clock size={22} aria-hidden="true" />{freshness(item.fetched, now)}</span>
              {/if}
              {#if lag !== null}<span class="lag" data-testid="lag-{item.id}">{lagLabel(lag)}</span>{/if}
            </span>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
{/if}

<style>
  .top {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px 14px;
    margin-bottom: 12px;
  }
  .dot {
    flex: none;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--good);
  }
  .dot.warn {
    background: var(--warn);
  }
  .status {
    flex: 1 1 0;
    font-size: var(--fs-small);
    color: var(--text-2);
    min-width: 0;
    line-height: 1.2;
  }
  .spacer {
    display: none;
  }
  .top .btn {
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
    margin-bottom: 12px;
    padding: 12px 16px;
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
  .problem .test {
    margin-top: 8px;
  }
  .diag {
    margin-bottom: 12px;
    padding: 8px 16px 12px;
    border-radius: var(--radius-s);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .check {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 6px 0;
    color: var(--good, #4ade80);
  }
  .check.bad {
    color: var(--bad);
  }
  .check :global(svg) {
    flex: none;
    margin-top: 2px;
  }
  .check span {
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.15;
  }
  .check strong {
    font-size: var(--fs-small);
    color: var(--text);
  }
  .check small {
    font-size: calc(13px * var(--ts));
    color: var(--text-2);
    overflow-wrap: anywhere;
  }
  .verdict {
    margin: 6px 0 10px;
    font-size: var(--fs-small);
    color: var(--warn);
  }
  .grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }
  .quote {
    position: relative;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 130px;
    align-items: center;
    column-gap: 16px;
    min-height: 120px;
    padding: 10px 18px;
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
  .foot {
    grid-column: 1 / -1;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    padding-top: 2px;
    font-size: calc(12px * var(--ts));
    line-height: 1.2;
    color: var(--warn);
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
    max-width: 420px;
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
    font-size: calc(12px * var(--ts));
    line-height: 1.2;
    color: var(--warn);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .old {
    display: inline-flex;
    align-items: center;
    gap: 4px;
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
  }
  .bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px 14px;
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
    flex: 1 1 100%;
    gap: 8px;
  }
  .range {
    flex: 1 1 0;
    height: var(--tap);
    padding: 0 10px;
    border-radius: 14px;
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
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 16px;
    margin-top: 6px;
  }
  .big {
    font-size: calc(30px * var(--ts) * 0.95);
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
    gap: 8px;
    min-height: 26px;
    font-size: var(--fs-cap);
  }
  .meta.warn {
    color: var(--warn);
  }
  .plot {
    height: 280px;
    display: flex;
    flex-direction: column;
    margin-top: 8px;
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
