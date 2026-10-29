import { describe, expect, it } from 'vitest'
import { reconnectDelay } from './backoff'

describe('reconnectDelay', () => {
  it('augmente puis plafonne à 4 s', () => {
    expect([0, 1, 2, 3, 4, 5, 6, 50].map(reconnectDelay)).toEqual([
      300, 300, 600, 1_200, 2_500, 4_000, 4_000, 4_000,
    ])
  })
})
