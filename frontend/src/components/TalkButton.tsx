import { useRef, useState } from 'react'

import { Recorder } from '../audio/recorder'

/** Hold to talk (mouse, touch or the space bar); the utterance is sent on release. */
export function TalkButton({
  disabled,
  onRecordingChange,
  onUtterance,
  onError,
}: {
  disabled: boolean
  onRecordingChange: (recording: boolean) => void
  onUtterance: (audio: ArrayBuffer) => void
  onError: (message: string) => void
}) {
  const recorder = useRef(new Recorder())
  const [recording, setRecording] = useState(false)
  const starting = useRef<Promise<void> | null>(null)

  const setRecordingState = (value: boolean) => {
    setRecording(value)
    onRecordingChange(value)
  }

  const start = () => {
    if (disabled || recording) return
    setRecordingState(true)
    starting.current = recorder.current.start().catch(() => {
      setRecordingState(false)
      onError('No se ha podido acceder al micrófono.')
    })
  }

  const stop = async () => {
    if (!recording) return
    await starting.current
    setRecordingState(false)
    const audio = await recorder.current.stop()
    // None if the microphone could not be opened: its error is shown, nothing is sent.
    if (audio !== null) onUtterance(audio)
  }

  const label = recording ? 'Suelte para enviar' : 'Mantenga pulsado para hablar'
  return (
    <div className="talk">
      <button
        type="button"
        className={recording ? 'talk-button talk-recording' : 'talk-button'}
        aria-label={label}
        disabled={disabled}
        onPointerDown={start}
        onPointerUp={() => void stop()}
        onPointerLeave={() => void stop()}
        onKeyDown={(e) => {
          if (e.key === ' ' && !e.repeat) start()
        }}
        onKeyUp={(e) => {
          if (e.key === ' ') void stop()
        }}
      >
        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="9" y="2" width="6" height="12" rx="3" />
          <path d="M5 10a7 7 0 0 0 14 0" />
          <path d="M12 17v5" />
        </svg>
      </button>
      <span className="talk-label">{label}</span>
    </div>
  )
}
