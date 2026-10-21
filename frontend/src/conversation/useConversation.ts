import { useCallback, useEffect, useReducer, useRef } from 'react'

import { toBase64 } from '../audio/wav'
import { resumeMessage, saveToken, socketUrl } from './access'
import type { ClientMessage, ServerEvent } from './protocol'
import { initialState, reducer } from './state'

const session = () => window.sessionStorage

/** One conversation over WebSocket. `resumeId` watches an existing one instead of starting. */
export function useConversation(resumeId: string | null, playAudio: (wav: Blob) => void) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const socket = useRef<WebSocket | null>(null)
  // Kept in a ref so that a new callback doesn't reconnect the socket.
  const play = useRef(playAudio)
  useEffect(() => {
    play.current = playAudio
  }, [playAudio])

  useEffect(() => {
    const ws = new WebSocket(socketUrl(window.location, resumeId))
    socket.current = ws
    ws.onopen = () => {
      // The handshake: the backend sends the snapshot once it has the token.
      if (resumeId) ws.send(JSON.stringify(resumeMessage(session, resumeId)))
      dispatch({ type: 'connection', connection: 'open' })
    }
    ws.onclose = () => dispatch({ type: 'connection', connection: 'closed' })
    ws.onmessage = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as ServerEvent
      if (event.type === 'conversation_started') {
        saveToken(session, event.conversation_id, event.token)
      }
      if (event.type === 'audio') {
        const bytes = Uint8Array.from(atob(event.data), (c) => c.charCodeAt(0))
        play.current(new Blob([bytes], { type: event.mime }))
      }
      dispatch({ type: 'server', event })
    }
    return () => ws.close()
  }, [resumeId])

  const send = useCallback((message: ClientMessage) => {
    if (socket.current?.readyState !== WebSocket.OPEN) return
    socket.current.send(JSON.stringify(message))
    dispatch({ type: 'sent', text: message.type === 'text' ? message.text : undefined })
  }, [])

  const sendAudio = useCallback(
    (wav: ArrayBuffer) => send({ type: 'audio', data: toBase64(wav) }),
    [send],
  )
  const sendText = useCallback(
    (text: string, replyAudio: boolean) => send({ type: 'text', text, reply_audio: replyAudio }),
    [send],
  )

  return { state, sendAudio, sendText }
}
