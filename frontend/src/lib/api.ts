import type { VideoInfo, LibraryVideo, LangOption, SSEEvent } from '../types'
import { cGet, cSet, TTL } from './cache'

const YT_ID = /(?:v=|youtu\.be\/|shorts\/|embed\/|live\/)([\w-]{11})/

/** Base API URL (configured via VITE_API_BASE_URL for Vercel deployment) */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

/** Construct full API URL */
export function apiUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  const cleanPath = path.startsWith('/') ? path : '/' + path
  return API_BASE ? `${API_BASE}${cleanPath}` : cleanPath
}

/** Extract YouTube video ID from a URL or bare ID */
export function extractVideoId(text: string): string | null {
  const m = YT_ID.exec(text.trim())
  if (m) return m[1]
  if (/^[\w-]{11}$/.test(text.trim()) && /[\dA-Z_-]/.test(text)) return text.trim()
  return null
}

/** Format seconds → mm:ss or h:mm:ss */
export function fmtTs(sec: number): string {
  sec = Math.round(sec || 0)
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`
}

/** Search YouTube for videos */
export async function searchVideos(query: string, n: number = 12, offset: number = 0): Promise<VideoInfo[]> {
  const key = `search:${query}:${n}:${offset}`
  const cached = cGet<VideoInfo[]>(key)
  if (cached) return cached
  const r = await fetch(apiUrl(`/api/search?q=${encodeURIComponent(query)}&n=${n}&offset=${offset}`))
  if (!r.ok) throw new Error((await r.json()).detail ?? 'Search failed')
  const data = await r.json()
  const results: VideoInfo[] = data.results || []
  cSet(key, results, TTL.SEARCH)
  return results
}

/** Fetch background ingest jobs */
export async function fetchJobs(): Promise<import('../types').IngestJob[]> {
  const r = await fetch(apiUrl('/api/jobs'))
  if (!r.ok) return []
  const data = await r.json()
  return (data.jobs || []) as import('../types').IngestJob[]
}

/** Fetch video info (cached) */
export async function fetchVideoInfo(videoId: string): Promise<VideoInfo> {
  const key = `vinfo:${videoId}`
  const cached = cGet<VideoInfo>(key)
  if (cached) return cached
  const r = await fetch(apiUrl(`/api/search?q=${encodeURIComponent('https://youtu.be/' + videoId)}`))
  if (!r.ok) throw new Error('Could not fetch video info')
  const data = await r.json()
  const info: VideoInfo = data.results[0]
  if (info) cSet(key, info, TTL.VIDEO_INFO)
  return info
}

/** List available transcript languages for a video (cached) */
export async function fetchLanguages(videoId: string): Promise<LangOption[]> {
  const key = `langs:${videoId}`
  const cached = cGet<LangOption[]>(key)
  if (cached) return cached
  const r = await fetch(apiUrl(`/api/transcripts/${videoId}`))
  if (!r.ok) throw new Error((await r.json()).detail ?? 'No transcripts found')
  const data = await r.json()
  const langs: LangOption[] = data.languages
  cSet(key, langs, TTL.LANGS)
  return langs
}

/** Fetch library */
export async function fetchLibrary(): Promise<LibraryVideo[]> {
  const r = await fetch(apiUrl('/api/library'))
  if (!r.ok) throw new Error('Could not load library')
  const data = await r.json()
  return data.videos as LibraryVideo[]
}

/** Cancel an in-progress background job */
export async function cancelJob(videoId: string): Promise<void> {
  await fetch(apiUrl(`/api/jobs/${videoId}`), { method: 'DELETE' })
}

/** Fetch detailed status of a specific job (including events) */
export async function fetchJob(videoId: string): Promise<import('../types').IngestJob | null> {
  const r = await fetch(apiUrl(`/api/jobs/${videoId}`))
  if (!r.ok) return null
  return await r.json()
}

/** Delete a video from library */
export async function deleteVideo(videoId: string): Promise<void> {
  await fetch(apiUrl(`/api/library/${videoId}`), { method: 'DELETE' })
}

/** SSE streaming helper */
export async function* streamSSE(url: string, body?: unknown): AsyncGenerator<SSEEvent> {
  const targetUrl = apiUrl(url)
  const options: RequestInit = body !== undefined
    ? {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }
    : { method: 'GET' }

  const r = await fetch(targetUrl, options)
  if (!r.ok) throw new Error(await r.text())
  const reader = r.body!.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const line = buf.slice(0, i)
      buf = buf.slice(i + 2)
      if (line.startsWith('data: ')) {
        yield JSON.parse(line.slice(6)) as SSEEvent
      }
    }
  }
}
