<script lang="ts">
  import CircleAlert from '@lucide/svelte/icons/circle-alert'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import Terminal from '@lucide/svelte/icons/terminal'
  import { accountView } from '../lib/spotify'
  import { spotify } from '../lib/spotify.svelte'
  import SpotifyNotice from './SpotifyNotice.svelte'

  // Ce que montrent la recherche et la bibliothèque tant que le compte Spotify n'est pas utilisable.
  const view = $derived(accountView(spotify.status))
</script>

{#if spotify.statusError}
  <SpotifyNotice icon={CircleAlert} title="Spotify est inaccessible" testid="sp-status-error" tone="bad">
    <p>{spotify.statusError.message}</p>
    <button class="again" data-testid="sp-status-retry" onclick={() => void spotify.refreshStatus()}>
      <RefreshCw size={32} aria-hidden="true" /> Réessayer
    </button>
  </SpotifyNotice>
{:else if view === 'loading'}
  <SpotifyNotice icon={RefreshCw} title="Vérification du compte…" testid="sp-account-loading" />
{:else if view === 'unconfigured'}
  <SpotifyNotice icon={Terminal} title="Connecte ton compte Spotify" testid="sp-connect">
    <p>Relie ton compte une seule fois, depuis un terminal du PC :</p>
    <code class="cmd" data-testid="sp-connect-command">mydock-agent spotify login</code>
    <p class="dim">Sans compte, la lecture, la pause et le volume marchent déjà.</p>
  </SpotifyNotice>
{:else if view === 'refused'}
  <SpotifyNotice icon={CircleAlert} title="Le compte Spotify est refusé" testid="sp-refused" tone="bad">
    <p data-testid="sp-refused-message">{spotify.status?.message ?? 'Spotify refuse la connexion de ce PC.'}</p>
    <code class="cmd">mydock-agent spotify login</code>
    <button class="again" data-testid="sp-status-retry" onclick={() => void spotify.refreshStatus()}>
      <RefreshCw size={32} aria-hidden="true" /> Vérifier à nouveau
    </button>
  </SpotifyNotice>
{/if}

<style>
  .cmd {
    display: block;
    padding: 12px 22px;
    border-radius: var(--radius-s);
    background: var(--surface-2);
    border: 1px solid var(--line);
    color: var(--text);
    font-family: ui-monospace, 'DejaVu Sans Mono', monospace;
    font-size: var(--fs-small);
    user-select: text;
  }
  .again {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    min-height: var(--tap);
    padding: 0 26px;
    border-radius: 14px;
    background: var(--surface-3);
    font-weight: 600;
  }
  .again:active {
    background: var(--line);
    transform: scale(0.97);
  }
</style>
