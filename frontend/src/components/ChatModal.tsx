import React, { useState, useEffect, useRef } from 'react'
import type { Message, LibraryVideo, Source, SSEEvent } from '../types'
import { streamSSE } from '../lib/api'

interface Props {
  lib: LibraryVideo[]
  cur: LibraryVideo | null
  messages: Message[]
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
  scope: Set<string>
  setScope: React.Dispatch<React.SetStateAction<Set<string>>>
  sessionId: string
  onClose: () => void
  onResetSession: () => void
}

function parseTs(v: string): number | null {
  if (!v.trim()) return null
  const parts = v.trim().split(':').map(Number).reverse()
  if (parts.some(isNaN)) return null
  return parts.reduce((acc, n, i) => acc + (n || 0) * Math.pow(60, i), 0)
}

function renderMarkdown(t: string): string {
  const esc = (s: string) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] || c))

  let s = esc(t)
    // Code blocks with language
    .replace(/```(?:([a-zA-Z0-9_-]+)\n)?([\s\S]*?)```/g, (_, l, c) => `<pre><code class="${l || ''}">${c.trim()}</code></pre>`)
    // Citations
    .replace(/\[S(\d+)\]/g, '<span class="cite">[$1]</span>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Bold
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    // Headers
    .replace(/^#{1,3}\s+(.+)$/gm, '<strong>$1</strong>')

  const lines = s.split('\n')
  const out: string[] = []
  let ul = false

  for (const l of lines) {
    if (l.startsWith('<pre') || l.endsWith('</pre>')) {
      if (ul) { out.push('</ul>'); ul = false }
      out.push(l)
      continue
    }
    const m = l.match(/^\s*[-*]\s+(.*)/)
    if (m) {
      if (!ul) { out.push('<ul>'); ul = true }
      out.push(`<li>${m[1]}</li>`)
    } else {
      if (ul) { out.push('</ul>'); ul = false }
      if (l.trim()) out.push(`<p>${l}</p>`)
    }
  }
  if (ul) out.push('</ul>')
  return out.join('')
}

export default function ChatModal({
  lib,
  cur,
  messages,
  setMessages,
  scope,
  setScope,
  sessionId,
  onClose,
  onResetSession
}: Props) {
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [timeFrom, setTimeFrom] = useState('')
  const [timeTo, setTimeTo] = useState('')
  const [showScrollBtn, setShowScrollBtn] = useState(false)

  const msgsEndRef = useRef<HTMLDivElement>(null)
  const msgsContainerRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const scrollToBottom = (smooth = true) => {
    msgsEndRef.current?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto' })
  }

  useEffect(() => {
    scrollToBottom(true)
  }, [messages.length])

  const handleScroll = () => {
    if (!msgsContainerRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = msgsContainerRef.current
    const atBottom = scrollHeight - scrollTop - clientHeight < 80
    setShowScrollBtn(!atBottom)
  }

  const toggleScope = (vid: string) => {
    setScope(prev => {
      const next = new Set(prev)
      if (next.has(vid)) next.delete(vid)
      else next.add(vid)
      return next
    })
  }

  const handleAsk = async (textToSend?: string) => {
    const q = (textToSend || input).trim()
    if (!q || busy) return

    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'

    const userMsgId = crypto.randomUUID()
    const assistantMsgId = crypto.randomUUID()

    setMessages(prev => [
      ...prev,
      { id: userMsgId, role: 'user', content: q },
      { id: assistantMsgId, role: 'assistant', content: '', thinking: true, steps: [] }
    ])

    setBusy(true)
    let fullText = ''
    const stepsAccumulator: { id: string; label: string; detail?: string }[] = []

    try {
      const payload = {
        session_id: sessionId,
        question: q,
        video_ids: Array.from(scope),
        t_from: parseTs(timeFrom),
        t_to: parseTs(timeTo)
      }

      for await (const ev of streamSSE('/api/chat', payload)) {
        if (ev.type === 'step') {
          const existingIdx = stepsAccumulator.findIndex(s => s.id === ev.id)
          const newStep = { id: ev.id, label: ev.label, detail: ev.detail }
          if (existingIdx >= 0) stepsAccumulator[existingIdx] = newStep
          else stepsAccumulator.push(newStep)

          setMessages(prev =>
            prev.map(m => m.id === assistantMsgId ? { ...m, steps: [...stepsAccumulator] } : m)
          )
        } else if (ev.type === 'token') {
          fullText += ev.text
          setMessages(prev =>
            prev.map(m => m.id === assistantMsgId ? { ...m, content: fullText, thinking: false } : m)
          )
        } else if (ev.type === 'done') {
          setMessages(prev =>
            prev.map(m => m.id === assistantMsgId ? {
              ...m,
              content: ev.answer || fullText,
              sources: ev.sources || [],
              thinking: false
            } : m)
          )
        } else if (ev.type === 'error') {
          throw new Error(ev.message)
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Error retrieving response'
      setMessages(prev =>
        prev.map(m => m.id === assistantMsgId ? {
          ...m,
          content: `⚠️ ${msg}`,
          thinking: false
        } : m)
      )
    } finally {
      setBusy(false)
      setTimeout(() => textareaRef.current?.focus(), 50)
    }
  }

  const suggestions = cur?.sections?.slice(0, 3).map(s => `Where is "${s.heading}" discussed?`) || [
    'Summarize the core takeaways',
    'Explain key architectural concepts mentioned'
  ]

  return (
    <div className="chat-panel">
      <div className="chat-backdrop" onClick={onClose} />
      <div className="chat-inner">
        {/* Header */}
        <div className="chat-hdr">
          <div>
            <div className="chat-hdr-title">Q&amp;A Chat</div>
            <div className="chat-hdr-sub">Multi-turn session with video citations</div>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
            <button className="btn icon" onClick={onResetSession} title="New conversation">
              ↺
            </button>
            <button className="btn icon" onClick={onClose} title="Close chat">
              ✕
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="chat-msgs" ref={msgsContainerRef} onScroll={handleScroll}>
          {messages.length === 0 ? (
            <div className="chat-empty">
              <div className="chat-empty-icon">💬</div>
              <div className="chat-empty-title">Ask anything</div>
              <div className="chat-empty-sub">
                Ask multiple continuous questions. Answers will pinpoint exact timestamps and video segments.
              </div>
            </div>
          ) : (
            messages.map(m => (
              <div key={m.id} className={`msg ${m.role}`}>
                {m.role === 'user' ? (
                  m.content
                ) : (
                  <div className="msg-ans">
                    {/* Reasoning steps if available */}
                    {m.steps && m.steps.length > 0 && (
                      <details className="msg-trace" open={m.thinking}>
                        <summary>▸ Reasoning steps ({m.steps.length})</summary>
                        <div className="msg-trace-body">
                          {m.steps.map(st => (
                            <div key={st.id} className="msg-trace-step">
                              <b>{st.label}</b> {st.detail ? `— ${st.detail}` : ''}
                            </div>
                          ))}
                        </div>
                      </details>
                    )}

                    {/* Thinking animation or markdown content */}
                    {m.thinking && !m.content ? (
                      <div className="thinking">
                        <span></span><span></span><span></span>
                      </div>
                    ) : (
                      <div dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }} />
                    )}

                    {/* Sources / Video timestamps */}
                    {m.sources && m.sources.length > 0 && (
                      <div className="msg-sources">
                        {m.sources.map(src => (
                          <a
                            key={src.n}
                            href={src.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="msg-source"
                            title={`${src.title} — ${src.chunk_title}`}
                          >
                            <b>[{src.n}]</b> {src.title.slice(0, 24)} · {src.label}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
          <div ref={msgsEndRef} />
        </div>

        {/* Scroll down button */}
        <button
          className={`scroll-btn ${showScrollBtn ? 'show' : ''}`}
          onClick={() => scrollToBottom(true)}
        >
          ↓
        </button>

        {/* Composer */}
        <div className="chat-composer">
          {/* Search scope selector */}
          <div className="chat-scope">
            <span>Scope:</span>
            {lib.map(v => (
              <button
                key={v.video_id}
                className={`scope-chip ${scope.has(v.video_id) ? 'on' : ''}`}
                onClick={() => toggleScope(v.video_id)}
                title={v.title}
              >
                {v.title}
              </button>
            ))}
            <div className="time-filter">
              <span>Time:</span>
              <input
                className="time-input"
                placeholder="from"
                value={timeFrom}
                onChange={e => setTimeFrom(e.target.value)}
              />
              <span>-</span>
              <input
                className="time-input"
                placeholder="to"
                value={timeTo}
                onChange={e => setTimeTo(e.target.value)}
              />
            </div>
          </div>

          {/* Quick suggestions if messages are empty or at start */}
          {messages.length === 0 && (
            <div className="suggestions">
              {suggestions.map((s, idx) => (
                <button
                  key={idx}
                  className="suggestion"
                  onClick={() => handleAsk(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          <div className="composer-input">
            <textarea
              ref={textareaRef}
              className="composer-textarea"
              rows={1}
              value={input}
              placeholder="Ask a question or follow-up..."
              disabled={busy}
              onChange={e => {
                setInput(e.target.value)
                e.target.style.height = 'auto'
                e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`
              }}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleAsk()
                }
              }}
            />
            <button
              className="btn primary sm"
              disabled={busy || !input.trim()}
              onClick={() => handleAsk()}
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
