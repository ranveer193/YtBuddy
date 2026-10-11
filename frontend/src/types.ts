export interface VideoInfo {
  video_id: string
  title: string
  channel: string
  duration: number
  thumbnail: string
  views?: number
  url: string
  chapters?: { start_time: number; title: string }[]
}

export interface LibraryVideo extends VideoInfo {
  language: string
  chunks: number
  created: string
  sections: { heading: string; start: number }[]
}

export interface IngestJob {
  video_id: string
  language: string
  title: string
  channel: string
  duration: number
  thumbnail: string
  status: 'running' | 'done' | 'error' | 'cancelled'
  current_step: string
  pct: number | null
  error: string | null
  events?: any[]
}

export interface LangOption {
  code: string
  name: string
  generated: boolean
  translatable: boolean
}

export interface StepEvent {
  type: 'step'
  id: string
  label: string
  status: 'running' | 'done' | 'pending'
  detail?: string
}

export interface ProgressEvent {
  type: 'progress'
  id: string
  done: number
  total: number
}

export interface DoneEvent {
  type: 'done'
  video_id?: string
  answer?: string
  sources?: Source[]
}

export interface TokenEvent {
  type: 'token'
  text: string
}

export interface ErrorEvent {
  type: 'error'
  message: string
}

export type SSEEvent = StepEvent | ProgressEvent | DoneEvent | TokenEvent | ErrorEvent

export interface Source {
  n: number
  video_id: string
  title: string
  chunk_title: string
  start: number
  end: number
  label: string
  url: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  steps?: { id: string; label: string; detail?: string }[]
  thinking?: boolean
}
