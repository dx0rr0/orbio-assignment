import { describe, expect, it } from 'vitest'

import { level, MIN_PART_SECONDS, PAUSE_SECONDS, PauseCutter } from './pauses'

const RATE = 1_000
const voice = (seconds: number) => new Float32Array(seconds * RATE).fill(0.5)
const silence = (seconds: number) => new Float32Array(seconds * RATE)

/** Pushes `blocks` in 10 ms pieces, as the microphone delivers them; returns the parts cut. */
function feed(cutter: PauseCutter, ...blocks: Float32Array[]): Float32Array[] {
  const parts: Float32Array[] = []
  for (const block of blocks) {
    for (let start = 0; start < block.length; start += 10) {
      const part = cutter.push(block.subarray(start, start + 10))
      if (part) parts.push(part)
    }
  }
  return parts
}

describe('level', () => {
  it('is the root mean square of the samples', () => {
    expect(level(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5)
    expect(level(new Float32Array())).toBe(0)
  })
})

describe('PauseCutter', () => {
  it('cuts in the middle of a pause once a part is long enough', () => {
    const cutter = new PauseCutter(RATE)

    const parts = feed(cutter, voice(MIN_PART_SECONDS), silence(PAUSE_SECONDS), voice(1))

    expect(parts.map((part) => part.length)).toEqual([(MIN_PART_SECONDS + PAUSE_SECONDS / 2) * RATE])
    expect(cutter.flush().length).toBe((PAUSE_SECONDS / 2 + 1) * RATE)
  })

  it('does not cut a part shorter than the minimum', () => {
    const cutter = new PauseCutter(RATE)

    expect(feed(cutter, voice(1), silence(0.5), voice(1))).toEqual([])
    expect(cutter.flush().length).toBe(2.5 * RATE)
  })

  it('does not cut in a gap between words', () => {
    const cutter = new PauseCutter(RATE)

    expect(feed(cutter, voice(MIN_PART_SECONDS), silence(PAUSE_SECONDS / 2), voice(1))).toEqual([])
  })

  it('keeps every sample, in order', () => {
    const cutter = new PauseCutter(RATE)
    const recording = [voice(2.5), silence(0.4), voice(3), silence(0.5), voice(0.7)]

    const parts = [...feed(cutter, ...recording), cutter.flush()]

    expect(parts).toHaveLength(3)
    const total = recording.reduce((sum, block) => sum + block.length, 0)
    expect(parts.reduce((sum, part) => sum + part.length, 0)).toBe(total)
  })
})
