<script lang="ts">
  import CircleAlert from '@lucide/svelte/icons/circle-alert'
  import CircleCheck from '@lucide/svelte/icons/circle-check'
  import Gauge from '@lucide/svelte/icons/gauge'
  import PlugZap from '@lucide/svelte/icons/plug-zap'
  import PageHeader from '../components/PageHeader.svelte'
  import { editText } from '../lib/keyboard.svelte'
  import { formatMs, ifaceKindLabel, type LatencyStats } from '../lib/format'
  import { MAX_PINNED } from '../lib/launcher'
  import { MAX_WATCHED, REFRESH_OPTIONS } from '../lib/markets'
  import { marketsUi } from '../lib/markets.svelte'
  import { pageRows, pagesSummary } from '../lib/pages'
  import { pagesUi } from '../lib/pagesEditor.svelte'
  import { picker } from '../lib/picker.svelte'
  import { SLEEP_OPTIONS, sleepPhrase } from '../lib/screen'
  import { screenSleep } from '../lib/screen.svelte'
  import type { Ack, MarketRefresh } from '../lib/protocol'
  import { sock } from '../lib/socket.svelte'
  import { notify } from '../lib/toast.svelte'

  const dock = $derived(sock.dock)
  const link = $derived(sock.link)
  const pagesLine = $derived(pagesSummary(pageRows(sock.ui?.order ?? [], sock.ui?.hidden ?? [])))

  /** Affiche le résultat d'une modification : succès en vert, sinon le message d'erreur du dock. */
  function report(ack: Ack, done: string): boolean {
    if (ack.ok) notify(done, 'good')
    else notify(ack.e?.msg ?? 'La modification a échoué.', 'bad', 8_000)
    return ack.ok
  }

  async function setRefresh(refresh: MarketRefresh): Promise<void> {
    const ack = await sock.act('dock.markets.set', { refresh })
    if (!ack.ok) notify(ack.e?.msg ?? 'La modification a échoué.', 'bad', 8_000)
  }

  async function setSleep(seconds: number): Promise<void> {
    const ack = await sock.act('dock.ui.set', { screen_sleep: seconds })
    if (!ack.ok) notify(ack.e?.msg ?? 'La modification a échoué.', 'bad', 8_000)
  }

  async function editHost(): Promise<void> {
    const value = await editText({
      title: 'Adresse du PC',
      hint: 'Nom (ubuntu.local) ou IPv4',
      kind: 'host',
      value: dock?.pc.host ?? '',
    })
    if (value !== null) {
      report(await sock.act('dock.pc.set', { host: value, port: dock?.pc.port ?? 8765 }), 'Adresse enregistrée.')
    }
  }

  async function editPort(): Promise<void> {
    const value = await editText({
      title: "Port de l'agent",
      hint: 'Par défaut 8765',
      kind: 'number',
      value: String(dock?.pc.port ?? 8765),
    })
    if (value !== null) {
      report(
        await sock.act('dock.pc.set', { host: dock?.pc.host ?? '', port: Number.parseInt(value, 10) || 0 }),
        'Port enregistré.',
      )
    }
  }

  async function editToken(): Promise<void> {
    const value = await editText({
      title: "Jeton de l'agent",
      hint: '43 caractères, à copier depuis le PC',
      kind: 'token',
      secret: true,
    })
    if (value !== null) report(await sock.act('dock.token.set', { token: value }), 'Jeton enregistré.')
  }

  async function editMac(): Promise<void> {
    const value = await editText({
      title: 'Adresse MAC du PC',
      hint: 'Exemple : 02:00:5e:10:00:01',
      kind: 'mac',
      value: dock?.wol.mac ?? '',
    })
    if (value !== null) {
      report(await sock.act('dock.wol.set_mac', { mac: value || null }), 'Adresse MAC enregistrée.')
    }
  }

  async function autoMac(): Promise<void> {
    report(await sock.act('dock.wol.set_mac', { mac: null }), 'Adresse MAC automatique.')
  }

  // --- tester la connexion ---------------------------------------------------------------------

  let test = $state<{ busy: boolean; ok?: boolean; text?: string }>({ busy: false })

  async function testConnection(): Promise<void> {
    test = { busy: true }
    const ack = await sock.act('dock.pc.test', {}, { timeoutMs: 20_000 })
    if (!ack.ok) {
      test = { busy: false, ok: false, text: ack.e?.msg ?? 'Test impossible.' }
    } else if (ack.r.state === 'connected') {
      test = { busy: false, ok: true, text: `Connexion réussie (agent ${ack.r.agent?.version ?? '?'}).` }
    } else {
      test = { busy: false, ok: false, text: ack.r.detail }
    }
  }

  // --- latence ---------------------------------------------------------------------------------

  let latency = $state<{ busy: boolean; dock?: LatencyStats | null; pc?: LatencyStats | null; done?: boolean }>({
    busy: false,
  })

  async function measureLatency(): Promise<void> {
    latency = { busy: true }
    const toDock = await sock.measure('dock.ping', 20)
    const toPc = sock.connected ? await sock.measure('diag.ping', 20) : null
    latency = { busy: false, dock: toDock, pc: toPc, done: true }
  }

  async function tryConfirmation(): Promise<void> {
    const ack = await sock.call('diag.confirm_demo')
    if (ack.cancelled) notify('Confirmation annulée : rien n’a été exécuté.', 'info')
    else report(ack, 'Action confirmée et exécutée (démo, sans effet).')
  }
