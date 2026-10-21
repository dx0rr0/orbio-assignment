/** The claim panel's stages: the claim's required fields grouped as the agent asks them. */

import type { ClaimField, ClaimView } from '../conversation/protocol'
import { FIELD_LABELS, LossLocation, formatValue } from './format'

export type StageState = 'done' | 'error' | 'pending'

export interface StageLine {
  text: string
  error: boolean
}

export interface Stage {
  id: string
  title: string
  state: StageState
  /** The first stage that is not done: the one the conversation is on. */
  current: boolean
  lines: StageLine[]
}

interface Group {
  id: string
  title: string
  fields: string[]
  /** One line for a finished stage, from its fields' values. */
  summary: (values: Record<string, unknown>) => string
}

const GROUPS: Group[] = [
  {
    id: 'identification',
    title: 'Identificación',
    fields: ['policy_number', 'plate', 'declarant_name'],
    summary: (v) => String(v.declarant_name),
  },
  {
    id: 'driver',
    title: 'Conductor',
    fields: ['driver_is_policyholder', 'driver_name'],
    summary: (v) => (v.driver_is_policyholder ? 'El titular' : `${v.driver_name} (no es el titular)`),
  },
  {
    id: 'loss',
    title: 'Qué, cuándo y dónde',
    fields: ['loss_type', 'loss_window', 'loss_location', 'description'],
    summary: (v) =>
      [
        formatValue('loss_type', v.loss_type),
        formatValue('loss_window', v.loss_window),
        municipality(v.loss_location),
      ].join(' · '),
  },
  {
    id: 'damage',
    title: 'Daños y heridos',
    fields: ['injuries', 'vehicle_immobilized'],
    summary: (v) =>
      `${v.injuries ? 'Con heridos' : 'Sin heridos'} · ` +
      (v.vehicle_immobilized ? 'vehículo inmovilizado' : 'el vehículo circula'),
  },
  {
    id: 'other_vehicle',
    title: 'Otro vehículo',
    fields: ['other_vehicle_involved', 'other_vehicle_plate', 'friendly_report_signed'],
    summary: (v) =>
      v.other_vehicle_involved
        ? `${formatValue('other_vehicle_plate', v.other_vehicle_plate)} · parte amistoso ` +
          (v.friendly_report_signed ? 'firmado' : 'sin firmar')
        : 'No hubo otro vehículo',
  },
  {
    id: 'police',
    title: 'Denuncia',
    fields: ['police_report_filed'],
    summary: (v) => (v.police_report_filed ? 'Denuncia presentada' : 'Sin denuncia'),
  },
]

function municipality(location: unknown): string {
  const parsed = LossLocation.safeParse(location)
  return parsed.success ? parsed.data.municipality : formatValue('loss_location', location)
}

function fieldLine(field: ClaimField): StageLine {
  const label = FIELD_LABELS[field.name] ?? field.name
  switch (field.state) {
    case 'valid':
      return { text: `${label}: ${formatValue(field.name, field.value)}`, error: false }
    case 'invalid':
      return { text: `${label}: ${field.error}`, error: true }
    case 'empty':
      return { text: `${label}: pendiente`, error: false }
  }
}

export function stages(claim: ClaimView): Stage[] {
  const byName = new Map(claim.fields.map((field) => [field.name, field]))
  const groups = GROUPS.map((group) => ({
    group,
    // Only the fields this claim requires: conditional groups appear once they apply.
    fields: group.fields.flatMap((name) => byName.get(name) ?? []),
  })).filter(({ fields }) => fields.length > 0)

  const states: StageState[] = groups.map(({ fields }) =>
    fields.every((f) => f.state === 'valid')
      ? 'done'
      : fields.some((f) => f.state === 'invalid')
        ? 'error'
        : 'pending',
  )
  const confirmed = claim.status === 'confirmed'
  // The confirmation stage comes last; -1 when everything is done.
  const currentIndex = [...states, confirmed ? 'done' : 'pending'].findIndex((s) => s !== 'done')

  const result: Stage[] = groups.map(({ group, fields }, i) => {
    const state = states[i]
    const current = i === currentIndex
    let lines: StageLine[] = []
    if (state === 'done') {
      const values = Object.fromEntries(fields.map((f) => [f.name, f.value]))
      lines = [{ text: group.summary(values), error: false }]
    } else if (current || state === 'error') {
      lines = fields.map(fieldLine)
    }
    return { id: group.id, title: group.title, state, current, lines }
  })

  result.push({
    id: 'confirmation',
    title: 'Confirmación',
    state: confirmed ? 'done' : 'pending',
    current: currentIndex === groups.length,
    lines: [
      {
        text: confirmed ? 'Confirmada por el declarante' : 'Le leeremos los datos para que los confirme',
        error: false,
      },
    ],
  })
  return result
}
