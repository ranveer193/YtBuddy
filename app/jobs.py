import json
import queue
import threading
from typing import Dict, List, Optional
from .graphs.ingest import ingest_graph
from . import youtube as yt


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
        self.events: List[dict] = []
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
            self.events.append(ev)
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
            cancel_ev = {"type": "error", "message": "Cancelled by user"}
            self.events.append(cancel_ev)
            for q in list(self._listeners):
                try:
                    q.put(cancel_ev)
                    q.put(None)
                except Exception:
                    pass
            self._listeners.clear()

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

    def get_job(self, video_id: str) -> Optional[IngestJob]:
        with self._lock:
            return self.jobs.get(video_id)

    def list_jobs(self) -> List[dict]:
        with self._lock:
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
