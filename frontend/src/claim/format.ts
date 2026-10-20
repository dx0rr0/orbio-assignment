/** Spanish labels and display values for the claim panel. */

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

interface LossWindow {
  start: string
  end: string
}

interface LossLocation {
  way: string | null
  municipality: string
  province: string
  country: string
}

export function formatValue(name: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Sí' : 'No'
  switch (name) {
    case 'loss_type':
      return LOSS_TYPES[value as string] ?? String(value)
    case 'loss_window': {
      const { start, end } = value as LossWindow
      const from = dateTime.format(new Date(start))
      return start === end ? from : `${from} – ${dateTime.format(new Date(end))}`
    }
    case 'loss_location': {
      const { way, municipality, province, country } = value as LossLocation
      return [way, municipality, province, country].filter(Boolean).join(', ')
    }
    default:
      return String(value)
  }
}
