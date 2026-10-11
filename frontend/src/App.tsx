import { useState, useEffect, useCallback, useRef } from 'react'
import type { LibraryVideo, VideoInfo, LangOption, Message, StepEvent, IngestJob } from './types'
import { fetchLibrary, deleteVideo, streamSSE, fetchJobs, fetchJob, cancelJob, fmtTs } from './lib/api'
import FindView from './components/FindView'
import BuildView from './components/BuildView'
import WorkView from './components/WorkView'
import ChatModal from './components/ChatModal'

type View = 'find' | 'build' | 'work'

interface Step {
  id: string
  label: string
  status: string
  detail?: string
  pct?: number
}

interface VideoBuildState {
  videoId: string
  title: string
  thumbnail: string
  steps: Step[]
  error: string
  done: boolean
  currentStep?: string
  pct?: number
}

function eventsToSteps(events: any[]): Step[] {
  const stepMap = new Map<string, Step>()
  let pct = 0
  for (const ev of events || []) {
    if (ev.type === 'step') {
      stepMap.set(ev.id, {
        id: ev.id,
        label: ev.label,
        status: ev.status,
        detail: ev.detail,
        pct: ev.id === 'summarize' ? pct : undefined
      })
    } else if (ev.type === 'progress') {
      pct = Math.round((ev.done / (ev.total || 1)) * 100)
      const s = stepMap.get('summarize')
      if (s) stepMap.set('summarize', { ...s, pct })
    }
  }
  return Array.from(stepMap.values())
}

