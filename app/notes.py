"""Map -> assemble -> outline synthesis -> render.
Handles videos of any length without token overflow:
1. Each chunk summary is mapped with accurate timestamps, code examples, and technical concepts.
2. Sections are assembled directly from the detailed chunk summaries (preserving all code & points).
3. A concise outline is sent to the LLM to synthesize high-level overview & key takeaways in seconds,
   staying well under TPM rate limits (never sends entire chunks/summaries at reduce time)."""
import re, time
from markupsafe import Markup, escape
from jinja2 import Environment, FileSystemLoader, select_autoescape
from langchain_core.prompts import ChatPromptTemplate
from pathlib import Path
from .config import settings as cfg
from .events import fmt_ts, tok
from .llm import structured
from .schemas import ChunkSummary, FinalNotes, NotesSection, VideoOverviewSynthesis

MAP_SYS = """You are an expert at turning technical video transcripts into detailed, accurate study notes.
Always respond by calling the ChunkSummary tool with your findings. Do not output raw markdown text.
- Preserve technical accuracy; never invent facts. Remove filler and narration.
- Dense, compound phrasing. No "the speaker discusses", "in this video".
- You also get a glossary and short notes from EARLIER parts: use them to resolve references ("it", "this", "the same
  approach") and to keep terminology consistent. Do not repeat earlier definitions.
- Write every note so it stands alone: name the topic explicitly instead of using pronouns.
- For code, classes, interfaces, methods, config, or shell commands: reconstruct the FULL snippet faithfully.
  Populate each in the code_examples parameter with caption, language, and complete code with indentation.
- Keywords: 5-8 short topics someone might search for.
Write in {language}."""
MAP_HUMAN = """Video: "{title}"  |  Part {i} of {n} [{start} - {end}]
Creator chapter title (hint only, may be vague or clickbait): {hint}
Glossary so far:
{glossary}

Earlier parts:
{prev}

Transcript of THIS part:
{chunk}"""

SINGLE_SYS = """You are an expert at turning a technical video transcript into detailed, structured study notes.
Always respond by calling the FinalNotes tool with your findings. Do not output raw markdown text.
Follow the video's flow in logical sections. Keep technical accuracy, invent nothing, no filler.
Every section MUST set start_sec from the nearest [Ns] marker where that topic begins.
For code, classes, interfaces, config, or commands mentioned: reconstruct each as a code_example
with caption (short label), language (java/yaml/sql/bash/etc.), and complete code with indentation.
Write in {language}."""

OVERVIEW_SYS = """You are an expert technical writer and curriculum designer.
You receive a chronological outline of a technical video (timestamps, section headings, and brief synopsis).
Always respond by calling the VideoOverviewSynthesis tool. Do not output raw markdown text.
- title: A clear, professional, informative study guide title for the video.
- overview: A comprehensive 2-3 paragraph executive summary explaining the video's core thesis, architecture/framework, and practical significance.
- key_takeaways: 5-8 actionable, high-impact bullet takeaways summarizing the most critical lessons.
Write in {language}."""
OVERVIEW_HUMAN = """Video: "{title}"

Topic Outline:
{outline}

Synthesize the executive summary and key takeaways for the complete study guide."""

_map = ChatPromptTemplate.from_messages([("system", MAP_SYS), ("human", MAP_HUMAN)])
_syn = ChatPromptTemplate.from_messages([("system", OVERVIEW_SYS), ("human", OVERVIEW_HUMAN)])