</script>

<PageHeader title="Réglages" />

<div class="grid">
  <div class="col">
    <section class="card" data-testid="settings-pc">
      <h2>PC piloté</h2>
      <div class="setting">
        <span class="label">Adresse</span>
        <span class="value mono" data-testid="pc-host">{dock?.pc.host || 'non définie'}</span>
        <button class="btn" data-testid="edit-host" onclick={editHost}>Modifier</button>
      </div>
      <div class="setting">
        <span class="label">Port</span>
        <span class="value mono" data-testid="pc-port">{dock?.pc.port ?? '—'}</span>
        <button class="btn" data-testid="edit-port" onclick={editPort}>Modifier</button>
      </div>
      <div class="setting">
        <span class="label">Jeton</span>
        <span class="value mono" data-testid="pc-token">{dock?.pc.token_set ? '••••••••••••' : 'non défini'}</span>
        <button class="btn" data-testid="edit-token" onclick={editToken}>Remplacer</button>
      </div>
      {#if link?.resolved}
        <p class="dim small" data-testid="pc-resolved">
          {link.state === 'connected' ? 'Connecté à' : 'Dernière adresse essayée :'}
          <span class="mono">{link.resolved}:{link.port}</span>
        </p>
      {/if}
      <div class="test">
        <button class="btn primary" data-testid="test-connection" disabled={test.busy} onclick={testConnection}>
          <PlugZap size={26} aria-hidden="true" />
          {test.busy ? 'Test en cours…' : 'Tester la connexion'}
        </button>
        {#if test.text}
          <span class="result" class:ok={test.ok} class:ko={!test.ok} data-testid="test-result">
            {#if test.ok}<CircleCheck size={24} />{:else}<CircleAlert size={24} />{/if}
            {test.text}
          </span>
        {/if}
      </div>
    </section>

    <section class="card" data-testid="settings-wol">
      <h2>Réveil du PC (Wake-on-LAN)</h2>
      <div class="setting">
        <span class="label">Adresse MAC</span>
        <span class="value mono" data-testid="wol-mac-setting">{dock?.wol.mac ?? 'inconnue'}</span>
        <button class="btn" data-testid="edit-mac" onclick={editMac}>Modifier</button>
      </div>
      <p class="dim small">
        {#if dock?.wol.source === 'manual'}
          Saisie à la main.
          <button class="link" data-testid="auto-mac" onclick={autoMac}>Revenir à l'automatique</button>
        {:else if dock?.wol.source === 'auto'}
          Apprise automatiquement ({ifaceKindLabel(dock.wol.kind ?? '')}{dock.wol.iface ? ` ${dock.wol.iface}` : ''}) quand le PC est allumé.
        {:else}
          Le dock la mémorise dès qu'il a vu le PC allumé.
        {/if}
      </p>
      {#if dock?.wol.kind === 'wifi'}
        <p class="warn small">Wi-Fi : réveiller un PC éteint par Wi-Fi ne marche en général pas.</p>
      {/if}
    </section>

    <section class="card" data-testid="settings-screen">
      <h2>Écran du dock</h2>
      <div class="pace compact" role="group" aria-label="Extinction de l'écran après inactivité">
        <span class="dim small">Éteindre l'écran après</span>
        {#each SLEEP_OPTIONS as option (option.id)}
          <button
            class="seg"
            class:on={(sock.ui?.screen_sleep ?? 0) === option.id}
            aria-pressed={(sock.ui?.screen_sleep ?? 0) === option.id}
            disabled={!sock.ui}
            data-testid="sleep-{option.id}"
            onclick={() => setSleep(option.id)}
          >
            {option.label}
          </button>
        {/each}
      </div>
      <p class="dim small" data-testid="screen-note">
        {#if (sock.ui?.screen_sleep ?? 0) === 0}
          L'écran reste allumé.
        {:else}
          Sans toucher pendant {sleepPhrase(sock.ui?.screen_sleep ?? 0)}, l'écran s'éteint ; le premier toucher le rallume sans rien déclencher.
        {/if}
      </p>
      <button class="btn trial" data-testid="sleep-now" onclick={() => screenSleep.sleepNow()}>Éteindre maintenant (essai)</button>
      <p class="dim small">L'essai noircit seulement l'image : c'est le Pi (swayidle) qui coupe le rétroéclairage, après le délai.</p>
    </section>
  </div>

  <div class="col">
    <section class="card" data-testid="settings-pages">
      <h2>Pages du dock</h2>
      <p data-testid="pages-count">{pagesLine}</p>
      <button class="btn" data-testid="choose-pages" onclick={() => pagesUi.edit()}>
        Choisir les pages et leur ordre…
      </button>
    </section>

    <section class="card" data-testid="settings-launcher">
      <h2>Lanceur d'applications</h2>
      <p data-testid="launcher-count">
        {#if (sock.ui?.pinned_apps.length ?? 0) === 0}
          Aucune application choisie pour la page Applis.
        {:else}
          {sock.ui?.pinned_apps.length} application{(sock.ui?.pinned_apps.length ?? 0) > 1 ? 's' : ''} sur {MAX_PINNED} au plus, dans l'ordre que tu as choisi.
        {/if}
      </p>
      <button class="btn" data-testid="choose-apps" disabled={!sock.connected} onclick={() => picker.show()}>
        Choisir les applications…
      </button>
      {#if !sock.connected}<p class="dim small">Le PC doit être allumé : c'est lui qui connaît ses applications.</p>{/if}
    </section>

    <section class="card" data-testid="settings-markets">
      <h2>Bourse</h2>
      <p data-testid="markets-count">
        {#if !dock?.markets}
          Lecture de la liste…
        {:else if dock.markets.count === 0}
          Aucune valeur suivie.
        {:else}
          {dock.markets.count} valeur{dock.markets.count > 1 ? 's' : ''} suivie{dock.markets.count > 1 ? 's' : ''} sur {MAX_WATCHED} au plus, cryptos cotées en {dock.markets.currency === 'USD' ? 'dollars' : 'euros'}.
        {/if}
      </p>
      <button class="btn" data-testid="choose-markets" onclick={() => marketsUi.edit()}>Choisir les valeurs…</button>
      <div class="pace" role="group" aria-label="Fréquence des mises à jour">
        <span class="dim small">Mise à jour des cryptos toutes les</span>
        {#each REFRESH_OPTIONS as option (option.id)}
          <button
            class="seg"
            class:on={dock?.markets?.refresh === option.id}
            aria-pressed={dock?.markets?.refresh === option.id}
            disabled={!dock?.markets}
            data-testid="refresh-{option.id}"
            onclick={() => setRefresh(option.id)}
          >
            {option.label}
          </button>
        {/each}
      </div>
      <p class="dim small" data-testid="markets-note">
        Le Pi lit lui-même les cotations (Yahoo Finance et Binance, sans clé) : la Bourse marche PC éteint.
        {#if dock?.markets?.demo}Mode démonstration : cotations calculées, sans réseau.
        {:else if dock?.markets}Clé Finnhub de secours : {dock.markets.finnhub ? 'configurée' : 'aucune (facultative)'}.{/if}
      </p>
    </section>

    <section class="card" data-testid="settings-latency">
      <h2>Latence appui → PC</h2>
      <button class="btn" data-testid="measure-latency" disabled={latency.busy} onclick={measureLatency}>
        <Gauge size={26} aria-hidden="true" />
        {latency.busy ? 'Mesure en cours…' : 'Mesurer (20 essais)'}
      </button>
      {#if latency.done}
        <dl class="stack" data-testid="latency-result">
          <dt>Navigateur → dock</dt>
          <dd>
            {#if latency.dock}médiane {formatMs(latency.dock.median)} · p95 {formatMs(latency.dock.p95)}{:else}—{/if}
          </dd>
          <dt>Navigateur → PC</dt>
          <dd>
            {#if latency.pc}
              médiane {formatMs(latency.pc.median)} · p95 {formatMs(latency.pc.p95)}
              <span class="chip" class:good={latency.pc.p95 < 50} class:warn={latency.pc.p95 >= 50}>
                {latency.pc.p95 < 50 ? 'sous 50 ms' : 'au-dessus de 50 ms'}
              </span>
            {:else}PC non connecté{/if}
          </dd>
        </dl>
      {/if}
    </section>

    <section class="card" data-testid="settings-about">
      <h2>À propos</h2>
      <dl>
        <dt>Dock</dt>
        <dd>version {dock?.version ?? '—'} · protocole {dock?.proto ?? '—'}</dd>
        <dt>Cet appareil</dt>
        <dd class="mono">{dock?.hostname ?? '—'}</dd>
        <dt>Adresse du Pi</dt>
        <dd class="mono" data-testid="pi-ips">{dock?.ips?.length ? dock.ips.join(' · ') : '—'}</dd>
        <dt>Agent</dt>
        <dd>{link?.agent ? `version ${link.agent.version}` : 'non connecté'}</dd>
        <dt>Liaison</dt>
        <dd>{link?.state ?? '—'}</dd>
      </dl>
      {#if link?.agent?.demo}
        <button class="btn" data-testid="try-confirm" onclick={tryConfirmation}>Tester la confirmation (démo)</button>
      {/if}
      <p class="dim small">Ajoute <span class="mono">?debug=1</span> à l'adresse pour afficher les images par seconde.</p>
    </section>
  </div>
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: 1.15fr 1fr;
    gap: 16px;
    align-items: start;
  }
  .col {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .setting {
    display: grid;
    grid-template-columns: 100px 1fr auto;
    align-items: center;
    gap: 12px;
    min-height: 64px;
  }
  .setting + .setting {
    border-top: 1px solid var(--line);
  }
  .setting .btn {
    min-width: 156px;
  }
  .label {
    color: var(--text-3);
    font-size: var(--fs-small);
  }
  .value {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .test {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin-top: 10px;
    padding-top: 14px;
    border-top: 1px solid var(--line);
  }
  .result {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-size: var(--fs-small);
  }
  .result.ok {
    color: var(--good);
  }
  .result.ko {
    color: var(--bad);
  }
  .small {
    font-size: var(--fs-small);
    margin-top: 6px;
  }
  .pace {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-top: 12px;
  }
  .pace .small {
    flex-basis: 100%;
    margin: 0;
  }
  .seg {
    height: 48px;
    min-width: 76px;
    padding: 0 16px;
    border-radius: 12px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    font-weight: 600;
    text-align: center;
  }
  .pace.compact {
    gap: 6px;
  }
  .pace.compact .seg {
    min-width: 0;
    padding: 0 10px;
  }
  .trial {
    margin-top: 12px;
  }
  .seg.on {
    background: var(--accent-soft);
    border-color: var(--accent);
    color: var(--accent);
  }
  .seg:disabled {
    opacity: 0.4;
  }
  .warn {
    color: var(--warn);
  }
  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 6px 16px;
    margin: 12px 0;
  }
  dl.stack {
    grid-template-columns: 1fr;
    gap: 0;
  }
  dl.stack dd {
    margin-bottom: 10px;
  }
  dt {
    color: var(--text-3);
    font-size: var(--fs-small);
  }
  dd {
    margin: 0;
  }
  .link {
    color: var(--accent);
    font-weight: 600;
    padding: 12px 0;
  }
</style>
