import { useEffect, useRef } from 'react'

import type { TranscriptEntry } from '../conversation/protocol'

export function Transcript({ entries, typing }: { entries: TranscriptEntry[]; typing: boolean }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length, typing])

  return (
    <div className="transcript" aria-live="polite">
      {entries.map((entry, i) => (
        <p key={i} className={`bubble bubble-${entry.role}`}>
          {entry.text}
        </p>
      ))}
      {typing && <p className="bubble bubble-agent bubble-typing">…</p>}
      <div ref={end} />
    </div>
  )
}
