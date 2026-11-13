<script lang="ts">
  import Bluetooth from '@lucide/svelte/icons/bluetooth'
  import Gamepad2 from '@lucide/svelte/icons/gamepad-2'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import Search from '@lucide/svelte/icons/search'
  import SearchX from '@lucide/svelte/icons/search-x'
  import Upload from '@lucide/svelte/icons/upload'
  import { onMount } from 'svelte'
  import { editText } from '../lib/keyboard.svelte'
  import { emptyText, filterGames, gameCount, noPadWarning, padSummary, retroBlocker, scanRatio, statusLabel } from '../lib/retro'
  import { retro } from '../lib/retro.svelte'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'

  // Retrogame : les jeux d'arcade de MAME. Un toucher lance le jeu en plein écran sur l'écran du dock ; il se quitte d'un
  // toucher de l'écran, du bouton physique, ou de Select + Start tenus. À la fin on revient ici.
  let { onPads, onUpload }: { onPads: () => void; onUpload: () => void } = $props()

  onMount(() => {
    void retro.open()
  })

  const data = $derived(sock.retro)
  const shown = $derived(filterGames(data?.games ?? [], retro.query))
  const summary = $derived(padSummary(sock.pads))
  const warning = $derived(noPadWarning(sock.pads))
  const blocker = $derived(retroBlocker(sock.player, data))
  const ratio = $derived(scanRatio(data))
  // Une capture qui manque (404) ne doit pas laisser une icône cassée : on retombe sur la manette.
  let broken = $state<Record<string, boolean>>({})

  async function search(): Promise<void> {
    const typed = await editText({ title: 'Chercher un jeu', hint: 'un nom, un éditeur, une année', kind: 'text', value: retro.query })
    if (typed !== null) retro.query = typed.trim()
  }

  async function play(id: string): Promise<void> {
    if (!summary.connected) notify('Aucune manette connectée : lancement quand même.', 'info', 5_000)
    await retro.play(id)
  }
</script>

