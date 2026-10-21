import { describe, expect, it } from 'vitest'

import { OpusRecording } from './opus'

describe('OpusRecording', () => {
  it('is not available without WebCodecs, so the recorder falls back to WAV', async () => {
    expect(typeof globalThis.AudioEncoder).toBe('undefined')

    expect(await OpusRecording.start(48_000)).toBeNull()
  })
})
