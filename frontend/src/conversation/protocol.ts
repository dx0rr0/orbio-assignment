/** Messages exchanged with the backend over /ws/conversations/ (see consumers.py). */

export type FieldState = 'empty' | 'valid' | 'invalid'

export interface ClaimField {
  name: string
  state: FieldState
  value: unknown
  error: string | null
}

export interface ClaimView {
  status: string
  urgency: 'high' | 'medium' | 'low' | null
  reference: string | null
  closure_reason: string | null
  pending_fields: { field: string; last_error: string | null }[]
  fields: ClaimField[]
}

export interface Summary {
  key_points: string[]
  urgency: string
  next_steps: string[]
}

export interface TranscriptEntry {
  role: 'declarant' | 'agent'
  text: string
}

export type ServerEvent =
  | { type: 'conversation_started'; conversation_id: string }
  | {
      type: 'snapshot'
      conversation: {
        id: string
        claim: ClaimView
        summary: Summary | null
        transcript: TranscriptEntry[]
      }
    }
  | { type: 'transcript'; text: string }
  | { type: 'bot_message'; text: string }
  | { type: 'claim_updated'; claim: ClaimView; summary: Summary | null }
  | { type: 'audio'; mime: string; data: string }
  | { type: 'error'; message: string }

export type ClientMessage =
  | { type: 'audio'; data: string }
  | { type: 'text'; text: string; reply_audio: boolean }
