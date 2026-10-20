import { describe, expect, it } from 'vitest'

import type { ClaimView } from './protocol'
import { initialState, isClosed, reducer } from './state'

const claim = (status: string): ClaimView => ({
  status,
  urgency: null,
  reference: null,
  closure_reason: null,
  pending_fields: [],
  fields: [],
})

describe('reducer', () => {
  it('shows typed text at once and waits for the reply', () => {
    const state = reducer(initialState, { type: 'sent', text: 'Hola' })
    expect(state.transcript).toEqual([{ role: 'declarant', text: 'Hola' }])
    expect(state.awaitingReply).toBe(true)
  })

  it('shows speech only once its transcript arrives', () => {
    let state = reducer(initialState, { type: 'sent' })
    expect(state.transcript).toEqual([])
    state = reducer(state, { type: 'server', event: { type: 'transcript', text: 'Hola' } })
    expect(state.transcript).toEqual([{ role: 'declarant', text: 'Hola' }])
  })

  it('marks an empty transcript as not understood', () => {
    const state = reducer(initialState, {
      type: 'server',
      event: { type: 'transcript', text: '' },
    })
    expect(state.transcript[0].text).toBe('(no se ha entendido el audio)')
  })

  it('stops waiting when the claim update closes the turn', () => {
    let state = reducer(initialState, { type: 'sent', text: 'Hola' })
    state = reducer(state, { type: 'server', event: { type: 'bot_message', text: '¿Póliza?' } })
    expect(state.awaitingReply).toBe(true)
    state = reducer(state, {
      type: 'server',
      event: { type: 'claim_updated', claim: claim('in_progress'), summary: null },
    })
    expect(state.awaitingReply).toBe(false)
    expect(state.transcript.map((e) => e.role)).toEqual(['declarant', 'agent'])
  })

  it('replaces everything with a snapshot', () => {
    const state = reducer(
      { ...initialState, transcript: [{ role: 'agent', text: 'viejo' }] },
      {
        type: 'server',
        event: {
          type: 'snapshot',
          conversation: {
            id: 'abc',
            claim: claim('confirmed'),
            summary: null,
            transcript: [{ role: 'agent', text: 'Hola' }],
          },
        },
      },
    )
    expect(state.conversationId).toBe('abc')
    expect(state.transcript).toEqual([{ role: 'agent', text: 'Hola' }])
  })

  it('keeps server errors and stops waiting', () => {
    let state = reducer(initialState, { type: 'sent', text: 'Hola' })
    state = reducer(state, { type: 'server', event: { type: 'error', message: 'ups' } })
    expect(state).toMatchObject({ error: 'ups', awaitingReply: false })
  })
})

describe('isClosed', () => {
  it.each([
    [null, false],
    [claim('in_progress'), false],
    [claim('pending_confirmation'), false],
    [claim('confirmed'), true],
    [claim('not_covered'), true],
  ])('%o → %s', (value, expected) => {
    expect(isClosed(value)).toBe(expected)
  })
})
