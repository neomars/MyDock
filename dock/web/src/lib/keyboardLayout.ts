// Disposition du clavier tactile (AZERTY) et règles de saisie par type de champ.
// Fonctions pures, testées dans keyboardLayout.test.ts.

export type KeyboardKind = 'text' | 'host' | 'number' | 'token' | 'mac' | 'url' | 'password'

export const DIGITS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']
export const LETTER_ROWS = [
  ['a', 'z', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
  ['q', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm'],
  ['w', 'x', 'c', 'v', 'b', 'n'],
]
export const SYMBOLS = ['.', '-', '_', ':', '/', '@', '^', '=']
/** Les touches d'une adresse web : celles qui servent vraiment dans un chemin et ses paramètres. */
export const URL_SYMBOLS = ['.', '-', '_', ':', '/', '?', '=', '&', '%', '~']

/** Les symboles d'un mot de passe : ceux qu'on y met le plus (tout caractère visible reste accepté au clavier physique). */
export const PASSWORD_SYMBOLS = ['.', '-', '_', '!', '@', '#', '$', '%', '&', '*']

/** La rangée de symboles du clavier, selon ce qu'on saisit. */
export const symbolsFor = (kind: KeyboardKind): readonly string[] =>
  kind === 'url' ? URL_SYMBOLS : kind === 'password' ? PASSWORD_SYMBOLS : SYMBOLS

const LETTERS = /^[a-zA-Z]$/
const ALLOWED: Record<KeyboardKind, RegExp> = {
  text: /^[\S ]$/u,
  host: /^[a-zA-Z0-9.-]$/,
  number: /^[0-9]$/,
  token: /^[a-zA-Z0-9._~-]$/,
  mac: /^[0-9a-fA-F:-]$/,
  url: /^[a-zA-Z0-9._~:/?#@!$&'()*+,;=%-]$/,
  password: /^\S$/u,
}
const MAX_LENGTH: Record<KeyboardKind, number> = {
  text: 120,
  host: 253,
  number: 5,
  token: 256,
  mac: 17,
  url: 500,
  password: 128,
}

export const isKeyAllowed = (kind: KeyboardKind, char: string): boolean => ALLOWED[kind].test(char)

/** Majuscules : utiles seulement pour le texte libre et les jetons ; les noms d'hôte et les MAC non. */
export const supportsUppercase = (kind: KeyboardKind): boolean => kind === 'text' || kind === 'token' || kind === 'url' || kind === 'password'

/** Insère « : » automatiquement après chaque paire hexadécimale d'une adresse MAC. */
export function formatMacInput(raw: string): string {
  const hex = raw.replace(/[^0-9a-fA-F]/g, '').toLowerCase().slice(0, 12)
  return hex.match(/.{1,2}/g)?.join(':') ?? ''
}

/** Ajoute une touche à la valeur courante en respectant le type de champ. */
export function typeChar(value: string, char: string, kind: KeyboardKind, shift: boolean): string {
  if (!isKeyAllowed(kind, char) || value.length >= MAX_LENGTH[kind]) return value
  const typed = shift && supportsUppercase(kind) && LETTERS.test(char) ? char.toUpperCase() : char
  if (kind === 'mac') return formatMacInput(value + typed)
  return value + typed
}

export function backspace(value: string, kind: KeyboardKind): string {
  const shorter = Array.from(value).slice(0, -1).join('')
  return kind === 'mac' ? formatMacInput(shorter).replace(/:$/, '') : shorter
}
