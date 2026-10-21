import { describe, expect, it } from 'vitest'

import { AudioQueue } from './queue'

/** A fake player: records what plays and lets the test end the current playback. */
function setUp() {
  const played: string[] = []
  const stopped: string[] = []
  const speaking: boolean[] = []
  let end: (() => void) | null = null
  const queue = new AudioQueue(
    (wav, done) => {
      const name = wav.type
      played.push(name)
      end = done
      return () => stopped.push(name)
    },
    (value) => speaking.push(value),
  )
  const finish = () => {
    const done = end
    end = null
    done?.()
  }
  return { queue, played, stopped, speaking, finish }
}

const chunk = (name: string) => new Blob([], { type: name })

describe('AudioQueue', () => {
  it('plays the chunks of a reply one after another', () => {
    const { queue, played, speaking, finish } = setUp()
    queue.push(chunk('a0'), true)
    queue.push(chunk('a1'), false)
    expect(played).toEqual(['a0'])
    finish()
    expect(played).toEqual(['a0', 'a1'])
    finish()
    expect(speaking.at(-1)).toBe(false)
  })

  it('plays a chunk at once when the previous one has already ended', () => {
    const { queue, played, finish } = setUp()
    queue.push(chunk('a0'), true)
    finish()
    queue.push(chunk('a1'), false)
    expect(played).toEqual(['a0', 'a1'])
  })

  it('replaces what is still playing when a new reply starts', () => {
    const { queue, played, stopped, finish } = setUp()
    queue.push(chunk('a0'), true)
    queue.push(chunk('a1'), false)
    queue.push(chunk('b0'), true)
    expect(stopped).toEqual(['a0'])
    finish()
    expect(played).toEqual(['a0', 'b0'])
  })

  it('drops the rest of an interrupted reply until the next one starts', () => {
    const { queue, played, stopped, speaking } = setUp()
    queue.push(chunk('a0'), true)
    queue.stop()
    queue.push(chunk('a1'), false)
    expect(stopped).toEqual(['a0'])
    expect(played).toEqual(['a0'])
    expect(speaking.at(-1)).toBe(false)
    queue.push(chunk('b0'), true)
    expect(played).toEqual(['a0', 'b0'])
  })

  it('ignores a late end of a playback that was stopped', () => {
    const { queue, played, finish } = setUp()
    queue.push(chunk('a0'), true)
    queue.push(chunk('a1'), false)
    queue.stop()
    finish()
    expect(played).toEqual(['a0'])
  })

  it('moves on when a playback ends as soon as it starts (autoplay blocked)', () => {
    const played: string[] = []
    const queue = new AudioQueue(
      (wav, done) => {
        played.push(wav.type)
        done()
        return () => undefined
      },
      () => undefined,
    )
    queue.push(chunk('a0'), true)
    queue.push(chunk('a1'), false)
    expect(played).toEqual(['a0', 'a1'])
  })
})
