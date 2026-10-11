from datetime import datetime, timezone
from functools import lru_cache
from langchain_core.documents import Document
from .config import settings as cfg
from .events import fmt_ts


@lru_cache
def get_embeddings():
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name=cfg.embed_model, encode_kwargs={"normalize_embeddings": True})


@lru_cache
def get_store():
    from langchain_chroma import Chroma
    return Chroma(collection_name="video_notes", embedding_function=get_embeddings(),
                  persist_directory=cfg.chroma_dir, collection_metadata={"hnsw:space": "cosine"})


def _page(summary: dict, excerpt: str) -> str:
    j = lambda k: "; ".join(summary.get(k) or [])
    return (f"{summary['title']}\n{summary['overview']}\nConcepts: {j('key_concepts')}\n"
            f"Points: {j('important_points')}\nExamples: {j('examples')}\nTerms: {j('technical_terms')}\n"
            f"Transcript excerpt: {excerpt}")


def index_video(meta: dict, language: str, chunks: list[dict], summaries: list[dict]) -> int:
    """Only summarised chunks are indexed. Re-ingesting a video replaces its old chunks."""
    store = get_store(); delete_video(meta["video_id"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    docs, ids = [], []
    for c, s in zip(chunks, summaries):
        docs.append(Document(page_content=_page(s, c["text"][:1200]), metadata={
            "video_id": meta["video_id"], "video_title": meta["title"] or "", "channel": meta.get("channel") or "",
            "video_duration": float(meta.get("duration") or 0), "language": language,
            "chunk_index": c["index"], "chunk_title": s["title"], "start_sec": float(c["start"]),
            "end_sec": float(c["end"]), "start_label": fmt_ts(c["start"]), "end_label": fmt_ts(c["end"]),
            "keywords": ", ".join(s.get("keywords") or []), "url": meta.get("url", ""), "indexed_at": now}))
        ids.append(f"{meta['video_id']}:{c['index']}")
    store.add_documents(docs, ids=ids)
    return len(docs)


def delete_video(video_id: str):
    try: get_store().delete(where={"video_id": video_id})
    except Exception: pass


def build_filter(video_ids=None, t_from=None, t_to=None, language=None):
    """Chroma `where` clause: video scope, time-window overlap, language."""
    c = []
    if video_ids: c.append({"video_id": {"$in": list(video_ids)}} if len(video_ids) > 1 else {"video_id": video_ids[0]})
    if t_from is not None: c.append({"end_sec": {"$gte": float(t_from)}})
    if t_to is not None: c.append({"start_sec": {"$lte": float(t_to)}})
    if language: c.append({"language": language})
    return None if not c else c[0] if len(c) == 1 else {"$and": c}
