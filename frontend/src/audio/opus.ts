import { encodeOggOpus, opusHead, type OpusPacket } from './ogg'

// Plenty for speech: 10 s is ~25 KB instead of ~320 KB of 16 kHz WAV.
export const OPUS_BITRATE = 24_000
// libopus's look-ahead at 48 kHz, for an encoder that gives no identification header.
const DEFAULT_PRE_SKIP = 312
const DEFAULT_PACKET_US = 20_000

/**
 * Encodes the microphone's samples to Opus (WebCodecs) as they arrive, so that stopping only
 * flushes the last packet; `finish()` returns them as an Ogg Opus file.
 */
export class OpusRecording {
  private readonly packets: OpusPacket[] = []
  private head: Uint8Array
  private failed = false
  private timestamp = 0
  private readonly encoder: AudioEncoder
  private readonly sampleRate: number

  private constructor(config: AudioEncoderConfig, sampleRate: number) {
    this.sampleRate = sampleRate
    this.head = opusHead(sampleRate, DEFAULT_PRE_SKIP)
    this.encoder = new AudioEncoder({
      output: (chunk, metadata) => this.collect(chunk, metadata),
      error: () => {
        this.failed = true
      },
    })
    this.encoder.configure(config)
  }

  /** A new recording, or null where the browser can't encode Opus (the caller sends WAV). */
  static async start(sampleRate: number): Promise<OpusRecording | null> {
    if (typeof AudioEncoder === 'undefined') return null
    const config = { codec: 'opus', sampleRate, numberOfChannels: 1, bitrate: OPUS_BITRATE }
    try {
      const support = await AudioEncoder.isConfigSupported(config)
      return support.supported ? new OpusRecording(config, sampleRate) : null
    } catch {
      return null
    }
  }

  push(samples: Float32Array): void {
    if (this.failed) return
    try {
      const data = new AudioData({
        format: 'f32',
        sampleRate: this.sampleRate,
        numberOfFrames: samples.length,
        numberOfChannels: 1,
        timestamp: Math.round(this.timestamp),
        data: samples,
      })
      this.timestamp += (samples.length / this.sampleRate) * 1e6
      this.encoder.encode(data)
      data.close()
    } catch {
      this.failed = true
    }
  }

  /** The Ogg Opus file, or null if encoding failed somewhere (the caller sends WAV). */
  async finish(): Promise<ArrayBuffer | null> {
    try {
      await this.encoder.flush()
      this.encoder.close()
    } catch {
      this.failed = true
    }
    if (this.failed || this.packets.length === 0) return null
    const ogg = encodeOggOpus(this.head, this.packets)
    return ogg.buffer.slice(ogg.byteOffset, ogg.byteOffset + ogg.byteLength)
  }

  private collect(chunk: EncodedAudioChunk, metadata?: EncodedAudioChunkMetadata): void {
    const description = metadata?.decoderConfig?.description
    if (description) {
      const bytes = ArrayBuffer.isView(description)
        ? new Uint8Array(description.buffer, description.byteOffset, description.byteLength)
        : new Uint8Array(description)
      // The encoder's own OpusHead carries its real pre-skip.
      if (String.fromCharCode(...bytes.subarray(0, 8)) === 'OpusHead') this.head = bytes.slice()
    }
    const data = new Uint8Array(chunk.byteLength)
    chunk.copyTo(data)
    const microseconds = chunk.duration ?? DEFAULT_PACKET_US
    this.packets.push({ data, samples: Math.round((microseconds * 48_000) / 1e6) })
  }
}
