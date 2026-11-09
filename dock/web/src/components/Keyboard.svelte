<script lang="ts">
  import ArrowBigUp from '@lucide/svelte/icons/arrow-big-up'
  import Check from '@lucide/svelte/icons/check'
  import Delete from '@lucide/svelte/icons/delete'
  import Eye from '@lucide/svelte/icons/eye'
  import EyeOff from '@lucide/svelte/icons/eye-off'
  import { finish, keyboard } from '../lib/keyboard.svelte'
  import {
    DIGITS,
    LETTER_ROWS,
    symbolsFor,
    backspace,
    isKeyAllowed,
    supportsUppercase,
    typeChar,
  } from '../lib/keyboardLayout'

  const showCase = (key: string) =>
    keyboard.shift && supportsUppercase(keyboard.kind) ? key.toUpperCase() : key

  function press(key: string): void {
    keyboard.value = typeChar(keyboard.value, key, keyboard.kind, keyboard.shift)
    // Majuscule « une fois » : retombe après une lettre, comme sur un téléphone.
    if (keyboard.shift) keyboard.shift = false
  }

  // Touches pressées à l'appui (pas au relâchement) : la frappe suit le doigt sans délai.
  function down(handler: () => void) {
    return (event: PointerEvent) => {
      event.preventDefault()
      handler()
    }
  }

  // Effacement répété en maintenant la touche.
  let repeatDelay: ReturnType<typeof setTimeout> | undefined
  let repeatTimer: ReturnType<typeof setInterval> | undefined
  function stopRepeat(): void {
    clearTimeout(repeatDelay)
    clearInterval(repeatTimer)
  }
  function startBackspace(event: PointerEvent): void {
    event.preventDefault()
    const erase = () => (keyboard.value = backspace(keyboard.value, keyboard.kind))
    erase()
    repeatDelay = setTimeout(() => (repeatTimer = setInterval(erase, 70)), 420)
  }

  // Confort de développement : le clavier physique marche aussi.
  function onKeydown(event: KeyboardEvent): void {
    if (!keyboard.open) return
    if (event.key === 'Enter') finish(keyboard.value)
    else if (event.key === 'Escape') finish(null)
    else if (event.key === 'Backspace') keyboard.value = backspace(keyboard.value, keyboard.kind)
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
      keyboard.value = typeChar(keyboard.value, event.key, keyboard.kind, false)
    } else return
    event.preventDefault()
  }

  const masked = $derived(keyboard.secret && !keyboard.reveal)
  const shown = $derived(masked ? '•'.repeat(keyboard.value.length) : keyboard.value)
  const usable = (key: string) => isKeyAllowed(keyboard.kind, key)

  // Un texte plus long que le champ (le jeton fait 43 caractères) : on montre toujours la fin, là où le curseur écrit.
  let fieldEl = $state<HTMLElement>()
  $effect(() => {
    void shown
    if (fieldEl) fieldEl.scrollLeft = fieldEl.scrollWidth
  })
</script>

<svelte:window onkeydown={onKeydown} onpointerup={stopRepeat} onpointercancel={stopRepeat} />

