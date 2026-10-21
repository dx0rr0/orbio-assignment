/** Starts playing one WAV and calls `done` once it ends (or fails); returns a function that stops it. */
export type StartPlayback = (wav: Blob, done: () => void) => () => void

interface Playback {
  stop: () => void
}

/**
 * The audio of a reply arrives in chunks, each sent as soon as it is synthesized: they play
 * one after another. The first chunk of a reply replaces whatever is still playing. After
 * `stop` (the declarant talks over the agent), the rest of the
 * interrupted reply is dropped until the next reply starts.
 */
export class AudioQueue {
  private readonly start: StartPlayback
  private readonly onSpeaking: (speaking: boolean) => void
  private readonly pending: Blob[] = []
  private current: Playback | null = null
  private dropping = false

  constructor(start: StartPlayback, onSpeaking: (speaking: boolean) => void) {
    this.start = start
    this.onSpeaking = onSpeaking
  }

  push(wav: Blob, startsReply: boolean): void {
    if (startsReply) {
      this.clear()
      this.dropping = false
    } else if (this.dropping) {
      return
    }
    this.pending.push(wav)
    if (this.current === null) this.playNext()
  }

  stop(): void {
    this.clear()
    this.dropping = true
    this.onSpeaking(false)
  }

  private clear(): void {
    this.pending.length = 0
    const playing = this.current
    this.current = null
    playing?.stop()
  }

  private playNext(): void {
    const wav = this.pending.shift()
    if (wav === undefined) {
      this.onSpeaking(false)
      return
    }
    this.onSpeaking(true)
    const playback: Playback = { stop: () => undefined }
    this.current = playback
    playback.stop = this.start(wav, () => {
      // A playback that was stopped or replaced must not start the next chunk.
      if (this.current !== playback) return
      this.current = null
      this.playNext()
    })
  }
}
