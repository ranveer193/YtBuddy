import React from 'react'
import type { LibraryVideo } from '../types'

import { apiUrl } from '../lib/api'

interface Props {
  active: boolean
  cur: LibraryVideo | null
  onChatOpen: () => void
  unread: boolean
}

export default function WorkView({ active, cur, onChatOpen, unread }: Props) {
  if (!cur) {
    return (
      <section className={`view work-view ${active ? 'active' : ''}`}>
        <div style={{ margin: 'auto', color: 'var(--mute)' }}>Select a video to view notes.</div>
      </section>
    )
  }

  return (
    <section className={`view work-view ${active ? 'active' : ''}`}>
      <div className="notes-pane">
        <div className="notes-bar">
          <div className="notes-bar-title" title={cur.title}>
            {cur.title}
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              className="btn sm"
              onClick={() => {
                const frame = document.getElementById('notes-frame') as HTMLIFrameElement
                frame?.contentWindow?.print()
              }}
            >
              Save PDF
            </button>
            <button className="btn sm primary" onClick={onChatOpen}>
              💬 Ask AI {unread && '•'}
            </button>
          </div>
        </div>
        <iframe
          id="notes-frame"
          className="notes-iframe"
          src={apiUrl(`/api/notes/${cur.video_id}/html`)}
          title={cur.title}
        />
      </div>
    </section>
  )
}