{#if keyboard.open}
  <div class="scrim" role="presentation">
    <div class="osk" role="dialog" aria-modal="true" aria-label={keyboard.title} data-testid="keyboard">
      <div class="head">
        <div class="title">
          <strong>{keyboard.title}</strong>
          {#if keyboard.hint}<span class="dim">{keyboard.hint}</span>{/if}
        </div>
        <button class="btn" data-testid="keyboard-cancel" onclick={() => finish(null)}>Annuler</button>
        <button class="btn primary" data-testid="keyboard-ok" onclick={() => finish(keyboard.value)}>
          <Check size={34} aria-hidden="true" /> Valider
        </button>
      </div>

      <div class="entry">
        <div class="field mono" data-testid="keyboard-value" bind:this={fieldEl}>
          <span class="text">{shown}</span><span class="caret"></span>
        </div>
        {#if keyboard.secret}
          <button
            class="btn icon"
            aria-label={keyboard.reveal ? 'Masquer' : 'Afficher'}
            onclick={() => (keyboard.reveal = !keyboard.reveal)}
          >
            {#if keyboard.reveal}<EyeOff size={36} />{:else}<Eye size={36} />{/if}
          </button>
        {/if}
      </div>

      <div class="keys">
        <div class="row">
          {#each DIGITS as key (key)}
            <button class="key" disabled={!usable(key)} onpointerdown={down(() => press(key))}>{key}</button>
          {/each}
        </div>
        {#each LETTER_ROWS as letters, index (index)}
          <div class="row">
            {#if index === 2}
              <button
                class="key wide"
                class:active={keyboard.shift}
                disabled={!supportsUppercase(keyboard.kind)}
                aria-label="Majuscule"
                aria-pressed={keyboard.shift}
                onpointerdown={down(() => (keyboard.shift = !keyboard.shift))}
              >
                <ArrowBigUp size={38} />
              </button>
            {/if}
            {#each letters as key (key)}
              <button class="key" disabled={!usable(key)} onpointerdown={down(() => press(key))}>
                {showCase(key)}
              </button>
            {/each}
            {#if index === 2}
              <button class="key wide" aria-label="Effacer" onpointerdown={startBackspace}>
                <Delete size={38} />
              </button>
            {/if}
          </div>
        {/each}
        <div class="row">
          {#each symbolsFor(keyboard.kind) as key (key)}
            <button class="key" disabled={!usable(key)} onpointerdown={down(() => press(key))}>{key}</button>
          {/each}
          <button
            class="key space"
            disabled={!usable(' ')}
            aria-label="Espace"
            onpointerdown={down(() => press(' '))}
          ></button>
        </div>
      </div>
    </div>
  </div>
{/if}

<style>
  /* Texte doublé : une barre (titre, Annuler, Valider), le champ sur toute la largeur, puis cinq rangées de touches. */
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 45;
    display: flex;
    align-items: flex-end;
    background: rgb(0 0 0 / 0.5);
  }
  .osk {
    width: 100%;
    padding: 8px 12px 10px;
    background: var(--surface);
    border-top: 1px solid var(--line);
  }
  .head {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }
  .title {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    font-size: var(--fs-small);
    line-height: 1.15;
  }
  .title strong,
  .title .dim {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title .dim {
    font-size: calc(13px * var(--ts));
  }
  .head .btn {
    padding: 0 22px;
  }
  .entry {
    display: flex;
    gap: 10px;
    margin-bottom: 8px;
  }
  .field {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    height: 64px;
    padding: 0 16px;
    background: var(--bg);
    border: 2px solid var(--accent);
    border-radius: 12px;
    font-size: calc(18px * var(--ts));
    overflow: hidden;
  }
  .text {
    flex: none;
    white-space: pre;
  }
  .caret {
    flex: none;
    width: 3px;
    height: 44px;
    margin-left: 2px;
    background: var(--accent);
  }
  .entry .btn.icon {
    padding: 0;
    width: 76px;
    height: 64px;
  }
  .keys {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .row {
    display: flex;
    gap: 6px;
  }
  .key {
    flex: 1 1 0;
    min-width: 0;
    height: 58px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 10px;
    background: var(--surface-3);
    font-size: calc(17px * var(--ts));
    font-weight: 500;
  }
  .key:active:not(:disabled) {
    background: var(--accent);
    color: var(--accent-ink);
  }
  .key:disabled {
    opacity: 0.22;
  }
  .key.wide {
    flex: 1.5 1 0;
    background: var(--surface-2);
  }
  .key.active {
    background: var(--accent);
    color: var(--accent-ink);
  }
  .key.space {
    flex: 6 1 0;
  }
</style>
