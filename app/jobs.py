import queue
import threading
import time
from typing import Dict, List, Optional
from .graphs.ingest import ingest_graph
from . import youtube as yt

# Completed/errored jobs are evicted after this many seconds (5 minutes)
_JOB_TTL = 300


class IngestJob:
    def __init__(self, video_id: str, language: str):
        self.video_id = video_id
        self.language = language
        self.status = "running"  # "running" | "done" | "error" | "cancelled"
        self.cancelled = False
        self.error: Optional[str] = None
        self.title = ""
        self.channel = ""
        self.duration = 0
        self.thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        self.current_step = "Starting ingest pipeline…"
        self.pct: Optional[int] = None
        # Only keep last 200 events — prevents unbounded growth per job
        self.events: List[dict] = []
        self._finished_at: Optional[float] = None
        self._listeners: List[queue.Queue] = []
        self._lock = threading.Lock()

    def add_listener(self) -> queue.Queue:
        q = queue.Queue()
        with self._lock:
            for ev in self.events:
                q.put(ev)
            if self.status in ("done", "error", "cancelled"):
                q.put(None)
            else:
                self._listeners.append(q)
        return q

    def remove_listener(self, q: queue.Queue):
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def push_event(self, ev: dict):
        with self._lock:
            if self.cancelled:
                return
            # Cap event list to last 200 to avoid RAM growth on long jobs
            self.events.append(ev)
            if len(self.events) > 200:
                self.events = self.events[-200:]

            if ev.get("type") == "step":
                label = ev.get("label", "")
                detail = ev.get("detail", "")
                self.current_step = f"{label}{(': ' + detail) if detail else ''}"
                if ev.get("id") == "meta" and ev.get("status") == "done" and detail:
                    if not self.title:
                        self.title = detail.split(" · ")[0]
            elif ev.get("type") == "progress":
                total = ev.get("total", 1)
                done = ev.get("done", 0)
                self.pct = int(done / max(total, 1) * 100)
            elif ev.get("type") == "done":
                self.status = "done"
                self.pct = 100
                self.current_step = "Notes generated successfully"

            for q in list(self._listeners):
                try:
                    q.put(ev)
                except Exception:
                    pass

    def finish_with_error(self, err_msg: str):
        with self._lock:
            self.status = "error" if not self.cancelled else "cancelled"
            self.error = err_msg
            self.current_step = f"Failed: {err_msg}"
            self._finished_at = time.time()
            err_ev = {"type": "error", "message": err_msg}
            self.events.append(err_ev)
            for q in list(self._listeners):
                try:
                    q.put(err_ev)
                    q.put(None)
                except Exception:
                    pass
            self._listeners.clear()

    def finish_done(self):
        with self._lock:
            if not self.cancelled:
                self.status = "done"
                self.pct = 100
            self._finished_at = time.time()
            for q in list(self._listeners):
                try:
                    q.put(None)
                except Exception:
                    pass
            self._listeners.clear()

    def cancel(self):
        with self._lock:
            self.cancelled = True
            self.status = "cancelled"
            self.current_step = "Cancelled"
            self._finished_at = time.time()
            cancel_ev = {"type": "error", "message": "Cancelled by user"}
            self.events.append(cancel_ev)
            for q in list(self._listeners):
                try:
                    q.put(cancel_ev)
                    q.put(None)
                except Exception:
                    pass
            self._listeners.clear()

    def is_expired(self) -> bool:
        """Completed/failed jobs older than _JOB_TTL seconds can be evicted."""
        return (
            self.status in ("done", "error", "cancelled")
            and self._finished_at is not None
            and (time.time() - self._finished_at) > _JOB_TTL
        )

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "video_id": self.video_id,
                "language": self.language,
                "title": self.title,
                "channel": self.channel,
                "duration": self.duration,
                "thumbnail": self.thumbnail,
                "status": self.status,
                "current_step": self.current_step,
                "pct": self.pct,
                "error": self.error,
                "events": list(self.events),
            }


class JobManager:
    def __init__(self):
        self.jobs: Dict[str, IngestJob] = {}
        self._lock = threading.Lock()

    def _evict_expired(self):
        """Remove completed jobs older than _JOB_TTL. Must be called under self._lock."""
        expired = [vid for vid, j in self.jobs.items() if j.is_expired()]
        for vid in expired:
            del self.jobs[vid]

    def get_job(self, video_id: str) -> Optional[IngestJob]:
        with self._lock:
            return self.jobs.get(video_id)

    def list_jobs(self) -> List[dict]:
        with self._lock:
            self._evict_expired()
            return [j.to_dict() for j in self.jobs.values()]

    def list_active_jobs(self) -> List[dict]:
        with self._lock:
            return [j.to_dict() for j in self.jobs.values() if j.status == "running"]

    def cancel_job(self, video_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(video_id)
            if not job:
                return False
            job.cancel()
            self.jobs.pop(video_id, None)
            return True

    def start_job(self, video_id: str, language: str) -> IngestJob:
        with self._lock:
            self._evict_expired()

            existing = self.jobs.get(video_id)
            if existing and existing.status == "running":
                return existing

            running_jobs = [j for j in self.jobs.values() if j.status == "running"]
            if len(running_jobs) >= 2:
                raise RuntimeError("Maximum 2 parallel video processing jobs allowed. Please wait for an active job to finish.")

            job = IngestJob(video_id, language)
            self.jobs[video_id] = job

        def _worker():
            try:
                info = yt.video_info(video_id)
                job.title = info.get("title") or video_id
                job.channel = info.get("channel") or ""
                job.duration = info.get("duration") or 0
                job.thumbnail = info.get("thumbnail") or job.thumbnail
            except Exception:
                pass

            try:
                for ev in ingest_graph.stream(
                    {"video_id": video_id, "language": language},
                    stream_mode="custom"
                ):
                    if job.cancelled:
                        break
                    job.push_event(ev)
                if not job.cancelled:
                    job.finish_done()
            except Exception as e:
                if not job.cancelled:
                    job.finish_with_error(str(e))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return job


job_manager = JobManager()
