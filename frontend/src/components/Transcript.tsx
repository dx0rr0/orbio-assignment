import { useEffect, useRef } from 'react'

import type { TranscriptEntry } from '../conversation/protocol'

// Past this length (a read-back, a closing) the agent's current message is shown smaller.
const LONG_MESSAGE = 160

/** The whole conversation, with the agent's latest words shown large, as in a live call. */
export function Transcript({ entries, thinking }: { entries: TranscriptEntry[]; thinking: boolean }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length, thinking])

  // The agent's last message is the one being said now, unless the declarant spoke since.
  const lastAgent = entries.at(-1)?.role === 'agent' && !thinking ? entries.length - 1 : -1

  return (
    <div className="transcript" aria-live="polite">
      {entries.map((entry, i) =>
        i === lastAgent && !entry.alert ? (
          <div
            key={i}
            className={entry.text.length > LONG_MESSAGE ? 'line line-now line-now-long' : 'line line-now'}
          >
            <span className="speaker">Asistente</span>
            <p>{entry.text}</p>
          </div>
        ) : (
          <div key={i} className={`line line-${entry.role}`}>
            <span className="speaker">{entry.role === 'agent' ? 'Asistente' : 'Usted'}</span>
            <p className={entry.alert ? 'alert' : undefined}>{entry.text}</p>
          </div>
        ),
      )}
      {thinking && (
        <div className="line line-agent">
          <span className="speaker">Asistente</span>
          <p className="thinking">Pensando…</p>
        </div>
      )}
      <div ref={end} />
    </div>
  )
}
