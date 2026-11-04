<script lang="ts">
  import type { Component } from 'svelte'

  let {
    label,
    icon: Icon,
    tone,
    width,
    height,
    dim = false,
    subtitle = null,
    cover = null,
    testid,
    onTap,
  }: {
    label: string
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    icon: Component<any>
    tone: { from: string; to: string }
    /** Dimensions du bouton, en pixels (voir homeLayout). */
    width: number
    height: number
    /** Page qui a besoin du PC alors qu'il n'est pas là : le bouton reste touchable, mais on voit qu'il est « éteint ». */
    dim?: boolean
    /** Une ligne sous le libellé (le morceau en cours). */
    subtitle?: string | null
    /** Image de fond (la pochette du morceau en cours). */
    cover?: string | null
    testid: string
    onTap: () => void
  } = $props()

  /** L'icône prend ce que le libellé et les marges lui laissent, entre une taille lisible et une taille raisonnable. */
  const iconSize = $derived(Math.max(32, Math.min(72, height - 72)))
</script>

<button
  class="tile"
  class:dim
  style:--from={tone.from}
  style:--to={tone.to}
  style:width="{width}px"
  style:height="{height}px"
  data-testid={testid}
  data-dim={dim ? 'true' : null}
  aria-label={subtitle ? `${label} : ${subtitle}` : label}
  onclick={onTap}
>
  {#if cover}
    <img class="cover" src={cover} alt="" />
    <span class="shade"></span>
  {:else}
    <span class="icon"><Icon size={iconSize} strokeWidth={1.75} aria-hidden="true" /></span>
  {/if}
  <span class="text">
    <span class="label">{label}</span>
    {#if subtitle}<span class="sub" data-testid="{testid}-sub">{subtitle}</span>{/if}
  </span>
</button>

<style>
  .tile {
    position: relative;
    flex: none;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    overflow: hidden;
    border-radius: 24px;
    background: linear-gradient(165deg, var(--from), var(--to));
    /* Un reflet en haut, une arête sombre en bas : une touche, sans flou ni ombre étendue (60 images/s sur un Pi 4). */
    box-shadow:
      inset 0 1px 0 rgb(255 255 255 / 0.25),
      0 3px 0 rgb(0 0 0 / 0.4);
    color: #fff;
    text-align: center;
  }
  .tile:active {
    transform: translateY(2px) scale(0.97);
    box-shadow:
      inset 0 1px 0 rgb(255 255 255 / 0.2),
      0 1px 0 rgb(0 0 0 / 0.4);
  }
  .tile.dim {
    opacity: 0.5;
  }
  .icon {
    flex: 1 1 0;
    min-height: 0;
    display: grid;
    place-items: center;
    padding-top: 8px;
  }
  .cover {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .shade {
    position: absolute;
    inset: 0;
    background: linear-gradient(to top, rgb(0 0 0 / 0.82) 12%, rgb(0 0 0 / 0.08) 70%);
  }
  .text {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    width: 100%;
    padding: 0 12px 12px;
  }
  .label {
    font-size: var(--fs-small);
    font-weight: 700;
    line-height: 1.15;
    letter-spacing: -0.01em;
  }
  .sub {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-cap);
    font-weight: 500;
    line-height: 1.2;
    opacity: 0.95;
  }
</style>
