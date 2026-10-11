import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from . import vectorstore as V, youtube as yt
from .config import settings as cfg
from .graphs.chat import chat_graph
from .graphs.ingest import ingest_graph

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NoteLens")
ROOT = Path(__file__).resolve().parent.parent

# Enable CORS for Vercel frontend deployment
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.frontend_origins if cfg.frontend_origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "NoteLens"}


def sse(gen):
    def it():
        try:
            for ev in gen: yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:
            yield f'data: {json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"})}\n\n'
    return StreamingResponse(it(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


from .jobs import job_manager


@app.get("/api/search")
def search(q: str, n: int = 12, offset: int = 0):
    try:
        vid = yt.extract_video_id(q)
        return {"results": [yt.video_info(vid)] if vid else yt.search(q, n=n, offset=offset)}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/transcripts/{video_id}")
def transcripts(video_id: str):
    try:
        return {"languages": yt.list_transcripts(video_id)}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Transcript fetch failed for {video_id}: {type(e).__name__}: {e}")
        status_code = 429 if any(k in str(e) for k in ["429", "TooManyRequests", "IpBound", "blocked"]) else 404
        raise HTTPException(status_code, f"No transcripts available: {e}")


class IngestReq(BaseModel):
    video_id: str
    language: str


@app.get("/api/jobs")
def get_jobs():
    return {"jobs": job_manager.list_jobs()}


@app.get("/api/jobs/{video_id}")
def get_job_status(video_id: str):
    job = job_manager.get_job(video_id)
    if not job: raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/api/jobs/{video_id}/stream")
def stream_job(video_id: str):
    job = job_manager.get_job(video_id)
    if not job: raise HTTPException(404, "Job not found")
    q = job.add_listener()

    def it():
        try:
            while True:
                ev = q.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        finally:
            job.remove_listener(q)

    return StreamingResponse(
        it(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.delete("/api/jobs/{video_id}")
def cancel_job(video_id: str):
    ok = job_manager.cancel_job(video_id)
    return {"ok": ok}


@app.post("/api/ingest")
def ingest(r: IngestReq):
    try:
        job = job_manager.start_job(r.video_id, r.language)
    except RuntimeError as e:
        raise HTTPException(429, str(e))
    q = job.add_listener()

    def it():
        try:
            while True:
                ev = q.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        finally:
            job.remove_listener(q)

    return StreamingResponse(
        it(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


class ChatReq(BaseModel):
    session_id: str
    question: str
    video_ids: list[str] = []
    t_from: float | None = None
    t_to: float | None = None


@app.post("/api/chat")
def chat(r: ChatReq):
    inp = {"question": r.question, "video_ids": r.video_ids, "t_from": r.t_from, "t_to": r.t_to}
    return sse(chat_graph.stream(inp, {"configurable": {"thread_id": r.session_id}}, stream_mode="custom"))


@app.delete("/api/chat/{session_id}")
def reset_chat(session_id: str):
    chat_graph.checkpointer.delete_thread(session_id); return {"ok": True}


@app.get("/api/library")
def library():
    items = []
    for f in sorted(cfg.notes_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        d = json.loads(f.read_text(encoding="utf-8"))
        items.append({**d["meta"], "language": d["language"], "chunks": d["stats"]["chunks"],
                      "created": d["stats"]["created"], "sections": [{"heading": s["heading"], "start": s["start_sec"]}
                                                                    for s in d["notes"]["sections"]]})
    return {"videos": items}


@app.get("/api/notes/{video_id}/html")
def notes_html(video_id: str):
    p = cfg.notes_dir / f"{Path(video_id).name}.html"
    if not p.exists(): raise HTTPException(404)
    return FileResponse(p, media_type="text/html")


@app.delete("/api/library/{video_id}")
def delete(video_id: str):
    V.delete_video(video_id)
    for ext in ("json", "html"): (cfg.notes_dir / f"{Path(video_id).name}.{ext}").unlink(missing_ok=True)
    return {"ok": True}


from fastapi.staticfiles import StaticFiles

# Mount built React assets if dist folder exists
DIST_DIR = ROOT / "frontend" / "dist"
if (DIST_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.get("/")
def index():
    if (DIST_DIR / "index.html").exists():
        return FileResponse(DIST_DIR / "index.html")
    return FileResponse(ROOT / "frontend" / "index.html")
