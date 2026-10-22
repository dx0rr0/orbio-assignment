import { useEffect, useState } from 'react'

export type AgentActivity = 'listening' | 'thinking' | 'speaking' | 'waiting'

const ACTIVITY_LABELS: Record<AgentActivity, string> = {
  listening: 'Le escucho…',
  thinking: 'Pensando…',
  speaking: 'El asistente está hablando',
  waiting: 'Su turno',
}

function useElapsed(startedAt: number | null, running: boolean): string | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  if (startedAt === null || !running) return null
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

/** "Call in progress" bar: connection, elapsed time and what the agent is doing. */
export function CallHeader({
  idle,
  connected,
  ended,
  startedAt,
  activity,
}: {
  idle: boolean
  connected: boolean
  ended: boolean
  startedAt: number | null
  activity: AgentActivity
}) {
  const live = connected && !ended
  const elapsed = useElapsed(startedAt, live)
  const title = idle
    ? 'Llamada no iniciada'
    : ended
      ? 'Llamada finalizada'
      : connected
        ? 'Llamada en curso'
        : 'Conectando…'

  return (
    <header className="call-header">
      <div className="call-state">
        <span className={live ? 'call-dot call-dot-live' : 'call-dot'} aria-hidden="true" />
        <span className="call-title">{title}</span>
        {elapsed && <span className="call-time">{elapsed}</span>}
      </div>
      {live && <span className={`activity activity-${activity}`}>{ACTIVITY_LABELS[activity]}</span>}
    </header>
  )
}
