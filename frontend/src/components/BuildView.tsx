import React from 'react'

interface Step {
  id: string
  label: string
  status: string
  detail?: string
  pct?: number
}

export interface ActiveJobItem {
  videoId: string
  title: string
  pct?: number
  done?: boolean
  error?: string
}

interface Props {
  active: boolean
  steps: Step[]
  error: string
  done: boolean
  videoTitle: string
  activeJobs?: ActiveJobItem[]
  currentJobId?: string | null
  onSelectJob?: (id: string) => void
  onOpen: () => void
  onNewVideo: () => void
  onCancel?: () => void
}

export default function BuildView({
  active,
  steps,
  error,
  done,
  videoTitle,
  activeJobs,
  currentJobId,
  onSelectJob,
  onOpen,
  onNewVideo,
  onCancel
}: Props) {
  return (
    <section className={`view build-view ${active ? 'active' : ''}`}>
      <div className="build-wrap">
        {/* Parallel Jobs Tabs Switcher */}
        {activeJobs && activeJobs.length > 1 && (
          <div style={{ display: 'flex', gap: 8, marginBottom: 24, overflowX: 'auto', paddingBottom: 4 }}>
            {activeJobs.map(j => {
              const isActive = j.videoId === currentJobId
              return (
                <button
                  key={j.videoId}
                  className={`btn sm ${isActive ? 'primary' : ''}`}
                  onClick={() => onSelectJob?.(j.videoId)}
                  style={{
                    maxWidth: 240,
                    textOverflow: 'ellipsis',
                    overflow: 'hidden',
                    whiteSpace: 'nowrap',
                    borderColor: isActive ? 'var(--accent)' : 'var(--line)'
                  }}
                  title={j.title}
                >
                  {j.done ? '✓ ' : j.error ? '⚠️ ' : '⏳ '}
                  {j.title || j.videoId} {j.pct != null && !j.done ? `(${j.pct}%)` : ''}
                </button>
              )
            })}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
          <div>
            <h2 className="build-title">{done ? 'Notes ready!' : 'Building your notes'}</h2>
            <div className="build-sub">{videoTitle}</div>
          </div>
          {!done && !error && onCancel && (
            <button
              className="btn sm"
              style={{ color: 'var(--err)', borderColor: 'rgba(248,113,113,0.3)', marginTop: 4 }}
              onClick={onCancel}
              title="Stop and cancel this build"
            >
              ✕ Cancel build
            </button>
          )}
        </div>

        <div className="steps-container">
          {steps.map(s => (
            <div key={s.id} className={`step ${s.status}`}>
              <div className="step-dot">{s.status === 'done' ? '✓' : ''}</div>
              <div className="step-body">
                <div className="step-label">{s.label}</div>
                {s.detail && <div className="step-detail">{s.detail}</div>}
                {s.pct != null && (
                  <div className="step-bar">
                    <div className="step-bar-fill" style={{ width: `${s.pct}%` }} />
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        {error && <div className="err-text" style={{ marginTop: 18 }}>{error}</div>}

        {(done || error) && (
          <div className="build-done-actions">
            {done && (
              <button className="btn primary" onClick={onOpen}>
                Open study notes →
              </button>
            )}
            <button className="btn" onClick={onNewVideo}>
              Try another video
            </button>
          </div>
        )}
      </div>
    </section>
  )
}
