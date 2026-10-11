"""Chat graph:  analyze -> multi_query -> retrieve -> compress -> answer -> memory
Conversation state lives in a LangGraph checkpointer keyed by thread_id (= session id)."""
from typing import TypedDict
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
try:
    from langchain_classic.retrievers.document_compressors import EmbeddingsFilter, LLMChainExtractor
except ImportError:  # older LangChain
    from langchain_classic.retrievers.document_compressors import EmbeddingsFilter, LLMChainExtractor
from .. import vectorstore as V
from ..config import settings as cfg
from ..events import fmt_ts, step, tok
from ..llm import get_llm, structured
from ..schemas import QueryAnalysis, QueryVariants


class S(TypedDict, total=False):
    question: str; video_ids: list; t_from: float | None; t_to: float | None
    standalone: str; intent: str; queries: list; docs: list; sources: list
    answer: str; history: list; summary: str


def _hist_text(s):
    h = "\n".join(f'{m["role"]}: {m["content"]}' for m in s.get("history", []))
    return (f"Summary of earlier conversation: {s['summary']}\n" if s.get("summary") else "") + h


def n_analyze(s: S):
    w = get_stream_writer(); step(w, "analyze", "Understanding the question", "running")
    p = ChatPromptTemplate.from_messages([("system", "Rewrite the user's latest question to be fully self-contained using the "
        "conversation context, and classify intent (qa / locate / overview)."), ("human", "{h}\n\nLatest question: {q}")])
    r = (p | structured(QueryAnalysis, 0)).invoke({"h": _hist_text(s) or "(no history)", "q": s["question"]})
    step(w, "analyze", "Understanding the question", "done", f'{r.intent} → "{r.standalone_question}"')
    return {"standalone": r.standalone_question, "intent": r.intent}


def n_multi_query(s: S):
    w = get_stream_writer(); step(w, "multi_query", "Expanding into multiple queries", "running")
    p = ChatPromptTemplate.from_messages([("system", f"Generate {cfg.n_queries} differently-worded search queries that would "
        "retrieve passages from video notes relevant to the question. Vary vocabulary and angle."), ("human", "{q}")])
    v = (p | structured(QueryVariants, 0.4)).invoke({"q": s["standalone"]}).queries[: cfg.n_queries]
    qs = [s["standalone"]] + [x for x in v if x.strip()]
    step(w, "multi_query", "Expanding into multiple queries", "done", " | ".join(qs))
    return {"queries": qs}


def n_retrieve(s: S):
    w = get_stream_writer(); step(w, "retrieve", "Searching vector database", "running")
    flt = V.build_filter(s.get("video_ids"), s.get("t_from"), s.get("t_to"))
    k = cfg.per_query_k * (2 if s["intent"] == "locate" else 1)
    seen = {}
    for q in s["queries"]:
        for d in V.get_store().similarity_search(q, k=k, filter=flt):
            seen.setdefault(f'{d.metadata["video_id"]}:{d.metadata["chunk_index"]}', d)
    scope = f'{len(s["video_ids"])} video(s)' if s.get("video_ids") else "all videos"
    step(w, "retrieve", "Searching vector database", "done", f"{len(seen)} unique chunks · scope: {scope}"
         + (f" · filter {flt}" if flt else ""))
    return {"docs": [{"text": d.page_content, "meta": d.metadata} for d in seen.values()]}


def n_compress(s: S):
    """Contextual compression: embedding relevance filter, then LLM extraction of the relevant sentences."""
    w = get_stream_writer(); step(w, "compress", "Contextual compression", "running")
    docs = [Document(page_content=d["text"], metadata=d["meta"]) for d in s["docs"]]
    if not docs:
        step(w, "compress", "Contextual compression", "done", "nothing retrieved"); return {"docs": []}
    locate = s["intent"] == "locate"
    use_llm = cfg.llm_compression and not locate
    top = EmbeddingsFilter(embeddings=V.get_embeddings(), k=(8 if locate else 4 if use_llm else 6)) \
        .compress_documents(docs, s["standalone"])
    out = top
    if use_llm and top:
        try:
            ext = LLMChainExtractor.from_llm(get_llm(0)).compress_documents(top, s["standalone"])
            out = ext or top
        except Exception:
            out = top
    out = sorted(out, key=lambda d: (d.metadata["video_id"], d.metadata["start_sec"]))
    step(w, "compress", "Contextual compression", "done", f"{len(docs)} → {len(out)} passages")
    return {"docs": [{"text": d.page_content, "meta": d.metadata} for d in out]}


