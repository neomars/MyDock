<script lang="ts">
  import Minus from '@lucide/svelte/icons/minus'
  import Plus from '@lucide/svelte/icons/plus'
  import { editText } from '../lib/keyboard.svelte'
  import { gameCount, gpioLabel, PHYSICAL_PIN, stepGpio } from '../lib/retro'
  import { retro } from '../lib/retro.svelte'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'

  // Réglages → Retrogame : le dossier des ROMs, la broche du bouton physique (qui quitte le jeu), et ce que l'écran en sait.
  const info = $derived(sock.dock?.retro)
  const data = $derived(sock.retro)
  const pin = $derived(info?.button_gpio ?? 17)
  const report = $derived(sock.player?.helper?.gpio ?? null)

  async function editFolder(): Promise<void> {
    const typed = await editText({
      title: 'Dossier des jeux',
      hint: 'chemin complet ; vide : /var/lib/mydock/roms',
      kind: 'text',
      value: info?.roms_dir_custom ? info.roms_dir : '',
    })
    if (typed !== null && (await retro.saveSettings({ roms_dir: typed.trim() }))) notify('Dossier enregistré.', 'good')
  }

  const withoutCode = $derived(info?.upload_without_code ?? true)

  async function toggleUpload(): Promise<void> {
    const next = !withoutCode
    if (await retro.saveSettings({ upload_without_code: next })) notify(next ? 'Le PC associé envoie sans code.' : 'Le code de l’écran est exigé pour tous.', 'good')
  }

  async function step(direction: 1 | -1): Promise<void> {
    const next = stepGpio(pin, direction)
    if (next !== pin && (await retro.saveSettings({ button_gpio: next }))) notify(`Bouton sur ${gpioLabel(next)}.`, 'good')
  }
</script>

<section class="card" data-testid="settings-retro">
  <h2>Retrogame</h2>
  <div class="setting">
    <span class="label">Jeux</span>
    <span class="value mono" data-testid="retro-dir">{info?.roms_dir ?? '—'}</span>
    <button class="btn" data-testid="edit-retro-dir" onclick={editFolder}>Modifier</button>
  </div>
  <p class="dim small" data-testid="retro-summary">
    {#if data}{data.mame ? gameCount(data.games.length) : 'MAME est absent du Pi (sudo apt install mame)'}{#if data.mame && !data.readable} · dossier illisible{/if}{:else}…{/if}
    <button class="btn inline" data-testid="retro-refresh" disabled={data?.state === 'scanning'} onclick={() => retro.refresh()}>Actualiser</button>
  </p>
  <div class="setting">
    <span class="label">Bouton</span>
    <span class="value mono" data-testid="retro-pin">{gpioLabel(pin)}</span>
    <button class="btn icon" aria-label="Broche précédente" data-testid="retro-pin-down" onclick={() => step(-1)}><Minus size={30} aria-hidden="true" /></button>
    <button class="btn icon" aria-label="Broche suivante" data-testid="retro-pin-up" onclick={() => step(1)}><Plus size={30} aria-hidden="true" /></button>
  </div>
  <p class="dim small" data-testid="retro-wiring">
    Un bouton entre la broche {PHYSICAL_PIN[pin] ?? '?'} ({gpioLabel(pin).split(' ')[0]}) et une masse (broche 9, 14 ou 20…), sans résistance : un appui quitte le jeu ou rallume l’écran.
  </p>
  <div class="setting">
    <span class="label">Envoi</span>
    <span class="value" data-testid="retro-upload-mode">{withoutCode ? 'sans code depuis le PC associé' : 'avec le code de l’écran'}</span>
    <button class="btn toggle" class:on={withoutCode} aria-pressed={withoutCode} data-testid="retro-upload-toggle" onclick={toggleUpload}>{withoutCode ? 'Sans code' : 'Avec code'}</button>
  </div>
  <p class="dim small" data-testid="retro-upload-help">
    Depuis le PC associé, la page <span class="mono">/jeux</span> du dock (lien sur sa page d’installation) reçoit les jeux sans rien demander ; un autre appareil recopie le code affiché sur cet écran. « Avec code » : tout le monde le recopie.
  </p>
  <p class="small" class:dim={!report} data-testid="retro-gpio-report">{report ?? 'L’assistant de l’écran n’a pas encore dit si le bouton est prêt.'}</p>
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
  .small {
    font-size: var(--fs-cap);
    margin: 8px 0 0;
  }
  .btn.toggle {
    min-width: 170px;
  }
  .btn.toggle.on {
    background: var(--accent);
    color: var(--accent-ink);
  }
  .btn.inline {
    margin-left: 10px;
    min-height: calc(var(--tap) - 12px);
  }
</style>
