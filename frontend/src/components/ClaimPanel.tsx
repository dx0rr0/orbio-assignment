import { CLOSURE_LABELS, STATUS_LABELS, URGENCY_LABELS, displayPlate } from '../claim/format'
import { stages } from '../claim/stages'
import type { ClaimView, Summary } from '../conversation/protocol'

function textField(claim: ClaimView, name: string): string | undefined {
  const value = claim.fields.find((field) => field.name === name && field.state === 'valid')?.value
  return typeof value === 'string' ? value : undefined
}

export function ClaimPanel({ claim, summary }: { claim: ClaimView | null; summary: Summary | null }) {
  if (claim === null) {
    return (
      <aside className="panel">
        <p className="panel-muted">Aquí verá los datos de su declaración.</p>
      </aside>
    )
  }

  const policy = textField(claim, 'policy_number')
  const plate = textField(claim, 'plate')

  return (
    <aside className="panel" aria-label="Su declaración">
      <header className="panel-header">
        <h2>Su declaración</h2>
        <div className="panel-badges">
          <span className={`status status-${claim.status}`}>
            {STATUS_LABELS[claim.status] ?? claim.status}
          </span>
          {claim.urgency && (
            <span className={`urgency urgency-${claim.urgency}`}>
              Urgencia {URGENCY_LABELS[claim.urgency].toLowerCase()}
            </span>
          )}
        </div>
      </header>
      {(policy || plate) && (
        <p className="panel-muted">
          {[policy && `Póliza ${policy}`, plate && displayPlate(plate)]
            .filter(Boolean)
            .join(' · ')}
        </p>
      )}

      {claim.reference && (
        <div className="reference">
          <span>Referencia</span>
          <strong>{claim.reference}</strong>
        </div>
      )}
      {claim.closure_reason && (
        <p className="closure">{CLOSURE_LABELS[claim.closure_reason] ?? claim.closure_reason}</p>
      )}

      <ol className="stages">
        {stages(claim).map((stage, i) => (
          <li
            key={stage.id}
            className={`stage stage-${stage.state}${stage.current ? ' stage-current' : ''}`}
          >
            <span className="stage-marker" aria-hidden="true">
              {stage.state === 'done' ? '✓' : stage.state === 'error' ? '!' : i + 1}
            </span>
            <div>
              <div className="stage-title">{stage.title}</div>
              {stage.lines.map((line) => (
                <div key={line.text} className={line.error ? 'stage-line stage-line-error' : 'stage-line'}>
                  {line.text}
                </div>
              ))}
            </div>
          </li>
        ))}
      </ol>

      {summary && (
        <section className="summary">
          <h3>Resumen para el tramitador</h3>
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
