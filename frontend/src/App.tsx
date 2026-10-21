import { useCallback, useState } from 'react'

import { AudioQueue } from './audio/queue'
import { type AgentActivity, CallHeader } from './components/CallHeader'
import { ClaimPanel } from './components/ClaimPanel'
import { TalkButton } from './components/TalkButton'
import { Transcript } from './components/Transcript'
import { isClosed } from './conversation/state'
import { useConversation } from './conversation/useConversation'

const params = new URLSearchParams(window.location.search)
// ?conversation=<id> watches an existing conversation; ?dev shows a text input.
const resumeId = params.get('conversation')
const devMode = params.has('dev')

/** Plays one WAV in the page; the returned function stops it. */
function playWav(wav: Blob, done: () => void): () => void {
  const url = URL.createObjectURL(wav)
  const audio = new Audio(url)
  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    URL.revokeObjectURL(url)
    done()
  }
  audio.onended = finish
  audio.play().catch(finish) // autoplay may be blocked
  return () => {
    finished = true
    audio.pause()
    URL.revokeObjectURL(url)
  }
}

export default function App() {
  const [speaking, setSpeaking] = useState(false)
  const [recording, setRecording] = useState(false)
  const [queue] = useState(() => new AudioQueue(playWav, setSpeaking))

  const stopSpeaking = useCallback(() => {
    queue.stop()
  }, [queue])

  const playAudio = useCallback(
    (wav: Blob, startsReply: boolean) => {
      queue.push(wav, startsReply)
    },
    [queue],
  )

  const { state, sendAudioPart, sendText } = useConversation(resumeId, playAudio)
  const [micError, setMicError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const closed = isClosed(state.claim)
  const connected = state.connection === 'open'
  const canSpeak = connected && !state.awaitingReply && !closed
  const activity: AgentActivity = recording
    ? 'listening'
    : state.awaitingReply
      ? 'thinking'
      : speaking
        ? 'speaking'
        : 'waiting'

  return (
    <div className="layout">
      <main className="call">
        <CallHeader
          connected={connected}
          ended={closed || state.connection === 'closed'}
          startedAt={state.startedAt}
          activity={activity}
        />

        <Transcript entries={state.transcript} thinking={state.awaitingReply} />

        {(state.error || micError) && <p className="error">{state.error ?? micError}</p>}

        <footer className="controls">
          {closed ? (
            <a className="restart" href={window.location.pathname}>
              Nueva declaración
            </a>
          ) : (
            <TalkButton
              disabled={!canSpeak}
              onRecordingChange={(value) => {
                setRecording(value)
                if (value) stopSpeaking() // talking over the agent interrupts it
              }}
              onUtterancePart={(wav, final) => {
                setMicError(null)
                sendAudioPart(wav, final)
              }}
              onError={setMicError}
            />
          )}
          {devMode && !closed && (
            <form
              className="dev-input"
              onSubmit={(e) => {
                e.preventDefault()
                if (!draft.trim()) return
                stopSpeaking()
                sendText(draft, true)
                setDraft('')
              }}
            >
              <label htmlFor="dev-text" className="visually-hidden">
                Escribir en lugar de hablar
              </label>
              <input
                id="dev-text"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Escribir en lugar de hablar (modo desarrollo)"
                disabled={!canSpeak}
              />
            </form>
          )}
        </footer>
      </main>

      <ClaimPanel claim={state.claim} summary={state.summary} />
    </div>
  )
}
