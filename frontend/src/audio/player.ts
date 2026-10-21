import type { SpeechPiece } from './pcm'

/** Where speech plays: Web Audio in the page (`WebAudioOutput`), a fake in tests. */
export interface AudioOutput {
  /** The output's clock, in seconds. */
  now: () => number
  /** Plays `samples` from time `at` on, and calls `ended` once they end (or cannot play);
   * returns a function that stops them. */
  play: (samples: Float32Array, sampleRate: number, at: number, ended: () => void) => () => void
}

interface Playback {
  stop: () => void
}

/**
 * The speech of a reply arrives in pieces as it is synthesized: each is scheduled to start
 * exactly when the one before it ends, so they play without gaps (or at once, if it arrives
 * after that). The first piece of a reply replaces whatever is still playing. After `stop`
 * (the declarant talks over the agent), the rest of the interrupted reply is dropped until
 * the next reply starts.
 */
export class SpeechPlayer {
  private readonly output: AudioOutput
  private readonly onSpeaking: (speaking: boolean) => void
  private readonly playing = new Set<Playback>()
  // When the last scheduled piece ends, on the output's clock.
  private nextStart = 0
  private dropping = false

  constructor(output: AudioOutput, onSpeaking: (speaking: boolean) => void) {
    this.output = output
    this.onSpeaking = onSpeaking
  }

  push(piece: SpeechPiece, startsReply: boolean): void {
    if (startsReply) {
      this.clear()
      this.dropping = false
    } else if (this.dropping) {
      return
    }
    if (piece.samples.length === 0) return
    const at = Math.max(this.output.now(), this.nextStart)
    this.nextStart = at + piece.samples.length / piece.sampleRate
    const playback: Playback = { stop: () => undefined }
    this.playing.add(playback)
    this.onSpeaking(true)
    playback.stop = this.output.play(piece.samples, piece.sampleRate, at, () => {
      // A piece that was stopped (or had already ended) changes nothing.
      if (!this.playing.delete(playback)) return
      if (this.playing.size === 0) this.onSpeaking(false)
    })
  }

  stop(): void {
    this.clear()
    this.dropping = true
    this.onSpeaking(false)
  }

  private clear(): void {
    const stopping = [...this.playing]
    this.playing.clear()
    this.nextStart = 0
    stopping.forEach((playback) => playback.stop())
  }
}

/** Plays through one AudioContext, created with the page so that it is running (or known to
 * be blocked) by the time speech arrives. */
export class WebAudioOutput implements AudioOutput {
  private readonly context = new AudioContext()

  /** Lets the context play after the browser blocked autoplay; call it on a user gesture. */
  unlock(): void {
    if (this.context.state !== 'running') void this.context.resume()
  }

  now(): number {
    return this.context.currentTime
  }

  play(samples: Float32Array, sampleRate: number, at: number, ended: () => void): () => void {
    if (this.context.state !== 'running') {
      // Autoplay blocked: skip the speech (the text is on screen) rather than play it late.
      ended()
      return () => undefined
    }
    const buffer = this.context.createBuffer(1, samples.length, sampleRate)
    buffer.copyToChannel(samples, 0)
    const source = this.context.createBufferSource()
    source.buffer = buffer
    source.connect(this.context.destination)
    source.onended = ended
    source.start(at)
    return () => {
      source.onended = null
      source.stop()
    }
  }
}
