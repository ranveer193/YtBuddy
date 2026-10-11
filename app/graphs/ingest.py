"""Ingest graph.
  meta -> transcript -> prepare -+- short  -> single -> index -> render
                                 +- longer -> chunk -> summarize (sequential) -> index -> reduce -> render"""
import json
from datetime import datetime, timezone
from typing import TypedDict
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from .. import notes as N, vectorstore as V, youtube as yt
from ..chunking import build_units, chapter_chunks, chunk_params, semantic_chunks
from ..config import settings as cfg
from ..events import fmt_ts, step, tok


class S(TypedDict, total=False):
    video_id: str; language: str; meta: dict; segments: list; units: list
    chunks: list; summaries: list; notes: dict; stats: dict


def n_meta(s: S):
    w = get_stream_writer(); step(w, "meta", "Reading video metadata", "running")
    meta = yt.video_info(s["video_id"])
    step(w, "meta", "Reading video metadata", "done", f'{meta["title"]} · {fmt_ts(meta["duration"])}')
    return {"meta": meta}


def n_transcript(s: S):
    w = get_stream_writer(); step(w, "transcript", "Fetching transcript", "running", s["language"])
    seg = yt.fetch_segments(s["video_id"], s["language"])
    if not seg: raise ValueError("Transcript is empty")
    step(w, "transcript", "Fetching transcript", "done", f"{len(seg):,} caption segments")
    return {"segments": seg}


def n_prepare(s: S):
    w = get_stream_writer(); units = build_units(s["segments"])
    total = sum(tok(u["text"]) for u in units)
    route = "single pass" if total <= cfg.short_tokens else "semantic chunks + sequential notes"
    step(w, "plan", "Choosing strategy", "done", f"~{total:,} tokens → {route}")
    return {"units": units}


def route_len(s: S):
    return "single" if sum(tok(u["text"]) for u in s["units"]) <= cfg.short_tokens else "chunk"


def n_single(s: S):
    w = get_stream_writer(); step(w, "notes", "Writing notes (single pass)", "running")
    notes = N.single_pass_notes(s["units"], s["meta"], "English")
    chunks, sums = N.sections_to_chunks(notes, s["units"])
    step(w, "notes", "Writing notes (single pass)", "done", f'{len(notes["sections"])} sections')
    return {"notes": notes, "chunks": chunks, "summaries": sums}


def n_chunk(s: S):
    w = get_stream_writer(); step(w, "chunk", "Chunking transcript", "running", "Embedding transcript units…")
    units, embed = s["units"], V.get_embeddings().embed_documents
    p = chunk_params(s["meta"].get("duration") or s["units"][-1]["end"])
    chs = s["meta"].get("chapters") or []
    if len(chs) >= 3: chunks, src = chapter_chunks(units, chs, embed, p), f"{len(chs)} creator chapters"
    else: chunks, src = semantic_chunks(units, embed, p), "semantic boundaries"
    step(w, "chunk", "Chunking transcript", "done",
         f"{len(chunks)} chunks from {src} · target {p['min']}–{p['max']} tokens")
    return {"chunks": chunks}


def n_summarize(s: S):
    w = get_stream_writer(); total = len(s["chunks"])
    step(w, "summarize", "Summarising chunks in order", "running", f"0 / {total}")
    def prog(d, t, i):
        w({"type": "progress", "id": "summarize", "done": d, "total": t})
        step(w, "summarize", "Summarising chunks in order", "running", f"{d} / {t}")
    sums = N.summarize_chunks(s["chunks"], s["meta"], "English", prog)
    step(w, "summarize", "Summarising chunks in order", "done", f"{total} chunk summaries")
    return {"summaries": sums}


def n_index(s: S):
    w = get_stream_writer(); step(w, "index", "Indexing into vector database", "running")
    n = V.index_video(s["meta"], s["language"], s["chunks"], s["summaries"])
    step(w, "index", "Indexing into vector database", "done", f"{n} chunks with video / time / keyword metadata")
    return {}


def route_after_index(s: S):
    return "render" if s.get("notes") else "reduce"


def n_reduce(s: S):
    w = get_stream_writer(); lbl = "Merging into final notes"
    step(w, "reduce", lbl, "running")
    notes = N.reduce_notes(s["chunks"], s["summaries"], s["meta"], "English", lambda m: step(w, "reduce", lbl, "running", m))
    notes = N.snap_timestamps(notes, s["chunks"])
    step(w, "reduce", lbl, "done", f'{len(notes["sections"])} sections')
    return {"notes": notes}


def n_render(s: S):
    w = get_stream_writer(); step(w, "render", "Rendering notes", "running")
    vid, meta = s["video_id"], s["meta"]
    stats = {"chunks": len(s["chunks"]), "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    (cfg.notes_dir / f"{vid}.html").write_text(N.render_html(s["notes"], meta, s["language"], stats), encoding="utf-8")
    (cfg.notes_dir / f"{vid}.json").write_text(json.dumps({
        "meta": meta, "language": s["language"], "notes": s["notes"], "stats": stats,
        "chunks": [{"index": c["index"], "start": c["start"], "end": c["end"], "title": x["title"]}
                   for c, x in zip(s["chunks"], s["summaries"])]}, ensure_ascii=False, indent=1), encoding="utf-8")
    step(w, "render", "Rendering notes", "done"); w({"type": "done", "video_id": vid})
    return {"stats": stats}


def build():
    g = StateGraph(S)
    for n, f in [("meta", n_meta), ("transcript", n_transcript), ("prepare", n_prepare), ("single", n_single),
                 ("chunk", n_chunk), ("summarize", n_summarize), ("index", n_index), ("reduce", n_reduce), ("render", n_render)]:
        g.add_node(n, f)
    g.add_edge(START, "meta"); g.add_edge("meta", "transcript"); g.add_edge("transcript", "prepare")
    g.add_conditional_edges("prepare", route_len, {"single": "single", "chunk": "chunk"})
    g.add_edge("single", "index"); g.add_edge("chunk", "summarize"); g.add_edge("summarize", "index")
    g.add_conditional_edges("index", route_after_index, {"render": "render", "reduce": "reduce"})
    g.add_edge("reduce", "render"); g.add_edge("render", END)
    return g.compile()


ingest_graph = build()
