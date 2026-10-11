import React, { useState, useEffect, useRef, useCallback } from 'react'
import type { VideoInfo, LibraryVideo } from '../types'
import { searchVideos, fetchVideoInfo, extractVideoId, fmtTs } from '../lib/api'

interface Props {
  active: boolean
  lib: LibraryVideo[]
  onChoose: (v: VideoInfo) => void
}

const RESULT_COUNTS = [6, 12, 18, 24, 30]
const BATCH_SIZE = 6

export default function FindView({ active, lib, onChoose }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<VideoInfo[]>([])
  const [resultCount, setResultCount] = useState<number>(12)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [err, setErr] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [urlPreview, setUrlPreview] = useState<VideoInfo | null>(null)
  const [urlLoading, setUrlLoading] = useState(false)

  const inputRef = useRef<HTMLInputElement>(null)
  const typingTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const observerTarget = useRef<HTMLDivElement>(null)

  const libIds = new Set(lib.map(v => v.video_id))

  // Focus search on mount / view activation
  useEffect(() => {
    if (active) setTimeout(() => inputRef.current?.focus(), 50)
  }, [active])

  // Handle typing: trigger animation only, NO automatic search
  const handleInputChange = useCallback((val: string) => {
    setQuery(val)
    setErr('')

    // Show typing animation
    setIsTyping(true)
    clearTimeout(typingTimerRef.current)
    typingTimerRef.current = setTimeout(() => {
      setIsTyping(false)
    }, 600)

    // Quick URL detection for preview
    const vid = extractVideoId(val)
    if (vid) {
      setUrlLoading(true)
      setUrlPreview(null)
      fetchVideoInfo(vid)
        .then((info: VideoInfo) => setUrlPreview(info))
        .catch(() => setErr('Could not fetch video info'))
        .finally(() => setUrlLoading(false))
      return
    }
    setUrlPreview(null)
  }, [])

  // Explicit search triggered ONLY by button or Enter key
  async function doSearch(count = resultCount) {
    const q = query.trim()
    if (!q) return

    clearTimeout(typingTimerRef.current)
    setIsTyping(false)

    // If it's a URL preview already resolved, directly choose it
    if (urlPreview) {
      onChoose(urlPreview)
      return
    }

    setLoading(true)
    setErr('')
    setHasMore(true)
    try {
      // Fast initial batch
      const initialCount = Math.min(count, 12)
      const res = await searchVideos(q, initialCount, 0)
      setResults(res)
      setHasMore(res.length >= initialCount)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Search failed')
    } finally {
      setLoading(false)
    }
  }

  // Lazy-load more videos when user clicks or reaches the bottom
  async function loadMore() {
    const q = query.trim()
    if (!q || loading || loadingMore || !hasMore) return

    setLoadingMore(true)
    try {
      const more = await searchVideos(q, BATCH_SIZE, results.length)
      if (more.length === 0) {
        setHasMore(false)
      } else {
        // Deduplicate against existing results
        const existingIds = new Set(results.map((r: VideoInfo) => r.video_id))
        const filtered = more.filter((m: VideoInfo) => !existingIds.has(m.video_id))
        if (filtered.length === 0) {
          setHasMore(false)
        } else {
          setResults(prev => [...prev, ...filtered])
        }
      }
    } catch (e) {
      console.error('Lazy loading failed', e)
    } finally {
      setLoadingMore(false)
    }
  }

  // IntersectionObserver for auto lazy loading when scrolling
  useEffect(() => {
    if (!active || results.length === 0 || !hasMore) return
    const el = observerTarget.current
    if (!el) return

    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && !loading && !loadingMore) {
        loadMore()
      }
    }, { rootMargin: '200px' })

    observer.observe(el)
    return () => observer.disconnect()
  }, [active, results.length, hasMore, loading, loadingMore])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') {
      doSearch()
    }
  }

  function handleCountChange(n: number) {
    setResultCount(n)
    if (query.trim() && results.length > 0) {
      doSearch(n)
    }
  }

  const displayResults = results.length > 0 ? results : lib

  return (
    <section className={`view find-view ${active ? 'active' : ''}`}>
      <div className="hero">
        <h1 className="hero-title">Turn any video into <span>notes you can talk to</span></h1>
        <p className="hero-sub">Paste a YouTube link or search by topic. Fast initial results with lazy loading as you scroll.</p>

        {/* Search bar with typing animation */}
        <div className={`search-bar ${isTyping ? 'typing' : ''}`}>
          <input
            ref={inputRef}
            value={query}
            onChange={e => handleInputChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder='Paste a YouTube link or search topic (e.g. "Spring Boot architecture")'
          />

          {/* Typing wave indicator */}
          {isTyping && (
            <div className="typing-dots" title="Typing…">
              <span></span><span></span><span></span>
            </div>
          )}

          <button
            className="btn primary"
            disabled={loading || !query.trim()}
            onClick={() => doSearch()}
          >
            {loading ? 'Searching…' : 'Search'}
          </button>
        </div>

        {/* User-controlled result count pills */}
        <div className="search-controls">
          <span>Target Results:</span>
          {RESULT_COUNTS.map(n => (
            <button
              key={n}
              className={`search-count-btn ${resultCount === n ? 'active' : ''}`}
              onClick={() => handleCountChange(n)}
            >
              {n}
            </button>
          ))}
        </div>

        <div className="err-text">{err}</div>

        {/* Instant URL preview card if detected */}
        {urlLoading && (
          <div className="url-preview">
            <div className="skeleton" style={{ width: 80, height: 45, borderRadius: 6, flexShrink: 0 }} />
            <div style={{ flex: 1 }}>
              <div className="skeleton" style={{ height: 14, width: '80%', marginBottom: 6, borderRadius: 4 }} />
              <div className="skeleton" style={{ height: 11, width: '50%', borderRadius: 4 }} />
            </div>
          </div>
        )}
        {urlPreview && !urlLoading && (
          <div className="url-preview" onClick={() => onChoose(urlPreview)} title="Click to generate notes">
            <img src={urlPreview.thumbnail} alt="" loading="lazy" />
            <div className="url-preview-text">
              <div className="url-preview-title">{urlPreview.title}</div>
              <div className="url-preview-sub">{urlPreview.channel} · {fmtTs(urlPreview.duration)} · Click to generate notes →</div>
            </div>
          </div>
        )}
      </div>

      {/* Results grid */}
      <div className="results-section">
        {displayResults.length > 0 && (
          <>
            <div className="results-heading">
              {results.length > 0 ? `Search results (${results.length}${hasMore ? '+' : ''})` : 'Your library'}
            </div>
            <div className="results-grid">
              {loading
                ? Array(6).fill(0).map((_, i) => <SkeletonCard key={i} />)
                : displayResults.map((v, i) => (
                    <VideoCard
                      key={v.video_id + '-' + i}
                      video={v}
                      inLibrary={libIds.has(v.video_id)}
                      onClick={() => onChoose(v)}
                    />
                  ))}
            </div>

            {/* Lazy loading indicator and observer anchor */}
            {results.length > 0 && (
              <div style={{ textAlign: 'center', marginTop: 24, marginBottom: 20 }}>
                {loadingMore && (
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: 'var(--mute)', fontSize: 13 }}>
                    <div className="typing-dots">
                      <span></span><span></span><span></span>
                    </div>
                    <span>Lazy-loading more videos…</span>
                  </div>
                )}
                {!loadingMore && hasMore && (
                  <button className="btn sm" onClick={loadMore} style={{ margin: '0 auto' }}>
                    Load more videos ↓
                  </button>
                )}
                <div ref={observerTarget} style={{ height: 1 }} />
              </div>
            )}
          </>
        )}
      </div>
    </section>
  )
}

