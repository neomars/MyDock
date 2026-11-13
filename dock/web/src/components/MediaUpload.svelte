<script lang="ts">
  import Upload from '@lucide/svelte/icons/upload'
  import { onMount } from 'svelte'
  import { countdown, uploadAddress, uploadResultText, uploadSecondsLeft } from '../lib/retro'
  import { retro } from '../lib/retro.svelte'
  import { sock } from '../lib/socket.svelte'

  // Envoyer des jeux depuis le PC : l'écran du dock affiche un code ; la page /jeux du portail (sur le PC) le demande, puis
  // accepte des fichiers .zip ou .7z. Le code prouve qu'on est devant le dock : sans lui, rien ne s'écrit sur le Pi.
  let { onDone }: { onDone: () => void } = $props()

  const info = $derived(sock.dock?.retro)
  const upload = $derived(info?.upload)
  const address = $derived(uploadAddress(upload, sock.dock?.hostname))
  const result = $derived(uploadResultText(upload))
  let failed = $state(false)
  let now = $state(Date.now())
  $effect(() => {
    void (upload?.open ? upload.expires_at : 0)
    now = Date.now()
  })

  onMount(() => {
    void retro.openUpload().then((ok) => (failed = !ok))
    const timer = setInterval(() => (now = Date.now()), 500)
    return () => {
      clearInterval(timer)
      void retro.closeUpload() // quitter l'écran ferme la session : le code n'est plus bon
    }
  })
  const left = $derived(uploadSecondsLeft(upload, now))
</script>

{#if upload?.open}
  <section class="card" data-testid="upload-open">
    <p class="step">1. Sur ton PC, ouvre cette page :</p>
    <p class="addr mono" data-testid="upload-address">{address}</p>
    <p class="step codeline">2. Recopie ce code : <span class="code mono" data-testid="upload-code">{upload.code}</span></p>
    <p class="step small">3. Dépose des fichiers <strong>.zip</strong> ou <strong>.7z</strong> (ex. <span class="mono">pacman.zip</span>).</p>
    {#if info?.upload_without_code !== false}
      <p class="dim small" data-testid="upload-trusted-hint">Le PC associé n’a pas besoin du code : il envoie directement.</p>
    {/if}
    <div class="foot">
      <button class="btn primary" data-testid="upload-done" onclick={onDone}>Terminé</button>
      <p class="status dim" data-testid="upload-status">
        <Upload size={26} aria-hidden="true" />
        {upload.files === 0 ? 'Aucun jeu reçu' : upload.files === 1 ? '1 jeu reçu' : `${upload.files} jeux reçus`} · code valable
        <span class="mono" data-testid="upload-left">{countdown(left)}</span>
      </p>
    </div>
  </section>
{:else if failed}
  <section class="card problem" data-testid="upload-failed">
    <h2>L’envoi n’a pas pu s’ouvrir</h2>
    <p>Le portail du Pi (port 80) doit tourner : voir « mydock-dock doctor ».</p>
    <button class="btn" onclick={onDone}>Retour</button>
  </section>
{:else if result !== null}
  <section class="card" data-testid="upload-closed">
    <p data-testid="upload-result">{result}</p>
    <button class="btn primary" data-testid="upload-done" onclick={onDone}>Retour aux jeux</button>
  </section>
{/if}

<style>
  .card {
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }
  p {
    margin: 0;
  }
  .step {
    font-size: var(--fs-small);
  }
  .addr {
    font-size: var(--fs);
    color: var(--accent);
    overflow-wrap: anywhere;
  }
  .small {
    font-size: var(--fs-cap);
  }
  .codeline {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 6px 16px;
  }
  .code {
    font-size: calc(42px * var(--ts) * 0.7);
    font-weight: 700;
    letter-spacing: 0.12em;
    color: var(--text);
  }
  .foot {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-top: 4px;
  }
  .status {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: var(--fs-cap);
  }
  .problem {
    border-color: var(--warn);
  }
  .problem h2 {
    color: var(--warn);
    margin: 0;
  }
</style>