def _retry(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                raise
            # Backoff for rate limits or network glitches
            wait_time = min(2 ** (i + 1), 15)
            time.sleep(wait_time)


def summarize_chunks(chunks, meta, language, on_progress) -> list[dict]:
    """Sequential on purpose: each part sees a glossary + previous parts' notes, so references resolve."""
    chain = _map | structured(ChunkSummary, 0.2)
    out, glossary = [], []
    for c in chunks:
        prev = "\n".join(f"- {o['title']}: {o['overview']} (concepts: {'; '.join(o['key_concepts'][:6])})"
                         for o in out[-cfg.context_parts:]) or "(this is the first part)"
        inp = {"chunk": c["text"], "title": meta["title"], "language": language, "i": c["index"] + 1,
               "n": len(chunks), "start": fmt_ts(c["start"]), "end": fmt_ts(c["end"]),
               "hint": c.get("hint") or "(none)", "glossary": "\n".join(glossary[-25:]) or "(none yet)", "prev": prev}
        d = _retry(lambda: chain.invoke(inp)).model_dump()
        glossary += d.get("new_definitions", [])
        out.append(d)
        on_progress(len(out), len(chunks), c["index"])
    return out


def single_pass_notes(units, meta, language) -> dict:
    """Short videos: the whole timestamped transcript in one call."""
    p = ChatPromptTemplate.from_messages([("system", SINGLE_SYS), ("human", "Video: {title}\n\nTranscript:\n{material}")])
    material = "\n".join(f"[{int(u['start'])}s] {u['text']}" for u in units)
    return _retry(lambda: (p | structured(FinalNotes, 0.2)).invoke(
        {"title": meta["title"], "material": material, "language": language})).model_dump()


def sections_to_chunks(notes, units):
    """Single-pass route: each note section becomes one index chunk (no extra LLM calls)."""
    secs = sorted(notes["sections"], key=lambda s: s["start_sec"])
    secs[0]["start_sec"] = int(units[0]["start"])
    chunks, sums = [], []
    for i, s in enumerate(secs):
        hi = secs[i + 1]["start_sec"] if i + 1 < len(secs) else float("inf")
        us = [u for u in units if s["start_sec"] <= u["start"] < hi]
        if not us: continue
        text = " ".join(u["text"] for u in us)
        chunks.append({"index": len(chunks), "text": text, "start": us[0]["start"], "end": us[-1]["end"], "tokens": tok(text)})
        sums.append({"title": s.get("heading", ""), "overview": s.get("overview", ""),
                     "key_concepts": s.get("key_concepts", []), "important_points": s.get("points", []),
                     "examples": s.get("examples", []), "technical_terms": s.get("technical_terms", []),
                     "code_examples": s.get("code_examples", []),
                     "keywords": [], "new_definitions": []})
    return chunks, sums


def snap_timestamps(notes, chunks):
    """LLM-written start times are snapped to nearest real chunk start."""
    for s in notes.get("sections", []):
        if chunks:
            s["start_sec"] = int(min(chunks, key=lambda c: abs(c["start"] - s.get("start_sec", 0)))["start"])
    return notes


def _assemble_sections(chunks, summaries) -> list[dict]:
    """Convert chunk summaries directly into structured notes sections,
    merging adjacent chunks that belong to the exact same chapter topic."""
    raw_sections = []
    for c, s in zip(chunks, summaries):
        heading = s.get("title") or c.get("hint") or f"Section at {fmt_ts(c['start'])}"
        raw_sections.append({
            "heading": heading,
            "start_sec": int(c["start"]),
            "overview": s.get("overview", "").strip(),
            "points": list(s.get("important_points") or []),
            "key_concepts": list(s.get("key_concepts") or []),
            "examples": list(s.get("examples") or []),
            "technical_terms": list(s.get("technical_terms") or []),
            "code_examples": list(s.get("code_examples") or []),
            "hint": c.get("hint") or "",
        })

    if not raw_sections:
        return []

    # Merge adjacent sections if they share the exact same hint/heading
    merged = [raw_sections[0]]
    for sec in raw_sections[1:]:
        prev = merged[-1]
        same_hint = prev.get("hint") and prev.get("hint") == sec.get("hint")
        same_heading = prev["heading"].lower().strip() == sec["heading"].lower().strip()
        if same_hint or same_heading:
            # Combine overview if distinct
            if sec["overview"] and sec["overview"] not in prev["overview"]:
                prev["overview"] = f"{prev['overview']}\n\n{sec['overview']}".strip()
            # Combine points without duplicates
            for p in sec["points"]:
                if p not in prev["points"]:
                    prev["points"].append(p)
            for k in sec["key_concepts"]:
                if k not in prev["key_concepts"]:
                    prev["key_concepts"].append(k)
            for e in sec["examples"]:
                if e not in prev["examples"]:
                    prev["examples"].append(e)
            for t in sec["technical_terms"]:
                if t not in prev["technical_terms"]:
                    prev["technical_terms"].append(t)
            prev["code_examples"].extend(sec["code_examples"])
        else:
            merged.append(sec)

    return merged


def reduce_notes(chunks, summaries, meta, language, emit) -> dict:
    """Assembles final notes without token overflow:
    1. Direct section assembly preserves 100% of points, code, and timestamps.
    2. Sends ONLY a lightweight outline (~400 tokens) to the LLM to synthesize
       overall video title, executive summary, and key takeaways."""
    emit("Assembling detailed study sections from chunk summaries…")
    sections = _assemble_sections(chunks, summaries)

    # Build concise outline for video-level synthesis (only ~300-600 tokens total)
    outline_lines = []
    for s in sections:
        short_ov = s["overview"].replace("\n", " ")[:200]
        outline_lines.append(f"- [{fmt_ts(s['start_sec'])}] {s['heading']}: {short_ov}")
    outline_str = "\n".join(outline_lines)

    emit("Synthesizing executive summary & key takeaways…")
    syn_chain = _syn | structured(VideoOverviewSynthesis, 0.2)

    try:
        syn_result = _retry(lambda: syn_chain.invoke({
            "title": meta.get("title", "Video Study Notes"),
            "outline": outline_str,
            "language": language,
        })).model_dump()
    except Exception as e:
        # Graceful fallback: construct overall overview from meta & top sections
        first_ov = sections[0]["overview"] if sections else ""
        syn_result = {
            "title": meta.get("title", "Video Study Notes"),
            "overview": first_ov or f"Comprehensive study notes covering {len(sections)} sections.",
            "key_takeaways": [s["points"][0] for s in sections if s.get("points")][:6],
        }

    title = syn_result.get("title") or meta.get("title", "Study Notes")
    overview = syn_result.get("overview") or (sections[0]["overview"] if sections else "")
    key_takeaways = syn_result.get("key_takeaways") or []

    if not key_takeaways and sections:
        key_takeaways = [s["points"][0] for s in sections if s.get("points")][:6]

    return {
        "title": title,
        "overview": overview,
        "sections": sections,
        "key_takeaways": key_takeaways,
    }


# ---------- HTML ----------
def _inline(text):
    t = str(escape(text))
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return Markup(re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t))


_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape())
_env.filters["inline"] = _inline
_env.filters["ts"] = fmt_ts


def render_html(notes: dict, meta: dict, language: str, stats: dict) -> str:
    return _env.get_template("notes.html.j2").render(notes=notes, meta=meta, language=language, stats=stats)
