/** A piece of the agent's speech, as the backend sends it: raw PCM at a rate in its type. */
export interface SpeechPiece {
  samples: Float32Array
  sampleRate: number
}

const PCM_TYPE = /^audio\/pcm;rate=(\d+)$/

/** The samples of base64 16-bit little-endian mono PCM of type "audio/pcm;rate=<Hz>", or null
 * for any other type. */
export function decodeSpeech(mime: string, base64: string): SpeechPiece | null {
  const rate = PCM_TYPE.exec(mime)?.[1]
  const sampleRate = Number(rate)
  if (rate === undefined || sampleRate <= 0) return null
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))
  const view = new DataView(bytes.buffer)
  const samples = new Float32Array(Math.floor(bytes.length / 2))
  for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 0x8000
  return { samples, sampleRate }
}
