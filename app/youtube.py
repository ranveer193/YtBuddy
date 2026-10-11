import re
import time
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

_URL_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})")

# In-memory cache to make repeated searches & info lookups instantaneous
_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_VINFO_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600  # 1 hour


def extract_video_id(text: str):
    text = text.strip()
    m = _URL_ID.search(text)
    if m: return m.group(1)
    if re.fullmatch(r"[\w-]{11}", text) and re.search(r"[\dA-Z_-]", text):
        return text
    return None


def _thumb(vid): return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"


def _ydl(**o):
    return yt_dlp.YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 6,
        **o
    })


def search(query: str, n: int = 12, offset: int = 0) -> list[dict]:
    """Search YouTube with in-memory caching and offset support for lazy-loading."""
    q_norm = query.strip().lower()
    total_needed = offset + n
    cache_key = f"{q_norm}:{max(total_needed, 24)}"
    now = time.time()

    entries = None
    if cache_key in _SEARCH_CACHE:
        ts, cached = _SEARCH_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            entries = cached

    if entries is None:
        fetch_n = max(total_needed, 12)
        with _ydl(extract_flat=True) as y:
            info = y.extract_info(f"ytsearch{fetch_n}:{query}", download=False)
        entries = []
        for e in info.get("entries", []):
            if not e or not e.get("id"): continue
            entries.append({
                "video_id": e["id"],
                "title": e.get("title"),
                "channel": e.get("channel") or e.get("uploader"),
                "duration": e.get("duration"),
                "thumbnail": _thumb(e["id"]),
                "views": e.get("view_count"),
            })
        _SEARCH_CACHE[cache_key] = (now, entries)

    return entries[offset : offset + n]


def video_info(vid: str) -> dict:
    now = time.time()
    if vid in _VINFO_CACHE:
        ts, cached = _VINFO_CACHE[vid]
        if now - ts < _CACHE_TTL:
            return cached

    with _ydl() as y:
        e = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    info = {
        "video_id": vid,
        "title": e.get("title"),
        "channel": e.get("channel") or e.get("uploader"),
        "duration": e.get("duration"),
        "thumbnail": e.get("thumbnail") or _thumb(vid),
        "views": e.get("view_count"),
        "url": f"https://www.youtube.com/watch?v={vid}",
        "chapters": [{"start_time": c["start_time"], "title": c.get("title") or ""}
                     for c in (e.get("chapters") or []) if c.get("start_time") is not None]
    }
    _VINFO_CACHE[vid] = (now, info)
    return info


def list_transcripts(vid: str) -> list[dict]:
    tl = YouTubeTranscriptApi().list(vid)
    return [{"code": t.language_code, "name": t.language, "generated": t.is_generated,
             "translatable": t.is_translatable} for t in tl]


def fetch_segments(vid: str, code: str) -> list[dict]:
    t = YouTubeTranscriptApi().list(vid).find_transcript([code])
    return [{"text": s.text, "start": s.start, "duration": s.duration} for s in t.fetch()]
