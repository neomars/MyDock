import { describe, expect, it } from 'vitest'
import {
  DIGITS,
  LETTER_ROWS,
  SYMBOLS,
  backspace,
  formatMacInput,
  isKeyAllowed,
  keyCase,
  typeChar,
} from './keyboardLayout'

describe('disposition', () => {
  it('contient les 26 lettres une seule fois, en AZERTY', () => {
    const letters = LETTER_ROWS.flat().sort().join('')
    expect(letters).toBe('abcdefghijklmnopqrstuvwxyz')
    expect(LETTER_ROWS[0].slice(0, 6).join('')).toBe('azerty')
    expect(LETTER_ROWS[1].slice(0, 6).join('')).toBe('qsdfgh')
    expect(DIGITS).toHaveLength(10)
  })

  it('offre les symboles nécessaires aux adresses et aux jetons', () => {
    for (const needed of ['.', '-', '_', ':', '/']) expect(SYMBOLS).toContain(needed)
  })
})

describe('règles par type de champ', () => {
  it('un nom d’hôte n’accepte ni espace ni deux-points ni barre oblique', () => {
    for (const bad of [' ', ':', '/', '_', '@']) expect(isKeyAllowed('host', bad)).toBe(false)
    for (const good of ['a', 'Z', '0', '.', '-']) expect(isKeyAllowed('host', good)).toBe(true)
  })

  it('un jeton accepte . _ ~ - mais pas le reste', () => {
    for (const good of ['a', 'Z', '9', '.', '_', '~', '-']) expect(isKeyAllowed('token', good)).toBe(true)
    for (const bad of [' ', ':', '/', '@']) expect(isKeyAllowed('token', bad)).toBe(false)
  })

  it('un nombre n’accepte que des chiffres, 5 au plus', () => {
    expect(typeChar('8765', '4', 'number', false)).toBe('87654')
    expect(typeChar('87654', '3', 'number', false)).toBe('87654')
    expect(typeChar('', 'a', 'number', false)).toBe('')
  })

  it('une adresse MAC n’accepte que de l’hexadécimal', () => {
    expect(isKeyAllowed('mac', 'g')).toBe(false)
    expect(isKeyAllowed('mac', 'f')).toBe(true)
  })
})

describe('saisie', () => {
  it('majuscule seulement avec Maj et seulement pour le texte et les jetons', () => {
    expect(typeChar('', 'a', 'token', true)).toBe('A')
    expect(typeChar('', 'a', 'token', false)).toBe('a')
    expect(typeChar('', 'a', 'host', true)).toBe('a')
    expect(typeChar('x', '.', 'token', true)).toBe('x.')
  })

  it('ignore les touches interdites', () => {
    expect(typeChar('pc', ' ', 'host', false)).toBe('pc')
  })

  it('efface sans casser les caractères accentués', () => {
    expect(backspace('abc', 'text')).toBe('ab')
    expect(backspace('é', 'text')).toBe('')
    expect(backspace('', 'text')).toBe('')
  })
})

describe('adresse MAC', () => {
  it('insère les deux-points toute seule', () => {
    let value = ''
    for (const c of '02005e100001') value = typeChar(value, c, 'mac', false)
    expect(value).toBe('02:00:5e:10:00:01')
  })

  it('accepte une saisie déjà séparée et normalise la casse', () => {
    expect(formatMacInput('02:00:5E:10:00:01')).toBe('02:00:5e:10:00:01')
    expect(formatMacInput('02-00-5E')).toBe('02:00:5e')
  })

  it('ne dépasse pas 6 paires', () => {
    expect(formatMacInput('02005e1000010203')).toBe('02:00:5e:10:00:01')
  })

  it('efface en retirant aussi le séparateur resté en bout', () => {
    expect(backspace('02:00', 'mac')).toBe('02:0')
    expect(backspace('02:0', 'mac')).toBe('02')
    expect(backspace('02', 'mac')).toBe('0')
  })
})

describe('symbole boursier', () => {
  it('accepte lettres, chiffres et . ^ = _ - jusqu’à 20 caractères, et rien d’autre', () => {
    for (const good of ['a', 'Z', '7', '.', '^', '=', '_', '-']) expect(isKeyAllowed('symbol', good)).toBe(true)
    for (const bad of [' ', ':', '/', '@', '?', '&', '%']) expect(isKeyAllowed('symbol', bad)).toBe(false)
    expect(typeChar('A'.repeat(20), 'B', 'symbol', false)).toBe('A'.repeat(20))
  })

  it('met les lettres en majuscules d’office', () => {
    expect(typeChar('', 'm', 'symbol', false)).toBe('M')
    expect(typeChar('^F', 'c', 'symbol', false)).toBe('^FC')
    expect(typeChar('EURUSD', '=', 'symbol', false)).toBe('EURUSD=')
    expect(keyCase('a', 'symbol', false)).toBe('A')
    expect(keyCase('a', 'text', false)).toBe('a')
    expect(keyCase('a', 'text', true)).toBe('A')
    expect(keyCase('a', 'host', true)).toBe('a')
  })

  it('offre ^ et = pour ^FCHI et EURUSD=X', () => {
    expect(SYMBOLS).toContain('^')
    expect(SYMBOLS).toContain('=')
  })
})
