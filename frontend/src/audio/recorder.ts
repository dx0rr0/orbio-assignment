import { OpusRecording } from './opus'
import { downsample, encodeWav, TARGET_SAMPLE_RATE } from './wav'

// Collects the microphone's samples on the audio thread and posts them to the page.
const CAPTURE_WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0][0]
    if (channel) this.port.postMessage(channel.slice(0))
    return true
  }
}
registerProcessor('capture', Capture)
`

/**
 * Push-to-talk recorder: `start()` opens the microphone, `stop()` returns the utterance as
 * Ogg Opus where the browser can encode it (about a twelfth of the bytes to upload), else as
 * a 16 kHz WAV.
 */
export class Recorder {
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private chunks: Float32Array[] = []
  private opus: OpusRecording | null = null
  // Whether the last `start()` succeeded: if it failed there is nothing to send.
  private started = false

  async start(): Promise<void> {
    this.started = false
    this.chunks = []
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
    try {
      this.context = new AudioContext()
      this.opus = await OpusRecording.start(this.context.sampleRate)
      const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: 'text/javascript' }))
      try {
        await this.context.audioWorklet.addModule(url)
      } finally {
        URL.revokeObjectURL(url)
      }
      const capture = new AudioWorkletNode(this.context, 'capture')
      capture.port.onmessage = (event: MessageEvent<Float32Array>) => {
        this.chunks.push(event.data)
        this.opus?.push(event.data)
      }
      this.context.createMediaStreamSource(this.stream).connect(capture)
    } catch (error) {
      // Don't leave the microphone open (and its indicator on) for a recording that never ran.
      this.opus = null
      await this.release()
      throw error
    }
    this.started = true
  }

  /** The utterance, or null if the recording never started (e.g. the microphone was denied). */
  async stop(): Promise<ArrayBuffer | null> {
    if (!this.started) return null
    this.started = false
    const sampleRate = this.context?.sampleRate ?? TARGET_SAMPLE_RATE
    const opus = this.opus
    this.opus = null
    await this.release()
    const ogg = await opus?.finish()
    if (ogg) {
      this.chunks = []
      return ogg
    }

    const samples = new Float32Array(this.chunks.reduce((total, c) => total + c.length, 0))
    let offset = 0
    for (const chunk of this.chunks) {
      samples.set(chunk, offset)
      offset += chunk.length
    }
    this.chunks = []
    return encodeWav(downsample(samples, sampleRate, TARGET_SAMPLE_RATE), TARGET_SAMPLE_RATE)
  }

  private async release(): Promise<void> {
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.context?.close()
    this.context = null
    this.stream = null
  }
}
