import { PauseCutter } from './pauses'
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
 * Push-to-talk recorder: `start(onPart)` opens the microphone and hands each part of the
 * utterance to `onPart` as a 16 kHz WAV as soon as a pause ends it; `stop()` returns the last
 * part.
 */
export class Recorder {
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private cutter: PauseCutter | null = null

  async start(onPart: (wav: ArrayBuffer) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
    this.context = new AudioContext()
    const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: 'text/javascript' }))
    try {
      await this.context.audioWorklet.addModule(url)
    } finally {
      URL.revokeObjectURL(url)
    }
    const sampleRate = this.context.sampleRate
    const cutter = new PauseCutter(sampleRate)
    this.cutter = cutter
    const capture = new AudioWorkletNode(this.context, 'capture')
    capture.port.onmessage = (event: MessageEvent<Float32Array>) => {
      const part = cutter.push(event.data)
      if (part) onPart(toWav(part, sampleRate))
    }
    this.context.createMediaStreamSource(this.stream).connect(capture)
  }

  async stop(): Promise<ArrayBuffer> {
    const sampleRate = this.context?.sampleRate ?? TARGET_SAMPLE_RATE
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.context?.close()
    this.context = null
    this.stream = null

    const last = this.cutter?.flush() ?? new Float32Array()
    this.cutter = null
    return toWav(last, sampleRate)
  }
}

function toWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  return encodeWav(downsample(samples, sampleRate, TARGET_SAMPLE_RATE), TARGET_SAMPLE_RATE)
}
