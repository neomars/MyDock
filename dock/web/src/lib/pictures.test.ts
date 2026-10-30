import { describe, expect, it } from 'vitest'
import { pictureUrl } from './pictures.svelte'

describe('pictureUrl', () => {
  it('fabrique une adresse data: à partir d’une réponse d’image', () => {
    expect(pictureUrl({ mime: 'image/png', data: 'AAAA' })).toBe('data:image/png;base64,AAAA')
    expect(pictureUrl({ mime: 'image/svg+xml', data: 'PHN2Zz4=' })).toBe('data:image/svg+xml;base64,PHN2Zz4=')
    expect(pictureUrl({ mime: 'image/vnd.microsoft.icon', data: 'AA' })).toContain('data:image/vnd.microsoft.icon')
  })

  it('refuse toute réponse qui n’est pas une image : jamais d’image cassée à l’écran', () => {
    for (const bad of [undefined, null, {}, [], 'texte', { mime: 'image/png' }, { data: 'AAAA' }, { mime: 5, data: 'AA' }, { mime: 'image/png', data: 5 }, { mime: 'text/html', data: 'AA' }, { mime: 'image/png;charset=x,<script>', data: 'AA' }, { mime: 'image/', data: 'AA' }]) {
      expect(pictureUrl(bad), JSON.stringify(bad)).toBeNull()
    }
  })
})
