<script lang="ts">
  import Gamepad2 from '@lucide/svelte/icons/gamepad-2'
  import { onMount } from 'svelte'
  import { appHue } from '../lib/audio'
  import { launch } from '../lib/launch.svelte'
  import { sortGames } from '../lib/launcher'
  import { steamCovers } from '../lib/pictures.svelte'
  import type { GameInfo, SteamData } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { whenVisible } from '../lib/visible'

  // La page suit les jeux installés et le jeu en cours (l'agent regarde les processus) tant qu'elle est ouverte.
  onMount(() => sock.subscribe(['steam']))

  const data = $derived(sock.topics.steam as SteamData | undefined)
  const running = $derived(data?.running ?? null)
  const games = $derived(data ? sortGames(data.games, running) : [])
  const current = $derived(games.find((g) => g.appid === running) ?? null)

  // Un jeu qui est « en cours » n'est plus « en lancement ».
  $effect(() => {
    if (running !== null) launch.done(`steam:${running}`)
  })

  function play(game: GameInfo): void {
    void launch.run(`steam:${game.appid}`, 'steam.launch', { appid: game.appid }, game.name, 30_000)
  }
</script>

{#if data?.unavailable}
  <section class="empty" data-testid="steam-unavailable">
    <Gamepad2 size={64} aria-hidden="true" />
    <h1>Steam indisponible</h1>
    <p class="muted">{data.unavailable}</p>
  </section>
{:else if !data}
  <p class="muted" data-testid="steam-loading">Lecture de la bibliothèque Steam…</p>
{:else}
  <div class="status" class:live={running !== null} data-testid="steam-status">
    <i aria-hidden="true"></i>
    {#if current}
      <span>En cours : <strong data-testid="steam-running">{current.name}</strong></span>
    {:else if running !== null}
      <span data-testid="steam-running">En cours : jeu n° {running}</span>
    {:else if data.client}
      <span>Steam est lancé</span>
    {:else}
      <span class="dim">Steam n’est pas lancé : toucher un jeu le démarre</span>
    {/if}
    <span class="count dim" data-testid="steam-count">{games.length} jeu{games.length > 1 ? 'x' : ''}</span>
  </div>

  {#if games.length === 0}
    <p class="muted none" data-testid="steam-empty">Aucun jeu installé pour l’instant : installe-en un depuis Steam.</p>
  {:else}
    <div class="grid" data-testid="steam-grid">
      {#each games as game (game.appid)}
        {@const url = steamCovers.urls[String(game.appid)]}
        {@const live = game.appid === running}
        {@const busy = launch.isBusy(`steam:${game.appid}`)}
        <button
          class="game"
          class:live
          class:busy
          aria-label="Lancer {game.name}"
          title={game.name}
          data-testid="game-{game.appid}"
          onclick={() => play(game)}
          use:whenVisible={() => steamCovers.want(String(game.appid))}
        >
          {#if url}
            <img src={url} alt="" decoding="async" />
          {:else}
            <span class="plain" style:--hue={appHue(game.name)}>{game.name}</span>
          {/if}
          {#if live}
            <span class="badge" data-testid="badge-{game.appid}">En cours</span>
          {:else if busy}
            <span class="badge" data-testid="badge-{game.appid}">Lancement…</span>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
{/if}

<style>
  .status {
    display: flex;
    align-items: center;
    gap: 10px 14px;
    flex-wrap: wrap;
    min-height: calc(var(--tap) + 8px);
    margin-bottom: 12px;
    padding: 8px 18px;
    border-radius: var(--radius-s);
    background: var(--surface);
    border: 1px solid var(--line);
    font-size: var(--fs-small);
  }
  .status i {
    flex: none;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--text-3);
  }
  .status.live {
    background: var(--accent-soft);
    border-color: var(--accent);
  }
  .status.live i {
    background: var(--good);
  }
  .count {
    margin-left: auto;
    font-size: var(--fs-cap);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
  }
  .game {
    position: relative;
    aspect-ratio: 2 / 3;
    overflow: hidden;
    border-radius: var(--radius-s);
    background: var(--surface-2);
    border: 2px solid transparent;
  }
  .game:active {
    transform: scale(0.97);
  }
  .game.live,
  .game.busy {
    border-color: var(--accent);
  }
  .game img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .plain {
    display: grid;
    place-items: center;
    width: 100%;
    height: 100%;
    padding: 8px;
    background: linear-gradient(135deg, hsl(var(--hue) 55% 34%), hsl(var(--hue) 60% 14%));
    font-size: var(--fs-small);
    font-weight: 700;
    line-height: 1.15;
    text-align: center;
    overflow-wrap: anywhere;
  }
  .badge {
    position: absolute;
    left: 8px;
    right: 8px;
    top: 8px;
    padding: 4px 0;
    border-radius: 999px;
    background: var(--accent);
    color: var(--accent-ink);
    font-size: calc(12px * var(--ts));
    font-weight: 700;
    text-align: center;
  }
  .none {
    padding: 14px 2px;
  }
  .empty {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    text-align: center;
    color: var(--text-3);
  }
  .empty h1 {
    font-size: var(--fs-title);
    color: var(--text);
  }
  .empty p {
    max-width: 760px;
  }
</style>
