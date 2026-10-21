import { describe, expect, it } from 'vitest'

import { parseServerEvent } from './protocol'

const claim = {
  status: 'in_progress',
  urgency: null,
  reference: null,
  closure_reason: null,
  pending_fields: [{ field: 'plate', last_error: 'No coincide' }],
  fields: [{ name: 'plate', state: 'invalid', value: null, error: 'No coincide' }],
}

describe('parseServerEvent', () => {
  it('reads the events the backend sends', () => {
    const event = { type: 'claim_updated', claim, summary: null }
    expect(parseServerEvent(JSON.stringify(event))).toEqual(event)
    expect(parseServerEvent('{"type": "bot_message", "text": "Hola", "alert": false}')).toEqual({
      type: 'bot_message',
      text: 'Hola',
      alert: false,
    })
  })

  it('reads a snapshot, leaving out what the page does not use', () => {
    const conversation = {
      id: 'abc',
      created_at: '2026-10-21T10:00:00+00:00',
      claim,
      summary: { key_points: ['Colisión'], urgency: 'low', next_steps: [] },
      transcript: [{ role: 'agent', text: 'Hola' }],
    }
    const sent = { ...conversation, updated_at: '2026-10-21T10:01:00+00:00' }
    expect(parseServerEvent(JSON.stringify({ type: 'snapshot', conversation: sent }))).toEqual({
      type: 'snapshot',
      conversation,
    })
  })

  it.each(['audio', 'audio_continued'])('reads %s pieces of a reply', (type) => {
    const audio = { type, mime: 'audio/pcm;rate=24000', data: 'AAD/fw==' }
    expect(parseServerEvent(JSON.stringify(audio))).toEqual(audio)
  })

  const badFields = [{ name: 'plate', state: 'maybe', value: null, error: null }]

  it.each([
    ['not JSON', '{"type":'],
    ['not a string', new ArrayBuffer(4)],
    ['an unknown type', '{"type": "video"}'],
    ['a missing field', '{"type": "bot_message", "text": "Hola"}'],
    ['a field of the wrong type', '{"type": "transcript", "text": 42}'],
    [
      'an unknown field state',
      JSON.stringify({ type: 'claim_updated', claim: { ...claim, fields: badFields }, summary: null }),
    ],
    ['audio that is not base64', '{"type": "audio", "mime": "audio/pcm;rate=24000", "data": "not base64!"}'],
  ])('rejects %s', (_, data) => {
    expect(parseServerEvent(data)).toBeNull()
  })
})
