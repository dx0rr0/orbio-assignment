import { useRef, useState } from 'react'

import { Recorder } from '../audio/recorder'

/** Hold to talk (mouse, touch or the space bar); the utterance is sent on release. */
export function TalkButton({
  disabled,
  onUtterance,
  onError,
}: {
  disabled: boolean
  onUtterance: (wav: ArrayBuffer) => void
  onError: (message: string) => void
}) {
  const recorder = useRef(new Recorder())
  const [recording, setRecording] = useState(false)
  const starting = useRef<Promise<void> | null>(null)

  const start = () => {
    if (disabled || recording) return
    setRecording(true)
    starting.current = recorder.current.start().catch(() => {
      setRecording(false)
      onError('No se ha podido acceder al micrófono.')
    })
  }

  const stop = async () => {
    if (!recording) return
    await starting.current
    setRecording(false)
    onUtterance(await recorder.current.stop())
  }

  return (
    <button
      type="button"
      className={`talk ${recording ? 'talk-recording' : ''}`}
      disabled={disabled}
      onPointerDown={start}
      onPointerUp={stop}
      onPointerLeave={stop}
      onKeyDown={(e) => e.key === ' ' && !e.repeat && start()}
      onKeyUp={(e) => e.key === ' ' && stop()}
    >
      {recording ? 'Suelte para enviar' : 'Mantenga pulsado para hablar'}
    </button>
  )
}
