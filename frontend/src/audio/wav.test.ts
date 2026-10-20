import { describe, expect, it } from 'vitest'

import { downsample, encodeWav, toBase64 } from './wav'

describe('downsample', () => {
  it('averages the input samples behind each output sample', () => {
    const input = new Float32Array([0, 0.3, 0.6, 1, 1, 1])
    expect(Array.from(downsample(input, 48_000, 16_000))).toEqual([
      expect.closeTo(0.3),
      expect.closeTo(1),
    ])
  })

  it('returns the same samples when the rate does not change', () => {
    const input = new Float32Array([0.1, 0.2])
    expect(downsample(input, 16_000, 16_000)).toBe(input)
  })

  it('refuses to upsample', () => {
    expect(() => downsample(new Float32Array(4), 8_000, 16_000)).toThrow()
  })
})

describe('encodeWav', () => {
  const wav = new DataView(encodeWav(new Float32Array([0, 1, -1, 2]), 16_000))
  const text = (offset: number) =>
    String.fromCharCode(...[0, 1, 2, 3].map((i) => wav.getUint8(offset + i)))

  it('writes a 16-bit mono PCM header', () => {
    expect([text(0), text(8), text(12), text(36)]).toEqual(['RIFF', 'WAVE', 'fmt ', 'data'])
    expect(wav.getUint16(20, true)).toBe(1) // PCM
    expect(wav.getUint16(22, true)).toBe(1) // mono
    expect(wav.getUint32(24, true)).toBe(16_000)
    expect(wav.getUint16(34, true)).toBe(16)
    expect(wav.getUint32(40, true)).toBe(8) // 4 samples × 2 bytes
  })

  it('scales samples to 16-bit and clamps them', () => {
    const samples = [0, 1, 2, 3].map((i) => wav.getInt16(44 + i * 2, true))
    expect(samples).toEqual([0, 32767, -32768, 32767])
  })
})

describe('toBase64', () => {
  it('encodes bytes', () => {
    expect(toBase64(new Uint8Array([82, 73, 70, 70]).buffer)).toBe('UklGRg==')
  })

  it('handles buffers larger than one chunk', () => {
    const bytes = new Uint8Array(100_000).fill(65)
    expect(atob(toBase64(bytes.buffer))).toHaveLength(100_000)
  })
})
