<script lang="ts">
  import CircleAlert from '@lucide/svelte/icons/circle-alert'
  import CircleCheck from '@lucide/svelte/icons/circle-check'
  import PlugZap from '@lucide/svelte/icons/plug-zap'
  import { editText } from '../lib/keyboard.svelte'
  import { media } from '../lib/media.svelte'
  import type { MediaTestPart } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'

  // Réglages → Divertissement : où le dock prend les chaînes (la liste de la Freebox) et les films (un partage Samba).
  const info = $derived(sock.dock?.media)
  let busy = $state(false)
  let result = $state<{ tv: MediaTestPart; files: MediaTestPart; player: MediaTestPart } | null>(null)

  async function editUrl(): Promise<void> {
    const typed = await editText({ title: 'Liste des chaînes', hint: 'http://… : la liste M3U de la Freebox', kind: 'url', value: info?.playlist_url ?? 'http://' })
    if (typed !== null && (await media.saveSettings({ playlist_url: typed.trim() }))) notify('Liste enregistrée.', 'good')
  }

  async function editHost(): Promise<void> {
    const typed = await editText({ title: 'Serveur de fichiers', hint: 'nom ou adresse du serveur Samba (la Freebox)', kind: 'host', value: info?.smb_host ?? '' })
    if (typed !== null && (await media.saveSettings({ smb_host: typed.trim() }))) notify('Serveur enregistré.', 'good')
  }

  async function editUser(): Promise<void> {
    const typed = await editText({ title: 'Identifiant du partage', hint: 'laisse vide pour un accès libre', kind: 'text', value: info?.smb_user ?? '' })
    if (typed !== null && (await media.saveSettings({ smb_user: typed.trim() }))) notify(typed.trim() ? 'Identifiant enregistré.' : 'Accès libre.', 'good')
  }

  async function editPassword(): Promise<void> {
    const typed = await editText({ title: 'Mot de passe du partage', hint: 'gardé sur le Pi, jamais affiché', kind: 'password', secret: true })
    if (typed !== null && (await media.saveSettings({ smb_password: typed }))) notify('Mot de passe enregistré.', 'good')
  }

  async function clearPassword(): Promise<void> {
    if (await media.saveSettings({ smb_password: '' })) notify('Mot de passe effacé.', 'good')
  }

  async function test(): Promise<void> {
    busy = true
    result = null
    try {
      result = await media.test()
    } finally {
      busy = false
    }
  }
</script>

<section class="card" data-testid="settings-media">
  <h2>Divertissement</h2>
  <div class="setting">
    <span class="label">Chaînes</span>
    <span class="value mono" data-testid="media-url">{info?.playlist_url ?? '—'}</span>
    <button class="btn" data-testid="edit-media-url" onclick={editUrl}>Modifier</button>
  </div>
  <div class="setting">
    <span class="label">Fichiers</span>
    <span class="value mono" data-testid="media-host">{info?.smb_host || 'non défini'}</span>
    <button class="btn" data-testid="edit-media-host" onclick={editHost}>Modifier</button>
  </div>
  <div class="setting">
    <span class="label">Identifiant</span>
    <span class="value mono" data-testid="media-user">{info?.smb_user || 'accès libre'}</span>
    <button class="btn" data-testid="edit-media-user" onclick={editUser}>Modifier</button>
  </div>
  <div class="setting">
    <span class="label">Mot de passe</span>
    <span class="value mono" data-testid="media-password">{info?.smb_password_set ? '••••••••' : 'aucun'}</span>
    {#if info?.smb_password_set}<button class="btn" data-testid="clear-media-password" onclick={clearPassword}>Effacer</button>{/if}
    <button class="btn" data-testid="edit-media-password" onclick={editPassword}>{info?.smb_password_set ? 'Remplacer' : 'Définir'}</button>
  </div>
  {#if info && !info.smbclient}
    <p class="warn small" data-testid="media-no-smbclient">smbclient manque sur le Pi : le Kiosque ne pourra pas lister le partage (sudo apt install smbclient).</p>
  {/if}
  <div class="test">
    <button class="btn primary" data-testid="media-test" disabled={busy} onclick={test}>
      <PlugZap size={36} aria-hidden="true" />{busy ? 'Test en cours…' : 'Tester Freetv et Kiosque'}
    </button>
  </div>
  {#if result}
    <ul class="results" data-testid="media-results">
      {#each [['tv', 'Chaînes', result.tv], ['files', 'Fichiers', result.files], ['player', 'Lecteur', result.player]] as const as [key, label, part] (key)}
        <li class:ok={part.ok} class:ko={!part.ok} data-testid="media-result-{key}">
          {#if part.ok}<CircleCheck size={30} />{:else}<CircleAlert size={30} />{/if}
          <span><strong>{label}</strong> · {part.text}</span>
        </li>
      {/each}
    </ul>
  {/if}
  <p class="dim small">Freetv : la liste M3U de la Freebox. Kiosque : un partage Samba (Windows) de la Freebox. Les vidéos se lisent avec VLC sur cet écran.</p>
</section>

<style>
  .setting {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 12px;
    min-height: calc(var(--tap) + 4px);
  }
  .label {
    color: var(--text-3);
    font-size: var(--fs-cap);
    min-width: 7.5ch;
  }
  .value {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-small);
  }
  .test {
    margin-top: 10px;
  }
  .results {
    list-style: none;
    margin: 12px 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-size: var(--fs-small);
  }
  .results li {
    display: flex;
    align-items: flex-start;
    gap: 10px;
  }
  .results li.ok {
    color: var(--good);
  }
  .results li.ko {
    color: var(--warn);
  }
  .small {
    font-size: var(--fs-cap);
    margin-top: 10px;
  }
  .warn {
    color: var(--warn);
  }
</style>