ANSWER_SYS = """You answer questions about YouTube videos using ONLY the numbered context passages from their notes.
- Cite passages inline as [S1], [S2].
- If the user asks where/when something is discussed, list each location as "mm:ss–mm:ss — video title — what is covered".
- If the context does not contain the answer, say so plainly; never invent.
- Be concise and well structured (short paragraphs or bullets)."""


def n_answer(s: S):
    w = get_stream_writer(); step(w, "answer", "Writing answer", "running")
    srcs, ctx = [], []
    for i, d in enumerate(s["docs"], 1):
        m = d["meta"]
        srcs.append({"n": i, "video_id": m["video_id"], "title": m["video_title"], "chunk_title": m["chunk_title"],
                     "start": m["start_sec"], "end": m["end_sec"], "label": f'{m["start_label"]}–{m["end_label"]}',
                     "url": f'https://youtu.be/{m["video_id"]}?t={int(m["start_sec"])}'})
        ctx.append(f'[S{i}] "{m["video_title"]}" @ {m["start_label"]}–{m["end_label"]} ({m["chunk_title"]})\n{d["text"]}')
    p = ChatPromptTemplate.from_messages([("system", ANSWER_SYS), ("human",
        "{h}\n\nContext:\n{ctx}\n\nQuestion: {q}")])
    text = ""
    for part in (p | get_llm(0.2)).stream({"h": _hist_text(s), "ctx": "\n\n".join(ctx) or "(no passages found)",
                                           "q": s["standalone"]}):
        if part.content: text += part.content; w({"type": "token", "text": part.content})
    step(w, "answer", "Writing answer", "done")
    w({"type": "done", "answer": text, "sources": srcs})
    return {"answer": text, "sources": srcs}


def n_memory(s: S):
    hist = s.get("history", []) + [{"role": "user", "content": s["question"]},
                                   {"role": "assistant", "content": s["answer"]}]
    summary, w = s.get("summary", ""), get_stream_writer()
    if tok(summary) + sum(tok(m["content"]) for m in hist) > cfg.memory_limit and len(hist) > cfg.memory_keep:
        step(w, "memory", "Compacting conversation memory", "running")
        old, hist = hist[: -cfg.memory_keep], hist[-cfg.memory_keep:]
        p = ChatPromptTemplate.from_messages([("human", "Condense this conversation into a short factual summary "
            "(topics asked, conclusions, which videos/timestamps were discussed).\n\nPrevious summary: {s}\n\n{h}")])
        summary = (p | get_llm(0)).invoke({"s": summary, "h": "\n".join(f'{m["role"]}: {m["content"]}' for m in old)}).content
        step(w, "memory", "Compacting conversation memory", "done", f"summarised {len(old)} messages, kept last {len(hist)}")
    return {"history": hist, "summary": summary}


def build():
    g = StateGraph(S)
    for n, f in [("analyze", n_analyze), ("multi_query", n_multi_query), ("retrieve", n_retrieve),
                 ("compress", n_compress), ("answer", n_answer), ("memory", n_memory)]:
        g.add_node(n, f)
    g.add_edge(START, "analyze")
    for a, b in [("analyze", "multi_query"), ("multi_query", "retrieve"), ("retrieve", "compress"),
                 ("compress", "answer"), ("answer", "memory")]:
        g.add_edge(a, b)
    g.add_edge("memory", END)
    return g.compile(checkpointer=MemorySaver())


chat_graph = build()
