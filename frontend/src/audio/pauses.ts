/**
 * Cuts a recording into parts at the speaker's pauses, so each part can be transcribed while
 * the rest is still being spoken. Cutting in a pause, not at a fixed length, keeps words whole.
 */

// Shorter parts save little: each transcription request has a fixed cost in time.
export const MIN_PART_SECONDS = 2
// A gap between words is shorter than this.
export const PAUSE_SECONDS = 0.3
// Root mean square level below which a block of samples counts as silence.
export const SILENCE_LEVEL = 0.01

export function level(block: Float32Array): number {
  if (block.length === 0) return 0
  let sum = 0
  for (const sample of block) sum += sample * sample
  return Math.sqrt(sum / block.length)
}

export class PauseCutter {
  private blocks: Float32Array[] = []
  private length = 0
  // How many samples at the end of what is held are silence.
  private silence = 0

  private readonly sampleRate: number

  constructor(sampleRate: number) {
    this.sampleRate = sampleRate
  }

  /** Adds a block of samples; returns a part when a pause ends one, else null. */
  push(block: Float32Array): Float32Array | null {
    this.blocks.push(block)
    this.length += block.length
    this.silence = level(block) < SILENCE_LEVEL ? this.silence + block.length : 0
    const longEnough = this.length >= MIN_PART_SECONDS * this.sampleRate
    if (!longEnough || this.silence < PAUSE_SECONDS * this.sampleRate) return null
    // Cut in the middle of the pause: both parts keep a little silence around their words.
    const cut = this.length - Math.floor(this.silence / 2)
    const held = this.take()
    this.keep(held.subarray(cut))
    return held.subarray(0, cut)
  }

  /** Whatever is held: the last part of the recording. */
  flush(): Float32Array {
    return this.take()
  }

  private take(): Float32Array {
    const samples = new Float32Array(this.length)
    let offset = 0
    for (const block of this.blocks) {
      samples.set(block, offset)
      offset += block.length
    }
    this.blocks = []
    this.length = 0
    this.silence = 0
    return samples
  }

  private keep(rest: Float32Array): void {
    this.blocks = [rest]
    this.length = rest.length
    // What is kept is the second half of the pause.
    this.silence = rest.length
  }
}
