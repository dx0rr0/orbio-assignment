import {
  CLOSURE_LABELS,
  FIELD_LABELS,
  formatValue,
  STATUS_LABELS,
  URGENCY_LABELS,
} from '../claim/format'
import type { ClaimView, Summary } from '../conversation/protocol'

const STATE_ICONS = { empty: '○', valid: '✓', invalid: '!' } as const

export function ClaimPanel({ claim, summary }: { claim: ClaimView | null; summary: Summary | null }) {
  if (claim === null) return <aside className="panel">Conectando…</aside>

  return (
    <aside className="panel" aria-label="Datos de la declaración">
      <header className="panel-header">
        <span className={`status status-${claim.status}`}>{STATUS_LABELS[claim.status]}</span>
        {claim.urgency && (
          <span className={`urgency urgency-${claim.urgency}`}>
            Urgencia {URGENCY_LABELS[claim.urgency].toLowerCase()}
          </span>
        )}
      </header>

      {claim.reference && <p className="reference">{claim.reference}</p>}
      {claim.closure_reason && (
        <p className="closure">{CLOSURE_LABELS[claim.closure_reason] ?? claim.closure_reason}</p>
      )}

      <ul className="fields">
        {claim.fields.map((field) => (
          <li key={field.name} className={`field field-${field.state}`}>
            <span className="field-icon" aria-hidden>
              {STATE_ICONS[field.state]}
            </span>
            <span className="field-label">{FIELD_LABELS[field.name] ?? field.name}</span>
            <span className="field-value">
              {field.state === 'invalid' ? field.error : formatValue(field.name, field.value)}
            </span>
          </li>
        ))}
      </ul>

      {summary && (
        <section className="summary">
          <h2>Resumen para el tramitador</h2>
          <ul>
            {summary.key_points.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
          <h3>Siguientes pasos</h3>
          <ol>
            {summary.next_steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </section>
      )}
    </aside>
  )
}