function VideoCard({ video, inLibrary, onClick }: { video: VideoInfo; inLibrary: boolean; onClick: () => void }) {
  const [imgLoaded, setImgLoaded] = useState(false)

  return (
    <div className={`video-card ${inLibrary ? 'in-library' : ''}`} onClick={onClick}>
      <div className="video-card-thumb" style={{ background: 'var(--card2)' }}>
        <img
          src={video.thumbnail}
          alt={video.title}
          loading="lazy"
          decoding="async"
          onLoad={() => setImgLoaded(true)}
          style={{ opacity: imgLoaded ? 1 : 0.6, transition: 'opacity .25s ease' }}
        />
        <span className="video-card-dur">{fmtTs(video.duration)}</span>
      </div>
      <div className="video-card-body">
        <div className="video-card-title">{video.title}</div>
        <div className="video-card-meta">{video.channel}</div>
      </div>
    </div>
  )
}

function SkeletonCard() {
  return (
    <div className="video-card" style={{ cursor: 'default' }}>
      <div className="skeleton" style={{ aspectRatio: '16/9' }} />
      <div className="video-card-body">
        <div className="skeleton" style={{ height: 14, marginBottom: 8, borderRadius: 4 }} />
        <div className="skeleton" style={{ height: 11, width: '60%', borderRadius: 4 }} />
      </div>
    </div>
  )
}
