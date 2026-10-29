<script lang="ts">
  import type { PageInfo } from '../lib/pages'

  let { page }: { page: PageInfo } = $props()
  const Icon = $derived(page.icon)
  const STAGES: Record<PageInfo['stage'], string> = {
    a: 'a',
    b: 'b (Système et Musique)',
    c: 'c (Son)',
    d: 'd (Lanceur, Steam, LibreOffice)',
    e: 'e (Bourse)',
    f: 'f (Alimentation et finitions)',
  }
</script>

<section class="soon" data-testid="soon">
  <Icon size={72} strokeWidth={1.5} aria-hidden="true" />
  <h1>{page.title}</h1>
  <p class="muted">{page.blurb}</p>
  <p class="chip accent">Cette page arrive à l'étape {STAGES[page.stage]}</p>
</section>

<style>
  .soon {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    text-align: center;
    color: var(--text-3);
  }
  h1 {
    font-size: var(--fs-title);
    font-weight: 700;
    color: var(--text);
  }
  p.muted {
    max-width: 620px;
  }
</style>
