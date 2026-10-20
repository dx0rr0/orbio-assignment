import { describe, expect, it } from 'vitest'

import { formatValue } from './format'

describe('formatValue', () => {
  it('shows missing values as a dash and booleans in Spanish', () => {
    expect(formatValue('injuries', null)).toBe('—')
    expect(formatValue('injuries', true)).toBe('Sí')
    expect(formatValue('injuries', false)).toBe('No')
  })

  it('writes current plates with a space and leaves other plates alone', () => {
    expect(formatValue('plate', '8305HJT')).toBe('8305 HJT')
    expect(formatValue('other_vehicle_plate', 'M1234AB')).toBe('M1234AB')
  })

  it('translates the loss type', () => {
    expect(formatValue('loss_type', 'broken_glass')).toBe('Rotura de lunas')
  })

  it('joins the location parts that are present', () => {
    expect(
      formatValue('loss_location', {
        way: null,
        municipality: 'Valencia',
        province: 'Valencia',
        country: 'España',
      }),
    ).toBe('Valencia, Valencia, España')
  })

  it('shows an instant once and an interval as a range, in Madrid time', () => {
    const instant = '2026-10-19T19:00:00+02:00'
    expect(formatValue('loss_window', { start: instant, end: instant })).toBe('19 oct 2026, 19:00')
    const range = formatValue('loss_window', {
      start: '2026-10-13T20:00:00+00:00',
      end: '2026-10-14T08:00:00+02:00',
    })
    expect(range).toBe('13 oct 2026, 22:00 – 14 oct 2026, 8:00')
  })
})
