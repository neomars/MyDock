<script lang="ts">
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import type { Component } from 'svelte'
  import { interfaceChanged, reloadIfAllowed } from '../lib/build'

  let { load }: { load: () => Promise<{ default: Component }> } = $props()

  /**
   * Charge la page. Si elle est introuvable parce que le dock a changé d'interface depuis l'ouverture de
   * cet écran (mise à jour du paquet), on recharge tout : l'écran se répare seul. Sinon l'erreur est
   * affichée : une page vide ne dirait rien à personne.
   */
  async function attempt(): Promise<{ default: Component }> {
    try {
      return await load()
    } catch (error) {
      if ((await interfaceChanged()) && reloadIfAllowed()) return new Promise(() => {}) // la page se recharge
      throw error
    }
  }

  const detail = (error: unknown): string => (error instanceof Error ? error.message : String(error))
</script>

{#await attempt()}
  <!-- Le chargement prend d'ordinaire quelques dizaines de ms : le mot n'apparaît que si ça traîne. -->
  <p class="later dim" data-testid="page-loading">Chargement…</p>
{:then module}
  {@const Page = module.default}
  <!-- Une page qui plante sur des données inattendues ne doit pas vider l'écran : l'erreur est dite. -->
  <svelte:boundary>
    <Page />
    {#snippet failed(error, reset)}
      <section class="broken" data-testid="page-crashed">
        <TriangleAlert size={44} aria-hidden="true" />
        <h1>Cette page a rencontré une erreur</h1>
        <p class="muted">Les autres pages ne sont pas touchées. Réessaie ; si ça recommence, le message ci-dessous dit pourquoi.</p>
        <button class="btn primary big" data-testid="page-retry" onclick={reset}>
          <RefreshCw size={28} aria-hidden="true" /> Réessayer
        </button>
        <p class="dim small mono" data-testid="page-crashed-detail">{detail(error)}</p>
      </section>
    {/snippet}
  </svelte:boundary>
{:catch error}
  <section class="broken" data-testid="page-failed">
    <TriangleAlert size={44} aria-hidden="true" />
    <h1>Cette page n'a pas pu être chargée</h1>
    <p class="muted">L'interface vient peut-être d'être mise à jour : recharge l'écran.</p>
    <button class="btn primary big" data-testid="page-reload" onclick={() => location.reload()}>
      <RefreshCw size={28} aria-hidden="true" /> Recharger
    </button>
    <p class="dim small mono" data-testid="page-failed-detail">{detail(error)}</p>
  </section>
{/await}

<style>
  .later {
    margin: 0;
    padding-top: 24px;
    text-align: center;
    opacity: 0;
    animation: appear 0.01s 450ms forwards;
  }
  @keyframes appear {
    to {
      opacity: 1;
    }
  }
  .broken {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    text-align: center;
    color: var(--warn);
  }
  .broken h1 {
    font-size: var(--fs-title);
  }
  .broken .muted {
    max-width: 640px;
  }
  .small {
    max-width: 800px;
    font-size: var(--fs-cap);
    overflow-wrap: anywhere;
  }
</style>
