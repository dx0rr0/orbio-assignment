import type { ClaimView, ServerEvent, Summary, TranscriptEntry } from './protocol'

export type Connection = 'connecting' | 'open' | 'closed'

export interface ConversationState {
  connection: Connection
  conversationId: string | null
  /** When the conversation started, in epoch milliseconds. */
  startedAt: number | null
  transcript: TranscriptEntry[]
  claim: ClaimView | null
  summary: Summary | null
  /** Waiting for the agent's reply to something the declarant said. */
  awaitingReply: boolean
  error: string | null
}

export type Action =
  | { type: 'connection'; connection: Connection }
  | { type: 'sent'; text?: string }
  | { type: 'server'; event: ServerEvent }

export const initialState: ConversationState = {
  connection: 'connecting',
  conversationId: null,
  startedAt: null,
  transcript: [],
  claim: null,
  summary: null,
  awaitingReply: false,
  error: null,
}

export function reducer(state: ConversationState, action: Action): ConversationState {
  switch (action.type) {
    case 'connection':
      return { ...state, connection: action.connection, awaitingReply: false }
    case 'sent':
      return {
        ...state,
        awaitingReply: true,
        error: null,
        // Typed text is shown at once; speech appears when its transcript arrives.
        transcript: action.text
          ? [...state.transcript, { role: 'declarant', text: action.text }]
          : state.transcript,
      }
    case 'server':
      return onServerEvent(state, action.event)
  }
}

function onServerEvent(state: ConversationState, event: ServerEvent): ConversationState {
  switch (event.type) {
    case 'conversation_started':
      return { ...state, conversationId: event.conversation_id, startedAt: Date.now() }
    case 'snapshot':
      return {
        ...state,
        conversationId: event.conversation.id,
        startedAt: Date.parse(event.conversation.created_at),
        transcript: event.conversation.transcript,
        claim: event.conversation.claim,
        summary: event.conversation.summary,
      }
    case 'transcript':
      return {
        ...state,
        transcript: [
          ...state.transcript,
          { role: 'declarant', text: event.text || '(no se ha entendido el audio)' },
        ],
      }
    case 'bot_message':
      return {
        ...state,
        transcript: [...state.transcript, { role: 'agent', text: event.text, alert: event.alert }],
      }
    case 'claim_updated':
      return { ...state, claim: event.claim, summary: event.summary, awaitingReply: false }
    case 'audio':
    case 'audio_continued':
      return state
    case 'error':
      return { ...state, error: event.message, awaitingReply: false }
  }
}

export function isClosed(claim: ClaimView | null): boolean {
  return claim !== null && !['in_progress', 'pending_confirmation'].includes(claim.status)
}
