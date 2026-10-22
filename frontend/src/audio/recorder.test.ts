import { afterEach, describe, expect, it, vi } from 'vitest'

import { Recorder } from './recorder'

/** A microphone (denied if `micDenied`) and an AudioContext whose worklet fails to load if `workletFails`. */
function setUp({ micDenied = false, workletFails = false } = {}) {
  const track = { stop: vi.fn() }
  const stream = { getTracks: () => [track] }
  const close = vi.fn(() => Promise.resolve())
  class FakeAudioContext {
    sampleRate = 48_000
    audioWorklet = {
      addModule: () => (workletFails ? Promise.reject(new Error('worklet failed')) : Promise.resolve()),
    }
    close = close
  }
  vi.stubGlobal('navigator', {
    mediaDevices: {
      getUserMedia: () =>
        micDenied ? Promise.reject(new DOMException('denied', 'NotAllowedError')) : Promise.resolve(stream),
    },
  })
  vi.stubGlobal('AudioContext', FakeAudioContext)
  return { track, close }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Recorder', () => {
  it('records nothing when the microphone is denied', async () => {
    setUp({ micDenied: true })
    const recorder = new Recorder()

    await expect(recorder.start()).rejects.toThrow('denied')

    expect(await recorder.stop()).toBeNull()
  })

  it('releases the microphone when it fails to start after opening it', async () => {
    const { track, close } = setUp({ workletFails: true })
    const recorder = new Recorder()

    await expect(recorder.start()).rejects.toThrow('worklet failed')

    expect(track.stop).toHaveBeenCalled()
    expect(close).toHaveBeenCalled()
    expect(await recorder.stop()).toBeNull()
  })
})
