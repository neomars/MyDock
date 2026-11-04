<script lang="ts">
  import { gridWidth, homeLayout } from '../lib/home'
  import { music } from '../lib/music.svelte'
  import { visiblePages } from '../lib/pages'
  import type { PageId } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import HomeBanner from './HomeBanner.svelte'
  import HomeTile from './HomeTile.svelte'

  let { onOpen }: { onOpen: (id: PageId) => void } = $props()

  // Le menu principal : un gros bouton par page, dans l'ordre choisi dans les Réglages. La grille remplit la place
  // que laisse le bandeau (association, PC éteint) ; sa taille est mesurée, pas écrite en dur.
  const pages = $derived(visiblePages(sock.ui?.order ?? [], sock.ui?.hidden ?? []))
  let areaWidth = $state(992)
  let areaHeight = $state(480)
  const layout = $derived(homeLayout(pages.length, areaWidth, areaHeight))

  const player = $derived(music.active)
  const cover = $derived(player?.art ? (music.arts[player.art] ?? null) : null)
  $effect(() => {
    void music.ensureArt(player?.art ?? null)
  })
</script>

<section class="home" data-testid="home">
  <HomeBanner {onOpen} />
  <div class="area" bind:clientWidth={areaWidth} bind:clientHeight={areaHeight}>
    <nav
      class="grid"
      aria-label="Menu principal"
      style:width="{gridWidth(layout)}px"
      style:gap="{layout.gap}px"
      data-testid="home-grid"
      data-cols={layout.cols}
      data-rows={layout.rows}
    >
      {#each pages as page (page.id)}
        <HomeTile
          label={page.tab}
          icon={page.icon}
          tone={page.tone}
          width={layout.width}
          height={layout.height}
          dim={page.needsPc && !sock.connected}
          subtitle={page.id === 'music' && player ? (player.title ?? player.name) : null}
          cover={page.id === 'music' && player ? cover : null}
          testid="tile-{page.id}"
          onTap={() => onOpen(page.id)}
        />
      {/each}
    </nav>
  </div>
</section>

<style>
  .home {
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 16px;
  }
  .area {
    flex: 1 1 0;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .grid {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    align-content: center;
  }
</style>
