import { useCallback, useEffect, useReducer, useRef } from 'react'

import { decodeSpeech, type SpeechPiece } from '../audio/pcm'
import { toBase64 } from '../audio/wav'
import { resumeMessage, saveToken, socketUrl } from './access'
import { type ClientMessage, parseServerEvent } from './protocol'
import { initialState, reducer } from './state'

const session = () => window.sessionStorage

/** One conversation over WebSocket, connected once `started`. `resumeId` watches an existing
 * one instead of starting. */
export function useConversation(
  resumeId: string | null,
  started: boolean,
  playAudio: (piece: SpeechPiece, startsReply: boolean) => void,
) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const socket = useRef<WebSocket | null>(null)
  // Kept in a ref so that a new callback doesn't reconnect the socket.
  const play = useRef(playAudio)
  useEffect(() => {
    play.current = playAudio
  }, [playAudio])

  useEffect(() => {
    if (!started) return
    dispatch({ type: 'connection', connection: 'connecting' })
    const ws = new WebSocket(socketUrl(window.location, resumeId))
    socket.current = ws
    ws.onopen = () => {
      // The handshake: the backend sends the snapshot once it has the token.
      if (resumeId) ws.send(JSON.stringify(resumeMessage(session, resumeId)))
      dispatch({ type: 'connection', connection: 'open' })
    }
    ws.onclose = () => dispatch({ type: 'connection', connection: 'closed' })
    ws.onmessage = (message: MessageEvent<unknown>) => {
      const event = parseServerEvent(message.data)
      if (event === null) {
        // A newer or broken backend: skip what this page can't show rather than crash.
        console.warn('Ignoring an unexpected message from the server', message.data)
        return
      }
      if (event.type === 'conversation_started') {
        saveToken(session, event.conversation_id, event.token)
      }
      if (event.type === 'audio' || event.type === 'audio_continued') {
        const piece = decodeSpeech(event.mime, event.data)
        if (piece === null) console.warn('Ignoring speech of an unknown type', event.mime)
        else play.current(piece, event.type === 'audio')
      }
      dispatch({ type: 'server', event })
    }
    return () => ws.close()
  }, [resumeId, started])

  const send = useCallback((message: ClientMessage) => {
    if (socket.current?.readyState !== WebSocket.OPEN) return
    socket.current.send(JSON.stringify(message))
    dispatch({ type: 'sent', text: message.type === 'text' ? message.text : undefined })
  }, [])

  const sendAudio = useCallback(
    (audio: ArrayBuffer) => send({ type: 'audio', data: toBase64(audio) }),
    [send],
  )
  const sendText = useCallback(
    (text: string, replyAudio: boolean) => send({ type: 'text', text, reply_audio: replyAudio }),
    [send],
  )

  return { state, sendAudio, sendText }
}
