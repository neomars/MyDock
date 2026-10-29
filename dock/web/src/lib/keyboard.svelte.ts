// État du clavier tactile intégré : une seule saisie à la fois, retournée par une promesse.

import type { KeyboardKind } from './keyboardLayout'

export interface EditOptions {
  title: string
  value?: string
  kind?: KeyboardKind
  hint?: string
  /** Masque la saisie (jeton) avec un bouton « afficher ». */
  secret?: boolean
}

interface KeyboardState {
  open: boolean
  title: string
  hint: string
  kind: KeyboardKind
  secret: boolean
  reveal: boolean
  shift: boolean
  value: string
}

export const keyboard = $state<KeyboardState>({
  open: false,
  title: '',
  hint: '',
  kind: 'text',
  secret: false,
  reveal: false,
  shift: false,
  value: '',
})

let resolver: ((value: string | null) => void) | null = null

/** Ouvre le clavier ; retourne le texte validé, ou `null` si l'utilisateur annule. */
export function editText(options: EditOptions): Promise<string | null> {
  finish(null)
  keyboard.title = options.title
  keyboard.hint = options.hint ?? ''
  keyboard.kind = options.kind ?? 'text'
  keyboard.secret = options.secret ?? false
  keyboard.reveal = false
  keyboard.shift = false
  keyboard.value = options.value ?? ''
  keyboard.open = true
  return new Promise<string | null>((resolve) => {
    resolver = resolve
  })
}

export function finish(result: string | null): void {
  keyboard.open = false
  const resolve = resolver
  resolver = null
  resolve?.(result)
}
