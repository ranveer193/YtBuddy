"""Semantic chunking for transcripts of any length.

1. Group caption segments into small *units* (~45 words) - captions have no reliable punctuation.
2. Embed units, compare the sliding window before/after each boundary (cosine distance).
3. Cut at local distance peaks above a percentile threshold (topic shifts).
4. Enforce min/max token size: merge tiny chunks, recursively split oversized ones at their
   strongest internal boundary. Cost is O(n) embeddings, so 5h videos are fine.
"""
import numpy as np
from .cleaner import clean_transcript
from .config import settings as cfg
from .events import tok


def default_params():
    return {"min": cfg.chunk_min_tokens, "max": cfg.chunk_max_tokens, "pct": cfg.breakpoint_percentile}


def chunk_params(duration_sec: float) -> dict:
    """Longer videos get bigger chunks and stricter breakpoints, so a 4h lecture
    doesn't produce dozens of tiny summaries and processing completes quickly."""
    m = (duration_sec or 0) / 60
    for limit, mn, mx, pct in [(30, 350, 1400, 85), (75, 600, 1800, 88), (150, 850, 2400, 90)]:
        if m <= limit: return {"min": mn, "max": mx, "pct": pct}
    return {"min": 1100, "max": 3000, "pct": 92}


def _tokens(us): return sum(tok(u["text"]) for u in us)


def _one(us, index=0):
    return {"index": index, "text": " ".join(u["text"] for u in us), "start": us[0]["start"],
            "end": us[-1]["end"], "tokens": _tokens(us)}


def build_units(segments: list[dict], target_words: int | None = None) -> list[dict]:
    target_words = target_words or cfg.unit_words
    units, buf, words, start, end = [], [], 0, 0.0, 0.0

    def flush():
        text = clean_transcript(" ".join(buf))
        if text: units.append({"text": text, "start": start, "end": end})

    for s in segments:
        if not buf: start = s["start"]
        t = s["text"].replace("\n", " ")
        buf.append(t); words += len(t.split()); end = s["start"] + s.get("duration", 0)
        if words >= target_words:
            flush(); buf, words = [], 0
    if buf: flush()
    return units


def semantic_chunks(units: list[dict], embed, p: dict | None = None) -> list[dict]:
    p = p or default_params()
    n = len(units)
    if n == 0: return []
    toks = np.array([tok(u["text"]) for u in units]); pre = np.concatenate([[0], np.cumsum(toks)])
    rng_tok = lambda lo, hi: int(pre[hi] - pre[lo])
    dist = np.zeros(max(n - 1, 0))

    if n > 2:
        E = np.array(embed([u["text"] for u in units]), dtype="float32")
        E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
        w = cfg.sim_window
        for i in range(n - 1):
            a, b = E[max(0, i - w + 1): i + 1].mean(0), E[i + 1: i + 1 + w].mean(0)
            dist[i] = 1 - float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        thr = np.percentile(dist, p["pct"])
        cuts = [i for i in range(n - 1) if dist[i] >= thr
                and (i == 0 or dist[i] >= dist[i - 1]) and (i == n - 2 or dist[i] >= dist[i + 1])]
    else:
        cuts = []

    bounds = [0] + [c + 1 for c in cuts] + [n]
    ranges = list(zip(bounds[:-1], bounds[1:]))

    # merge tiny chunks forward
    merged, cur = [], None
    for lo, hi in ranges:
        cur = (lo, hi) if cur is None else (cur[0], hi)
        if rng_tok(*cur) >= p["min"]:
            merged.append(cur); cur = None
    if cur:
        merged[-1:] = [(merged[-1][0], cur[1])] if merged else [cur]

    # split oversized chunks at their strongest internal boundary
    def split(lo, hi):
        if rng_tok(lo, hi) <= p["max"] or hi - lo < 2: return [(lo, hi)]
        ok = [k for k in range(lo + 1, hi) if rng_tok(lo, k) >= p["min"] and rng_tok(k, hi) >= p["min"]]
        if ok: k = max(ok, key=lambda k: dist[k - 1])          # strongest topic shift that keeps both sides sizeable
        else: k = min(range(lo + 1, hi), key=lambda k: abs(rng_tok(lo, k) - rng_tok(lo, hi) / 2))
        return split(lo, k) + split(k, hi)

    final = [r for lo, hi in merged for r in split(lo, hi)]
    return [{"index": i, "text": " ".join(u["text"] for u in units[lo:hi]),
             "start": units[lo]["start"], "end": units[hi - 1]["end"], "tokens": rng_tok(lo, hi)}
            for i, (lo, hi) in enumerate(final)]


def chapter_chunks(units, chapters, embed, p) -> list[dict]:
    """Creator chapters are the primary boundaries. Tiny chapters merge forward;
    oversized ones are split semantically. Each chunk keeps its chapter title as a `hint`."""
    chs = sorted(chapters, key=lambda c: c["start_time"])
    groups = []
    for i, c in enumerate(chs):
        hi = chs[i + 1]["start_time"] if i + 1 < len(chs) else float("inf")
        us = [u for u in units if c["start_time"] <= u["start"] < hi]
        if us: groups.append([c["title"], us])
    pre = [u for u in units if u["start"] < chs[0]["start_time"]]
    if pre and groups: groups[0][1] = pre + groups[0][1]
    merged = []
    for t, us in groups:
        if merged and _tokens(merged[-1][1]) < p["min"]:
            merged[-1][0] += " / " + t; merged[-1][1] += us
        else:
            merged.append([t, us])
    if len(merged) > 1 and _tokens(merged[-1][1]) < p["min"]:
        t, us = merged.pop(); merged[-1][0] += " / " + t; merged[-1][1] += us
    out = []
    for t, us in merged:
        for part in (semantic_chunks(us, embed, p) if _tokens(us) > p["max"] else [_one(us)]):
            part["index"] = len(out); part["hint"] = t; out.append(part)
    return out
