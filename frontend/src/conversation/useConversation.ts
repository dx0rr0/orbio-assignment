import { useCallback, useEffect, useReducer, useRef } from 'react'

import { toBase64 } from '../audio/wav'
import type { ClientMessage, ServerEvent } from './protocol'
import { initialState, reducer } from './state'

function socketUrl(conversationId: string | null): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const path = conversationId ? `/ws/conversations/${conversationId}/` : '/ws/conversations/'
  return `${scheme}://${window.location.host}${path}`
}

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
    const ws = new WebSocket(socketUrl(resumeId))
    socket.current = ws
    ws.onopen = () => dispatch({ type: 'connection', connection: 'open' })
    ws.onclose = () => dispatch({ type: 'connection', connection: 'closed' })
    ws.onmessage = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as ServerEvent
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
