import { describe, expect, it } from 'vitest'

import type { ClaimField, ClaimView } from '../conversation/protocol'
import { stages } from './stages'

const valid = (name: string, value: unknown): ClaimField => ({
  name,
  state: 'valid',
  value,
  error: null,
})
const empty = (name: string): ClaimField => ({ name, state: 'empty', value: null, error: null })

function claim(fields: ClaimField[], status = 'in_progress'): ClaimView {
  return { status, urgency: null, reference: null, closure_reason: null, pending_fields: [], fields }
}

const IDENTIFIED = [
  valid('policy_number', 'AUT-51730284'),
  valid('plate', '8305HJT'),
  valid('declarant_name', 'Javier Ruiz Martín'),
]

describe('stages', () => {
  it('summarises finished stages and details the current one', () => {
    const result = stages(
      claim([
        ...IDENTIFIED,
        valid('driver_is_policyholder', false),
        empty('driver_name'),
        empty('loss_type'),
      ]),
    )

    expect(result.map((s) => [s.id, s.state, s.current])).toEqual([
      ['identification', 'done', false],
      ['driver', 'pending', true],
      ['loss', 'pending', false],
      ['confirmation', 'pending', false],
    ])
    expect(result[0].lines).toEqual([{ text: 'Javier Ruiz Martín', error: false }])
    expect(result[1].lines.map((l) => l.text)).toEqual([
      '¿Conducía el titular?: No',
      'Conductor: pendiente',
    ])
    expect(result[2].lines).toEqual([]) // a later stage shows nothing yet
  })

  it('shows rejected values as errors even outside the current stage', () => {
    const result = stages(
      claim([
        empty('policy_number'),
        { name: 'plate', state: 'invalid', value: null, error: 'No coincide con la matrícula' },
        empty('declarant_name'),
        { name: 'other_vehicle_plate', state: 'invalid', value: null, error: '«25» no vale' },
      ]),
    )

    const otherVehicle = result.find((s) => s.id === 'other_vehicle')!
    expect(otherVehicle).toMatchObject({ state: 'error', current: false })
    expect(otherVehicle.lines).toEqual([
      { text: 'Matrícula del otro vehículo: «25» no vale', error: true },
    ])
    expect(result[0].lines[1]).toEqual({
      text: 'Matrícula: No coincide con la matrícula',
      error: true,
    })
  })

  it('reaches confirmation when every field is valid, and finishes once confirmed', () => {
    const complete = [
      ...IDENTIFIED,
      valid('driver_is_policyholder', true),
      valid('loss_type', 'theft'),
      valid('loss_window', { start: '2026-10-19T19:00:00+02:00', end: '2026-10-19T19:00:00+02:00' }),
      valid('loss_location', { way: null, municipality: 'Alcalá de Henares', province: 'Madrid', country: 'España' }),
      valid('description', 'Me robaron el coche'),
      valid('injuries', false),
      valid('vehicle_immobilized', false),
      valid('police_report_filed', true),
    ]

    const pending = stages(claim(complete, 'pending_confirmation'))
    expect(pending.at(-1)).toMatchObject({ id: 'confirmation', current: true })
    expect(pending.find((s) => s.id === 'loss')!.lines[0].text).toBe(
      'Robo · 19 oct 2026, 19:00 · Alcalá de Henares',
    )
    expect(pending.find((s) => s.id === 'police')!.lines[0].text).toBe('Denuncia presentada')

    const confirmed = stages(claim(complete, 'confirmed'))
    expect(confirmed.every((s) => s.state === 'done' && !s.current)).toBe(true)
  })

  it('omits conditional stages the claim does not require', () => {
    const ids = stages(claim(IDENTIFIED)).map((s) => s.id)
    expect(ids).toEqual(['identification', 'confirmation'])
  })
})
