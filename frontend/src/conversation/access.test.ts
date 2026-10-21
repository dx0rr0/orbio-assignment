import { describe, expect, it } from 'vitest'

import { loadToken, saveToken, socketUrl } from './access'

class MemoryStorage {
  private items = new Map<string, string>()
  getItem(key: string) {
    return this.items.get(key) ?? null
  }
  setItem(key: string, value: string) {
    this.items.set(key, value)
  }
}

const blocked = (): Storage => {
  throw new Error('storage is blocked')
}

describe('conversation tokens', () => {
  it('keeps the token of each conversation', () => {
    const memory = new MemoryStorage()
    const storage = () => memory
    saveToken(storage, 'abc', 's3cret')
    expect(loadToken(storage, 'abc')).toBe('s3cret')
    expect(loadToken(storage, 'other')).toBeNull()
  })

  it('works without storage', () => {
    expect(() => saveToken(blocked, 'abc', 's3cret')).not.toThrow()
    expect(loadToken(blocked, 'abc')).toBeNull()
  })
})

describe('socketUrl', () => {
  const page = { protocol: 'https:', host: 'example.test' }

  it('starts a conversation', () => {
    expect(socketUrl(page, null, null)).toBe('wss://example.test/ws/conversations/')
  })

  it('resumes one with its token', () => {
    expect(socketUrl({ protocol: 'http:', host: 'localhost:8080' }, 'abc', 'a b')).toBe(
      'ws://localhost:8080/ws/conversations/abc/?token=a%20b',
    )
  })
})