export default function App() {
  // ── Library ──────────────────────────────────────────────
  const [lib, setLib] = useState<LibraryVideo[]>([])
  const [cur, setCur] = useState<LibraryVideo | null>(null)
  const loadLib = useCallback(async () => {
    try { setLib(await fetchLibrary()) } catch { /* ignore */ }
  }, [])
  useEffect(() => { loadLib() }, [loadLib])

  // ── Background Server Jobs ───────────────────────────────
  const [serverJobs, setServerJobs] = useState<IngestJob[]>([])
  const activeStreamsRef = useRef<Set<string>>(new Set())

  // ── View routing ─────────────────────────────────────────
  const [view, setView] = useState<View>('find')
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  // ── Language selection modal ──────────────────────────────
  const [pick, setPick] = useState<VideoInfo | null>(null)
  const [langs, setLangs] = useState<LangOption[]>([])
  const [lang, setLang] = useState<string | null>(null)
  const [langsLoading, setLangsLoading] = useState(false)

  // ── Multi-video Build States ──────────────────────────────
  const [buildStates, setBuildStates] = useState<Record<string, VideoBuildState>>({})
  const [activeBuildId, setActiveBuildId] = useState<string | null>(null)

  // ── Chat ──────────────────────────────────────────────────
  const [chatOpen, setChatOpen] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [scope, setScope] = useState<Set<string>>(new Set())
  const sessionId = useRef(crypto.randomUUID())

  // ── Synchronize with Server Jobs ──────────────────────────
  const refreshJobs = useCallback(async () => {
    try {
      const js = await fetchJobs()
      setServerJobs(js)

      // Sync server jobs into buildStates
      setBuildStates(prev => {
        const next = { ...prev }
        for (const j of js) {
          const pastSteps = eventsToSteps(j.events || [])
          const isDone = j.status === 'done'
          const isError = j.status === 'error' || j.status === 'cancelled'

          if (!next[j.video_id]) {
            next[j.video_id] = {
              videoId: j.video_id,
              title: j.title || j.video_id,
              thumbnail: j.thumbnail,
              steps: pastSteps,
              error: isError ? (j.error || 'Failed') : '',
              done: isDone,
              currentStep: j.current_step,
              pct: j.pct ?? undefined,
            }
          } else {
            // Keep steps fresh if not currently streaming
            if (!activeStreamsRef.current.has(j.video_id) && pastSteps.length > next[j.video_id].steps.length) {
              next[j.video_id].steps = pastSteps
            }
            if (isDone) next[j.video_id].done = true
            if (isError) next[j.video_id].error = j.error || 'Failed'
            next[j.video_id].currentStep = j.current_step
            if (j.pct != null) next[j.video_id].pct = j.pct
          }
        }
        return next
      })
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    refreshJobs()
    const timer = setInterval(refreshJobs, 3000)
    return () => clearInterval(timer)
  }, [refreshJobs])

  // ── Re-attach stream for an in-progress job ────────────────
  const attachToJobStream = useCallback(async (vid: string) => {
    if (activeStreamsRef.current.has(vid)) return

    // Immediately fetch detailed job with events so stages display without delay
    const detailed = await fetchJob(vid)
    if (detailed) {
      const initialSteps = eventsToSteps(detailed.events || [])
      setBuildStates(prev => ({
        ...prev,
        [vid]: {
          videoId: vid,
          title: detailed.title || detailed.video_id,
          thumbnail: detailed.thumbnail,
          steps: initialSteps,
          error: detailed.error || '',
          done: detailed.status === 'done',
          currentStep: detailed.current_step,
          pct: detailed.pct ?? undefined,
        }
      }))
      if (detailed.status !== 'running') return
    }

    activeStreamsRef.current.add(vid)
    const stepMap = new Map<string, Step>()
    if (detailed?.events) {
      for (const st of eventsToSteps(detailed.events)) stepMap.set(st.id, st)
    }

    try {
      for await (const ev of streamSSE(`/api/jobs/${vid}/stream`)) {
        if (ev.type === 'step') {
          const s = ev as StepEvent
          stepMap.set(s.id, {
            id: s.id,
            label: s.label,
            status: s.status,
            detail: s.detail,
          })
          setBuildStates(prev => {
            const curState = prev[vid]
            if (!curState) return prev
            return {
              ...prev,
              [vid]: {
                ...curState,
                steps: Array.from(stepMap.values()),
                currentStep: `${s.label}${s.detail ? ' · ' + s.detail : ''}`
              }
            }
          })
        } else if (ev.type === 'progress') {
          const pct = Math.round((ev.done / (ev.total || 1)) * 100)
          const s = stepMap.get('summarize')
          if (s) {
            stepMap.set('summarize', { ...s, pct })
            setBuildStates(prev => {
              const curState = prev[vid]
              if (!curState) return prev
              return {
                ...prev,
                [vid]: {
                  ...curState,
                  steps: Array.from(stepMap.values()),
                  pct,
                  currentStep: `Summarizing ${ev.done}/${ev.total}`
                }
              }
            })
          }
        } else if (ev.type === 'done') {
          setBuildStates(prev => {
            const curState = prev[vid]
            if (!curState) return prev
            return { ...prev, [vid]: { ...curState, done: true, pct: 100, currentStep: 'Notes complete!' } }
          })
          await loadLib()
          await refreshJobs()
          break
        } else if (ev.type === 'error') {
          setBuildStates(prev => {
            const curState = prev[vid]
            if (!curState) return prev
            return { ...prev, [vid]: { ...curState, error: ev.message, currentStep: `Failed: ${ev.message}` } }
          })
          break
        }
      }
    } catch {
      // Stream closed or error
    } finally {
      activeStreamsRef.current.delete(vid)
    }
  }, [loadLib, refreshJobs])

  // ── Open video from library ───────────────────────────────
  function openVideo(id: string) {
    const v = lib.find(x => x.video_id === id)
    if (!v) return
    setCur(v)
    setScope(new Set([id]))
    setView('work')
    setMobileMenuOpen(false)
  }

  // ── Delete video ──────────────────────────────────────────
  async function handleDelete(id: string) {
    if (!confirm('Remove this video and its vectors?')) return
    await deleteVideo(id)
    setScope(s => { s.delete(id); return new Set(s) })
    if (cur?.video_id === id) { setCur(null); setView('find') }
    loadLib()
  }

  // ── Cancel in-progress job (Cross button) ─────────────────
  async function handleCancelJob(vid: string) {
    if (!confirm('Cancel note generation for this video?')) return
    await cancelJob(vid)
    activeStreamsRef.current.delete(vid)
    setBuildStates(prev => {
      const next = { ...prev }
      delete next[vid]
      return next
    })
    if (activeBuildId === vid) {
      setActiveBuildId(null)
      setView('find')
    }
    await refreshJobs()
  }

  // ── Open in-progress build screen ─────────────────────────
  function openBuildJob(vid: string) {
    setActiveBuildId(vid)
    setView('build')
    setMobileMenuOpen(false)
    attachToJobStream(vid)
  }

  // ── Choose a video to process ─────────────────────────────
  async function choosePick(info: VideoInfo) {
    const existing = lib.find(v => v.video_id === info.video_id)
    if (existing) { openVideo(info.video_id); return }

    if (buildStates[info.video_id] && !buildStates[info.video_id].done && !buildStates[info.video_id].error) {
      openBuildJob(info.video_id)
      return
    }

    // Limit users to maximum 2 parallel video processing jobs
    const activeCount = Object.values(buildStates).filter(b => !b.done && !b.error).length
    if (activeCount >= 2 && !buildStates[info.video_id]) {
      alert('Maximum 2 videos can be processed in parallel. Please wait for an active job to finish or cancel one.')
      return
    }

    setPick(info)
    setLang(null)
    setLangs([])
    setLangsLoading(true)
    try {
      const { fetchLanguages } = await import('./lib/api')
      const ls = await fetchLanguages(info.video_id)
      setLangs(ls)
      if (ls.length === 1) setLang(ls[0].code)
    } catch {
      setLangs([])
    } finally {
      setLangsLoading(false)
    }
  }

  function dismissModal() {
    setPick(null)
    setLang(null)
    setLangs([])
  }

  // ── Start Build for Selected Video ────────────────────────
  async function startBuild() {
    if (!pick || !lang) return
    const targetPick = pick
    const targetLang = lang
    const vid = targetPick.video_id

    const activeCount = Object.values(buildStates).filter(b => !b.done && !b.error).length
    if (activeCount >= 2 && !buildStates[vid]) {
      alert('Maximum 2 videos can be processed in parallel. Please wait for one of them to finish.')
      return
    }

    dismissModal()

    setBuildStates(prev => ({
      ...prev,
      [vid]: {
        videoId: vid,
        title: targetPick.title,
        thumbnail: targetPick.thumbnail,
        steps: [],
        error: '',
        done: false,
        currentStep: 'Starting ingestion…',
        pct: 0,
      }
    }))

    setActiveBuildId(vid)
    setView('build')

    // Start background stream
    activeStreamsRef.current.add(vid)
    const stepMap = new Map<string, Step>()

    const updateState = (
      stepItem?: Step,
      isDone?: boolean,
      errMsg?: string,
      currentStepDesc?: string,
      percent?: number
    ) => {
      if (stepItem) stepMap.set(stepItem.id, stepItem)
      setBuildStates(prev => {
        const existing = prev[vid] || {
          videoId: vid,
          title: targetPick.title,
          thumbnail: targetPick.thumbnail,
          steps: [],
          error: '',
          done: false,
        }
        return {
          ...prev,
          [vid]: {
            ...existing,
            steps: Array.from(stepMap.values()),
            done: isDone ?? existing.done,
            error: errMsg ?? existing.error,
            currentStep: currentStepDesc ?? existing.currentStep,
            pct: percent ?? existing.pct,
          }
        }
      })
    }

    ;(async () => {
      let pct = 0
      try {
        for await (const ev of streamSSE('/api/ingest', { video_id: vid, language: targetLang })) {
          if (ev.type === 'step') {
            const s = ev as StepEvent
            const stepDesc = `${s.label}${s.detail ? ' · ' + s.detail : ''}`
            updateState(
              { id: s.id, label: s.label, status: s.status, detail: s.detail, pct: s.id === 'summarize' ? pct : undefined },
              false,
              undefined,
              stepDesc
            )
          } else if (ev.type === 'progress') {
            pct = Math.round((ev.done / (ev.total || 1)) * 100)
            const s = stepMap.get('summarize')
            if (s) {
              updateState({ ...s, pct }, false, undefined, `Summarizing ${ev.done}/${ev.total}`, pct)
            }
          } else if (ev.type === 'done') {
            updateState(undefined, true, undefined, 'Notes complete!', 100)
            await loadLib()
            await refreshJobs()
          } else if (ev.type === 'error') {
            throw new Error(ev.message)
          }
        }
      } catch (e: unknown) {
        const err = e instanceof Error ? e.message : 'Build failed'
        updateState(undefined, false, err, `Failed: ${err}`)
      } finally {
        activeStreamsRef.current.delete(vid)
      }
    })()
  }

  // ── Open Notes After Build ────────────────────────────────
  async function openAfterBuild(vid: string) {
    await loadLib()
    const v = lib.find((x: LibraryVideo) => x.video_id === vid)
    if (v) {
      setCur(v)
      setScope(new Set([vid]))
      setView('work')
    } else {
      const freshLib = await fetchLibrary()
      setLib(freshLib)
      const freshV = freshLib.find((x: LibraryVideo) => x.video_id === vid)
      if (freshV) {
        setCur(freshV)
        setScope(new Set([vid]))
        setView('work')
      }
    }
  }

  // Active in-progress jobs list
  const activeJobsList: {
    video_id: string
    title: string
    thumbnail: string
    current_step: string
    pct?: number
  }[] = []

  const activeIds = new Set<string>()

  // Local build states
  Object.values(buildStates).forEach(b => {
    if (!b.done && !b.error) {
      activeJobsList.push({
        video_id: b.videoId,
        title: b.title,
        thumbnail: b.thumbnail,
        current_step: b.currentStep || 'Processing…',
        pct: b.pct,
      })
      activeIds.add(b.videoId)
    }
  })

  // Server background jobs
  serverJobs.forEach(sj => {
    if (sj.status === 'running' && !activeIds.has(sj.video_id)) {
      activeJobsList.push({
        video_id: sj.video_id,
        title: sj.title || sj.video_id,
        thumbnail: sj.thumbnail,
        current_step: sj.current_step,
        pct: sj.pct ?? undefined,
      })
    }
  })

  const currentBuild = activeBuildId ? buildStates[activeBuildId] : null

  return (
    <>
      {/* Mobile Top Header */}
      <header className="mobile-topbar">
        <button
          className="btn icon"
          onClick={() => setMobileMenuOpen(true)}
          title="Open menu"
          style={{ fontSize: 20 }}
        >
          ☰
        </button>
        <div className="mobile-topbar-title">
          <div className="mobile-topbar-logo">◎</div>
          NoteLens
        </div>
        <button
          className="btn sm primary"
          onClick={() => {
            setPick(null)
            setView('find')
          }}
        >
          ＋ New
        </button>
      </header>

      {/* Sidebar Backdrop for Mobile */}
      <div
        className={`sidebar-backdrop ${mobileMenuOpen ? 'show' : ''}`}
        onClick={() => setMobileMenuOpen(false)}
      />

      {/* Sidebar / Mobile Drawer */}
      <aside className={`sidebar ${mobileMenuOpen ? 'mobile-open' : ''}`}>
        <div className="sidebar-brand">
          <div className="sidebar-logo">◎</div>
          NoteLens
          {/* Close button inside mobile drawer */}
          <button
            className="btn icon sm"
            style={{ marginLeft: 'auto' }}
            onClick={() => setMobileMenuOpen(false)}
            title="Close menu"
          >
            ✕
          </button>
        </div>

        {/* ＋ New video button */}
        <button
          className="btn primary sidebar-new"
          onClick={() => {
            setPick(null)
            setView('find')
            setMobileMenuOpen(false)
          }}
        >
          ＋ New video
        </button>

        {/* In-Progress Section */}
        {activeJobsList.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div className="sidebar-label" style={{ color: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span>In Progress ({activeJobsList.length})</span>
              <span className="typing-dots" style={{ transform: 'scale(0.8)' }}>
                <span></span><span></span><span></span>
              </span>
            </div>
            <div style={{ padding: '0 8px' }}>
              {activeJobsList.map(job => (
                <div
                  key={job.video_id}
                  className={`job-item ${view === 'build' && activeBuildId === job.video_id ? 'active' : ''}`}
                  onClick={() => openBuildJob(job.video_id)}
                  title={job.title}
                >
                  <div className="job-thumb-wrap">
                    <img src={job.thumbnail} alt="" />
                    <div className="job-spinner-overlay">
                      <div className="job-spin-icon" />
                    </div>
                  </div>
                  <div className="job-item-text" style={{ paddingRight: 20 }}>
                    <div className="job-item-title">{job.title}</div>
                    <div className="job-item-step">{job.current_step}</div>
                    {job.pct != null && (
                      <div className="job-item-bar">
                        <div className="job-item-bar-fill" style={{ width: `${job.pct}%` }} />
                      </div>
                    )}
                  </div>
                  {/* Cross button to cancel / remove mistaken build */}
                  <button
                    className="job-item-del"
                    onClick={e => {
                      e.stopPropagation()
                      handleCancelJob(job.video_id)
                    }}
                    title="Cancel processing"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Library Section */}
        <div className="sidebar-label">Library ({lib.length})</div>
        <div className="sidebar-lib">
          {lib.length === 0 ? (
            <div className="sidebar-label" style={{ textTransform: 'none', letterSpacing: 0, paddingTop: 10 }}>
              No notes saved yet
            </div>
          ) : (
            lib.map(v => (
              <div
                key={v.video_id}
                className={`lib-item ${cur?.video_id === v.video_id && view === 'work' ? 'active' : ''}`}
                onClick={() => openVideo(v.video_id)}
              >
                <img src={v.thumbnail} alt="" />
                <div className="lib-item-text">
                  <div className="lib-item-title">{v.title}</div>
                  <div className="lib-item-meta">{fmtTs(v.duration)} · {v.chunks} chunks</div>
                </div>
                <button
                  className="lib-item-del"
                  onClick={e => {
                    e.stopPropagation()
                    handleDelete(v.video_id)
                  }}
                  title="Delete from library"
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="main">
        <FindView
          active={view === 'find'}
          lib={lib}
          onChoose={choosePick}
        />

        <BuildView
          active={view === 'build'}
          steps={currentBuild?.steps ?? []}
          error={currentBuild?.error ?? ''}
          done={currentBuild?.done ?? false}
          videoTitle={currentBuild?.title ?? ''}
          activeJobs={Object.values(buildStates).map(b => ({
            videoId: b.videoId,
            title: b.title,
            pct: b.pct,
            done: b.done,
            error: b.error,
          }))}
          currentJobId={activeBuildId}
          onSelectJob={vid => {
            setActiveBuildId(vid)
            attachToJobStream(vid)
          }}
          onOpen={() => activeBuildId && openAfterBuild(activeBuildId)}
          onNewVideo={() => {
            setPick(null)
            setView('find')
          }}
          onCancel={() => activeBuildId && handleCancelJob(activeBuildId)}
        />

        <WorkView
          active={view === 'work'}
          cur={cur}
          onChatOpen={() => setChatOpen(true)}
          unread={messages.length > 0}
        />
      </main>

      {/* Mobile Bottom Navigation Bar */}
      <nav className="mobile-bottom-nav">
        <button
          className={`nav-tab ${view === 'find' ? 'active' : ''}`}
          onClick={() => {
            setPick(null)
            setView('find')
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
          Search
        </button>

        <button
          className={`nav-tab ${view === 'build' ? 'active' : ''}`}
          onClick={() => {
            if (activeJobsList.length > 0) {
              openBuildJob(activeJobsList[0].video_id)
            } else if (activeBuildId) {
              setView('build')
            } else {
              setView('find')
            }
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          Progress
          {activeJobsList.length > 0 && <span className="nav-badge">{activeJobsList.length}</span>}
        </button>

        <button
          className="nav-tab"
          onClick={() => setMobileMenuOpen(true)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
          Library
          {lib.length > 0 && <span className="nav-badge" style={{ background: 'var(--panel)', border: '1px solid var(--line)', color: 'var(--ink)' }}>{lib.length}</span>}
        </button>

        <button
          className={`nav-tab ${view === 'work' ? 'active' : ''}`}
          disabled={!cur}
          style={{ opacity: cur ? 1 : 0.4 }}
          onClick={() => cur && setView('work')}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
          Notes
        </button>
      </nav>

      {/* Language Selection Modal */}
      {pick && !langsLoading && langs.length > 0 && (
        <div className="overlay" onClick={e => e.target === e.currentTarget && dismissModal()}>
          <div className="modal">
            <img src={pick.thumbnail} alt="" />
            <div className="modal-title">{pick.title}</div>
            <div className="modal-meta">{pick.channel} · {fmtTs(pick.duration)}</div>
            <div className="sidebar-label" style={{ padding: 0, marginBottom: 4 }}>Transcript language</div>
            {langs.map(l => (
              <div
                key={l.code}
                className={`lang-option ${lang === l.code ? 'selected' : ''}`}
                onClick={() => setLang(l.code)}
              >
                <span>{l.name} <small style={{ color: 'var(--mute)' }}>({l.code})</small></span>
                <span className="lang-badge">{l.generated ? 'auto' : 'manual'}</span>
              </div>
            ))}
            <button
              className="btn primary"
              style={{ width: '100%', marginTop: 16 }}
              disabled={!lang}
              onClick={startBuild}
            >
              Generate notes
            </button>
          </div>
        </div>
      )}

      {/* Loading transcripts spinner */}
      {pick && langsLoading && (
        <div className="overlay" onClick={e => e.target === e.currentTarget && dismissModal()}>
          <div className="modal">
            <img src={pick.thumbnail} alt="" />
            <div className="modal-title">{pick.title}</div>
            <div className="modal-meta" style={{ color: 'var(--mute)' }}>Loading available transcripts…</div>
          </div>
        </div>
      )}

      {/* Floating Chat Bubble (Work view) */}
      {view === 'work' && !chatOpen && (
        <button className="chat-bubble" onClick={() => setChatOpen(true)} title="Ask your videos">
          💬
          {messages.length > 0 && <span className="badge">{messages.length}</span>}
        </button>
      )}

      {/* Chat Slide-in Modal */}
      {chatOpen && (
        <ChatModal
          lib={lib}
          cur={cur}
          messages={messages}
          setMessages={setMessages}
          scope={scope}
          setScope={setScope}
          sessionId={sessionId.current}
          onClose={() => setChatOpen(false)}
          onResetSession={() => {
            sessionId.current = crypto.randomUUID()
            setMessages([])
          }}
        />
      )}
    </>
  )
}
