import os
import re
import time
import logging
import tempfile
from http.cookiejar import MozillaCookieJar
import requests
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

logger = logging.getLogger(__name__)

_URL_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})")

# In-memory cache to make repeated searches & info lookups instantaneous
_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_VINFO_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600  # 1 hour

_TEMP_COOKIE_FILE: str | None = None


def _get_cookie_file() -> str | None:
    """Finds cookies.txt or creates one from YOUTUBE_COOKIES environment variable."""
    global _TEMP_COOKIE_FILE

    # 1. Local cookies.txt file
    for p in ["cookies.txt", "data/cookies.txt", "/app/cookies.txt"]:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p

    # 2. YOUTUBE_COOKIES environment variable (e.g. on Render)
    cookies_content = os.getenv("YOUTUBE_COOKIES") or os.getenv("YOUTUBE_COOKIES_CONTENT")
    if cookies_content and cookies_content.strip():
        if not _TEMP_COOKIE_FILE or not os.path.exists(_TEMP_COOKIE_FILE):
            f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt")
            if not cookies_content.startswith("# Netscape"):
                f.write("# Netscape HTTP Cookie File\n")
            f.write(cookies_content)
            f.close()
            _TEMP_COOKIE_FILE = f.name
        return _TEMP_COOKIE_FILE

    return None


def _get_transcript_api() -> YouTubeTranscriptApi:
    """Builds YouTubeTranscriptApi with browser headers, cookie jar, and proxy support."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.youtube.com/",
        "Origin": "https://www.youtube.com",
    })

    # Load cookies if available
    c_file = _get_cookie_file()
    if c_file:
        try:
            cj = MozillaCookieJar(c_file)
            cj.load(ignore_discard=True, ignore_expires=True)
            session.cookies = cj
        except Exception as e:
            logger.warning(f"Could not load cookies from {c_file}: {e}")

    # Configure proxy if available
    proxy_url = os.getenv("YOUTUBE_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    proxy_config = None
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
        proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)

    return YouTubeTranscriptApi(proxy_config=proxy_config, http_client=session)


def extract_video_id(text: str):
    text = text.strip()
    m = _URL_ID.search(text)
    if m: return m.group(1)
    if re.fullmatch(r"[\w-]{11}", text) and re.search(r"[\dA-Z_-]", text):
        return text
    return None


def _thumb(vid): return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"


def _ydl(**o):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 6,
        **o
    }
    c_file = _get_cookie_file()
    if c_file:
        opts["cookiefile"] = c_file
    proxy_url = os.getenv("YOUTUBE_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if proxy_url:
        opts["proxy"] = proxy_url
    return yt_dlp.YoutubeDL(opts)


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
    """Lists transcripts with automatic yt-dlp fallback if YouTube blocks datacenter IP."""
    try:
        api = _get_transcript_api()
        tl = api.list(vid)
        return [{"code": t.language_code, "name": t.language, "generated": t.is_generated,
                 "translatable": t.is_translatable} for t in tl]
    except Exception as e:
        logger.warning(f"youtube_transcript_api.list failed for {vid} ({e}). Attempting yt-dlp fallback...")

    # Fallback to yt-dlp subtitles
    try:
        with _ydl() as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
        subs = info.get("subtitles") or {}
        auto = info.get("automatic_captions") or {}
        langs = []
        for code, formats in subs.items():
            name = formats[0].get("name") if formats else code
            langs.append({"code": code, "name": name or code, "generated": False, "translatable": True})
        for code, formats in auto.items():
            if code not in [l["code"] for l in langs]:
                name = formats[0].get("name") if formats else code
                langs.append({"code": code, "name": (name or code) + " (auto)", "generated": True, "translatable": False})
        if langs:
            return langs
    except Exception as ex:
        logger.warning(f"yt-dlp subtitle listing also failed for {vid}: {ex}")

    raise RuntimeError(f"Could not retrieve transcripts for {vid}. If running in cloud (Render/AWS), configure YOUTUBE_COOKIES or YOUTUBE_PROXY.")


def fetch_segments(vid: str, code: str) -> list[dict]:
    """Fetches transcript caption segments with cookies and headers."""
    api = _get_transcript_api()
    t = api.list(vid).find_transcript([code])
    return [{"text": s.text, "start": s.start, "duration": s.duration} for s in t.fetch()]
