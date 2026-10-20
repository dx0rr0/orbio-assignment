import { useCallback, useRef, useState } from 'react'

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
  const player = useRef<HTMLAudioElement | null>(null)
  const playAudio = useCallback((wav: Blob) => {
    player.current?.pause()
    const url = URL.createObjectURL(wav)
    const audio = new Audio(url)
    audio.onended = () => URL.revokeObjectURL(url)
    player.current = audio
    audio.play().catch(() => URL.revokeObjectURL(url)) // autoplay may be blocked
  }, [])

  const { state, sendAudio, sendText } = useConversation(resumeId, playAudio)
  const [micError, setMicError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const closed = isClosed(state.claim)
  const canSpeak = state.connection === 'open' && !state.awaitingReply && !closed

  return (
    <div className="layout">
      <main className="conversation">
        <header>
          <h1>Declaración de siniestro</h1>
          {state.connection !== 'open' && (
            <span className="connection">
              {state.connection === 'connecting' ? 'Conectando…' : 'Desconectado'}
            </span>
          )}
        </header>

        <Transcript entries={state.transcript} typing={state.awaitingReply} />

        {(state.error || micError) && <p className="error">{state.error ?? micError}</p>}

        <footer className="controls">
          {closed ? (
            <a className="restart" href={window.location.pathname}>
              Nueva declaración
            </a>
          ) : (
            <TalkButton
              disabled={!canSpeak}
              onUtterance={(wav) => {
                player.current?.pause()
                setMicError(null)
                sendAudio(wav)
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
                sendText(draft, true)
                setDraft('')
              }}
            >
              <input
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
