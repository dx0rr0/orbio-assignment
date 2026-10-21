import { useCallback, useState } from 'react'

import type { SpeechPiece } from './audio/pcm'
import { SpeechPlayer, WebAudioOutput } from './audio/player'
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

export default function App() {
  const [speaking, setSpeaking] = useState(false)
  const [recording, setRecording] = useState(false)
  const [output] = useState(() => new WebAudioOutput())
  const [player] = useState(() => new SpeechPlayer(output, setSpeaking))

  // Called on the declarant's gestures, which also let the browser play audio from then on.
  const stopSpeaking = useCallback(() => {
    output.unlock()
    player.stop()
  }, [output, player])

  const playAudio = useCallback(
    (piece: SpeechPiece, startsReply: boolean) => {
      player.push(piece, startsReply)
    },
    [player],
  )

  const { state, sendAudio, sendText } = useConversation(resumeId, playAudio)
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
              onUtterance={(audio) => {
                setMicError(null)
                sendAudio(audio)
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
