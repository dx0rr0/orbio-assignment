import { describe, expect, it } from 'vitest'

import { type AudioOutput, SpeechPlayer } from './player'

interface Scheduled {
  name: string
  at: number
  end: number
  ended: () => void
}

/** A fake output whose clock the test moves; it records what is scheduled and stopped. */
function setUp() {
  const scheduled: Scheduled[] = []
  const stopped: string[] = []
  const speaking: boolean[] = []
  let clock = 0
  const output: AudioOutput = {
    now: () => clock,
    play: (samples, rate, at, ended) => {
      const name = String(samples[0])
      scheduled.push({ name, at, end: at + samples.length / rate, ended })
      return () => stopped.push(name)
    },
  }
  const player = new SpeechPlayer(output, (value) => speaking.push(value))
  // Moves the clock to `time`, ending what was scheduled to end by then (as the output would).
  const advance = (time: number) => {
    clock = time
    for (const s of scheduled) if (s.end <= time) s.ended()
  }
  return { player, scheduled, stopped, speaking, advance, starts: () => scheduled.map((s) => [s.name, s.at]) }
}

// One second at 10 Hz: the name of a piece is its first sample.
const piece = (name: number, seconds = 1) => ({ samples: new Float32Array(seconds * 10).fill(name), sampleRate: 10 })

describe('SpeechPlayer', () => {
  it('schedules the pieces of a reply back to back', () => {
    const { player, starts, speaking } = setUp()
    player.push(piece(1), true)
    player.push(piece(2, 2), false)
    player.push(piece(3), false)

    expect(starts()).toEqual([
      ['1', 0],
      ['2', 1],
      ['3', 3],
    ])
    expect(speaking).toEqual([true, true, true])
  })

  it('stops speaking once the last piece ends', () => {
    const { player, speaking, advance } = setUp()
    player.push(piece(1), true)
    player.push(piece(2), false)
    advance(1.5)
    expect(speaking.at(-1)).toBe(true)
    advance(2.5)
    expect(speaking.at(-1)).toBe(false)
  })

  it('plays a late piece at once rather than in the past', () => {
    const { player, starts, advance } = setUp()
    player.push(piece(1), true)
    advance(2.5)
    player.push(piece(2), false)
    expect(starts().at(-1)).toEqual(['2', 2.5])
  })

  it('replaces what is still playing when a new reply starts', () => {
    const { player, stopped, starts, advance } = setUp()
    player.push(piece(1), true)
    player.push(piece(2), false)
    advance(0.5)
    player.push(piece(3), true)

    expect(stopped).toEqual(['1', '2'])
    expect(starts().at(-1)).toEqual(['3', 0.5])
  })

  it('drops the rest of an interrupted reply until the next one starts', () => {
    const { player, stopped, starts, speaking, advance } = setUp()
    player.push(piece(1), true)
    advance(0.5)
    player.stop()
    player.push(piece(2), false)

    expect(stopped).toEqual(['1'])
    expect(starts()).toEqual([['1', 0]])
    expect(speaking.at(-1)).toBe(false)
    player.push(piece(3), true)
    expect(starts().at(-1)).toEqual(['3', 0.5])
  })

  it('ignores a late end of a piece that was stopped', () => {
    const { player, scheduled, speaking } = setUp()
    player.push(piece(1), true)
    const first = scheduled[0]
    player.push(piece(2), true)
    first.ended()
    expect(speaking.at(-1)).toBe(true)
  })

  it('moves on when the output cannot play (autoplay blocked)', () => {
    const speaking: boolean[] = []
    const player = new SpeechPlayer(
      {
        now: () => 0,
        play: (_samples, _rate, _at, ended) => {
          ended()
          return () => undefined
        },
      },
      (value) => speaking.push(value),
    )
    player.push(piece(1), true)
    expect(speaking.at(-1)).toBe(false)
  })
})