<div class="bar">
  <div class="pill" class:ok={summary.connected} data-testid="pad-pill" data-connected={summary.connected}>
    <Gamepad2 size={34} aria-hidden="true" />
    <span class="txt">
      <strong class="label" data-testid="pad-pill-label">{summary.label}</strong>
      {#if summary.detail}<span class="detail" data-testid="pad-pill-detail">{summary.detail}</span>{/if}
    </span>
  </div>
  <button class="btn" data-testid="games-pads" onclick={onPads}><Bluetooth size={30} aria-hidden="true" />Manettes</button>
</div>

<div class="bar">
  <button class="btn search" data-testid="games-search" onclick={search}>
    <Search size={30} aria-hidden="true" />
    <span class="q" class:dim={!retro.query}>{retro.query || 'Chercher un jeu…'}</span>
  </button>
  {#if retro.query}
    <button class="btn icon" aria-label="Effacer la recherche" data-testid="games-clear" onclick={() => (retro.query = '')}>
      <SearchX size={30} aria-hidden="true" />
    </button>
  {/if}
  <button class="btn icon" aria-label="Actualiser la liste des jeux" data-testid="games-refresh" disabled={data?.state === 'scanning'} onclick={() => retro.refresh()}>
    <RefreshCw size={30} aria-hidden="true" />
  </button>
  <button class="btn icon" aria-label="Envoyer des jeux depuis le PC" data-testid="games-upload" onclick={onUpload}>
    <Upload size={30} aria-hidden="true" />
  </button>
</div>

{#if blocker}
  <p class="notice warn" data-testid="games-blocker">{blocker}</p>
{:else if warning}
  <p class="notice warn" data-testid="games-nopad">{warning}</p>
{/if}
{#if data && !data.readable}
  <p class="notice warn" data-testid="games-unreadable">Le dossier des ROMs n’est pas lisible : {data.roms_dir}. Vérifie qu’il existe et que l’utilisateur du kiosque peut le lire.</p>
{/if}
{#if data?.state === 'error' && data.error}
  <p class="notice warn" data-testid="games-error">{data.error}</p>
{/if}
{#if data?.state === 'scanning'}
  <div class="scan" data-testid="games-scan">
    <span>Vérification des jeux par MAME… {data.done} / {data.total}</span>
    {#if ratio !== null}<div class="meter"><span style:width="{ratio * 100}%"></span></div>{/if}
  </div>
{/if}

<p class="count dim" data-testid="games-count">
  {gameCount(shown.length)}{retro.query ? ` pour « ${retro.query} »` : ''}
</p>
<ul class="list" data-testid="games-list">
  {#each shown as game (game.id)}
    {@const flag = statusLabel(game.status)}
    <li>
      <button class="row" class:busy={retro.starting === game.id} disabled={retro.starting !== null || blocker !== null} data-testid="game-{game.id}" onclick={() => play(game.id)}>
        <span class="thumb">
          {#if game.snap && !broken[game.id]}
            <img src="/retrogame/snap/{game.id}" alt="" loading="lazy" onerror={() => (broken[game.id] = true)} />
          {:else}
            <Gamepad2 size={40} aria-hidden="true" />
          {/if}
        </span>
        <span class="what">
          <strong class="title">{game.title}</strong>
          <span class="meta dim">{[game.year, game.maker].filter(Boolean).join(' · ')}{#if flag}<span class="flag"> · {flag}</span>{/if}</span>
        </span>
        {#if retro.starting === game.id}<span class="chip accent">Lancement…</span>{/if}
      </button>
    </li>
  {:else}
    <li class="none dim" data-testid="games-none">{emptyText(data, retro.query)}</li>
  {/each}
</ul>

<style>
  .bar {
    display: flex;
    gap: 10px;
    margin-bottom: 10px;
  }
  .pill {
    flex: 1 1 0;
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    min-height: calc(var(--tap) + 8px);
    padding: 4px 18px;
    border-radius: 18px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    color: var(--text-3);
  }
  .pill.ok {
    border-color: var(--good);
    color: var(--text);
  }
  .pill.ok :global(svg) {
    color: var(--good);
  }
  .pill :global(svg) {
    flex: none;
  }
  .txt {
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.15;
  }
  .label {
    font-size: var(--fs-cap);
  }
  .detail {
    font-size: var(--fs-cap);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-2, var(--text));
  }
  .search {
    flex: 1 1 0;
    justify-content: flex-start;
    min-width: 0;
  }
  .q {
    overflow: hidden;
    text-overflow: ellipsis;
    font-weight: 500;
  }
  .notice {
    margin: 0 0 8px;
    font-size: var(--fs-small);
  }
  .notice.warn {
    color: var(--warn);
  }
  .scan {
    margin-bottom: 8px;
    font-size: var(--fs-cap);
  }
  .meter {
    margin-top: 6px;
    height: 8px;
    border-radius: 4px;
    background: var(--surface-3);
    overflow: hidden;
  }
  .meter span {
    display: block;
    height: 100%;
    background: var(--accent);
  }
  .count {
    font-size: var(--fs-cap);
    margin: 0 0 8px;
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    min-height: 96px;
    padding: 8px 16px 8px 8px;
    border-radius: 14px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    text-align: left;
  }
  .row:active:not(:disabled) {
    background: var(--surface-3);
  }
  .row.busy {
    border-color: var(--accent);
  }
  .thumb {
    flex: none;
    display: grid;
    place-items: center;
    width: 112px;
    height: 80px;
    border-radius: 10px;
    background: #000;
    color: var(--text-3);
    overflow: hidden;
  }
  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }
  .what {
    display: flex;
    flex-direction: column;
    flex: 1 1 0;
    min-width: 0;
  }
  .title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-small);
  }
  .meta {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-cap);
  }
  .flag {
    color: var(--warn);
  }
  .none {
    padding: 20px 4px;
    font-size: var(--fs-small);
  }
</style>
