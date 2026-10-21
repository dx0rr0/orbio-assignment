/** Spanish labels and display values for the claim panel. */

import { z } from 'zod'

export const FIELD_LABELS: Record<string, string> = {
  policy_number: 'Póliza',
  plate: 'Matrícula',
  declarant_name: 'Declarante',
  driver_is_policyholder: '¿Conducía el titular?',
  driver_name: 'Conductor',
  loss_type: 'Tipo de siniestro',
  loss_window: 'Momento',
  loss_location: 'Lugar',
  description: 'Descripción',
  injuries: 'Heridos',
  vehicle_immobilized: 'Vehículo inmovilizado',
  other_vehicle_involved: 'Otro vehículo implicado',
  other_vehicle_plate: 'Matrícula del otro vehículo',
  friendly_report_signed: 'Parte amistoso firmado',
  police_report_filed: 'Denuncia presentada',
}

export const STATUS_LABELS: Record<string, string> = {
  in_progress: 'En curso',
  pending_confirmation: 'Pendiente de confirmación',
  confirmed: 'Confirmada',
  incomplete: 'Incompleta',
  not_covered: 'No cubierta',
  escalated: 'Derivada a tramitador',
}

export const CLOSURE_LABELS: Record<string, string> = {
  data_refused: 'El declarante no quiso dar un dato obligatorio',
  abandoned: 'Conversación abandonada',
  retries_exhausted: 'Demasiados intentos fallidos',
  system_error: 'Error técnico persistente',
  outside_coverage_period: 'Fuera del periodo de vigencia',
  partial_coverage: 'En parte fuera del periodo de vigencia',
  outside_spain: 'Ocurrido fuera de España',
}

export const URGENCY_LABELS: Record<string, string> = {
  high: 'Alta',
  medium: 'Media',
  low: 'Baja',
}

const LOSS_TYPES: Record<string, string> = {
  collision: 'Colisión',
  theft: 'Robo',
  broken_glass: 'Rotura de lunas',
  parked_damage: 'Daños con el vehículo aparcado',
  other: 'Otro',
}

const dateTime = new Intl.DateTimeFormat('es-ES', {
  dateStyle: 'medium',
  timeStyle: 'short',
  timeZone: 'Europe/Madrid',
})

// Field values come from the server; these are the structured ones it sends.
const LossWindow = z.object({
  start: z.iso.datetime({ offset: true }),
  end: z.iso.datetime({ offset: true }),
})

export const LossLocation = z.object({
  way: z.string().nullable(),
  municipality: z.string(),
  province: z.string(),
  country: z.string(),
})

/** Current Spanish plates ('8305HJT') are written '8305 HJT'; others stay as they are. */
export function displayPlate(plate: string): string {
  return /^\d{4}[A-Z]{3}$/.test(plate) ? `${plate.slice(0, 4)} ${plate.slice(4)}` : plate
}

export function formatValue(name: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Sí' : 'No'
  switch (name) {
    case 'plate':
    case 'other_vehicle_plate':
      return typeof value === 'string' ? displayPlate(value) : asText(value)
    case 'loss_type':
      return (typeof value === 'string' && LOSS_TYPES[value]) || asText(value)
    case 'loss_window': {
      const lossWindow = LossWindow.safeParse(value)
      if (!lossWindow.success) return asText(value)
      const { start, end } = lossWindow.data
      const from = dateTime.format(new Date(start))
      return start === end ? from : `${from} – ${dateTime.format(new Date(end))}`
    }
    case 'loss_location': {
      const location = LossLocation.safeParse(value)
      if (!location.success) return asText(value)
      const { way, municipality, province, country } = location.data
      return [way, municipality, province, country].filter(Boolean).join(', ')
    }
    default:
      return asText(value)
  }
}

/** A value of an unexpected shape, shown as it came rather than as '[object Object]'. */
function asText(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}
