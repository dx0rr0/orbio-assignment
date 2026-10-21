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

/** Push-to-talk recorder: `start()` opens the microphone, `stop()` returns a 16 kHz WAV. */
export class Recorder {
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private chunks: Float32Array[] = []

  async start(): Promise<void> {
    this.chunks = []
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
    const capture = new AudioWorkletNode(this.context, 'capture')
    capture.port.onmessage = (event: MessageEvent<Float32Array>) => this.chunks.push(event.data)
    this.context.createMediaStreamSource(this.stream).connect(capture)
  }

  async stop(): Promise<ArrayBuffer> {
    const sampleRate = this.context?.sampleRate ?? TARGET_SAMPLE_RATE
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.context?.close()
    this.context = null
    this.stream = null

    const samples = new Float32Array(this.chunks.reduce((total, c) => total + c.length, 0))
    let offset = 0
    for (const chunk of this.chunks) {
      samples.set(chunk, offset)
      offset += chunk.length
    }
    this.chunks = []
    return encodeWav(downsample(samples, sampleRate, TARGET_SAMPLE_RATE), TARGET_SAMPLE_RATE)
  }
}
