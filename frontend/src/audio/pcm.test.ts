import { describe, expect, it } from 'vitest'

import { decodeSpeech } from './pcm'

const base64 = (...bytes: number[]) => btoa(String.fromCharCode(...bytes))

describe('decodeSpeech', () => {
  it('reads 16-bit little-endian PCM at the rate in its type', () => {
    // 0, the largest sample, the smallest one and -1.
    const piece = decodeSpeech('audio/pcm;rate=24000', base64(0, 0, 0xff, 0x7f, 0, 0x80, 0xff, 0xff))

    expect(piece?.sampleRate).toBe(24_000)
    expect(Array.from(piece?.samples ?? [])).toEqual([0, 32767 / 32768, -1, -1 / 32768])
  })

  it('drops a half sample at the end', () => {
    expect(decodeSpeech('audio/pcm;rate=16000', base64(0, 0x40, 0x01))?.samples).toEqual(
      new Float32Array([0.5]),
    )
  })

  it.each(['audio/wav', 'audio/pcm', 'audio/pcm;rate=fast', 'audio/pcm;rate=0'])(
    'refuses audio of type %s',
    (mime) => {
      expect(decodeSpeech(mime, base64(0, 0))).toBeNull()
    },
  )
})
