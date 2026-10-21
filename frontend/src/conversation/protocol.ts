/**
 * Messages exchanged with the backend over /ws/conversations/ (see consumers.py). What the
 * server sends is checked against these schemas before the page uses it.
 */

import { z } from 'zod'

const FieldState = z.enum(['empty', 'valid', 'invalid'])
export type FieldState = z.infer<typeof FieldState>

const ClaimField = z.object({
  name: z.string(),
  state: FieldState,
  // Its shape depends on the field; formatValue checks it.
  value: z.unknown(),
  error: z.string().nullable(),
})
export type ClaimField = z.infer<typeof ClaimField>

const ClaimView = z.object({
  status: z.string(),
  urgency: z.enum(['high', 'medium', 'low']).nullable(),
  reference: z.string().nullable(),
  closure_reason: z.string().nullable(),
  pending_fields: z.array(z.object({ field: z.string(), last_error: z.string().nullable() })),
  fields: z.array(ClaimField),
})
export type ClaimView = z.infer<typeof ClaimView>

const Summary = z.object({
  key_points: z.array(z.string()),
  urgency: z.string(),
  next_steps: z.array(z.string()),
})
export type Summary = z.infer<typeof Summary>

const TranscriptEntry = z.object({
  role: z.enum(['declarant', 'agent']),
  text: z.string(),
  /** Safety advice to make stand out (only known for messages received live). */
  alert: z.boolean().optional(),
})
export type TranscriptEntry = z.infer<typeof TranscriptEntry>

const ServerEvent = z.discriminatedUnion('type', [
  z.object({ type: z.literal('conversation_started'), conversation_id: z.string(), token: z.string() }),
  z.object({
    type: z.literal('snapshot'),
    conversation: z.object({
      id: z.string(),
      created_at: z.string(),
      claim: ClaimView,
      summary: Summary.nullable(),
      transcript: z.array(TranscriptEntry),
    }),
  }),
  z.object({ type: z.literal('transcript'), text: z.string() }),
  z.object({ type: z.literal('bot_message'), text: z.string(), alert: z.boolean() }),
  z.object({ type: z.literal('claim_updated'), claim: ClaimView, summary: Summary.nullable() }),
  // The first piece of a reply's speech; it replaces whatever is still playing. `data` is raw
  // PCM of type `mime` ("audio/pcm;rate=24000", see decodeSpeech).
  z.object({ type: z.literal('audio'), mime: z.string(), data: z.base64() }),
  // The next piece of the same reply, played right after the ones before it.
  z.object({ type: z.literal('audio_continued'), mime: z.string(), data: z.base64() }),
  // After the last piece: the turn's speech is complete (with no pieces if synthesis failed).
  z.object({ type: z.literal('audio_end') }),
  z.object({ type: z.literal('error'), message: z.string() }),
])
export type ServerEvent = z.infer<typeof ServerEvent>

/** The event in a WebSocket message's data, or null if it is not one this page knows. */
export function parseServerEvent(data: unknown): ServerEvent | null {
  if (typeof data !== 'string') return null
  let json: unknown
  try {
    json = JSON.parse(data)
  } catch {
    return null
  }
  const event = ServerEvent.safeParse(json)
  return event.success ? event.data : null
}

export type ClientMessage =
  | { type: 'resume'; token: string }
  | { type: 'audio'; data: string }
  | { type: 'text'; text: string; reply_audio: boolean }
