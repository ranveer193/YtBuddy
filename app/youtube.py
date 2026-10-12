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

# In-memory cache with active TTL eviction — capped to avoid RAM growth
_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_VINFO_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600   # 1 hour
_SEARCH_CACHE_MAX = 30   # max distinct search queries kept
_VINFO_CACHE_MAX = 50    # max distinct video metadata entries kept

_TEMP_COOKIE_FILE: str | None = None


def _evict_cache(cache: dict, max_size: int):
    """Remove expired entries; if still too big, remove the oldest ones."""
    now = time.time()
    expired = [k for k, (ts, _) in cache.items() if now - ts > _CACHE_TTL]
    for k in expired:
        del cache[k]
    # If still over max, evict oldest by timestamp
    if len(cache) > max_size:
        oldest = sorted(cache.items(), key=lambda x: x[1][0])
        for k, _ in oldest[:len(cache) - max_size]:
            del cache[k]


_TRANSCRIPTAPI_BASE = "https://transcriptapi.com/api/v2/youtube/transcript"
_TRANSCRIPTAPI_TIMEOUT = 30  # seconds


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

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
        raw_text = cookies_content.strip()
        # Decode base64 if provided in base64 format
        try:
            import base64
            decoded = base64.b64decode(raw_text).decode("utf-8", errors="ignore")
            if "# Netscape" in decoded or "\tyoutube.com" in decoded or "\t.youtube.com" in decoded:
                raw_text = decoded
        except Exception:
            pass

        if not _TEMP_COOKIE_FILE or not os.path.exists(_TEMP_COOKIE_FILE):
            f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8")
            if not raw_text.startswith("# Netscape"):
                f.write("# Netscape HTTP Cookie File\n")
            f.write(raw_text)
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
            logger.info(f"Loaded {len(cj)} YouTube cookies from {c_file}")
        except Exception as e:
            logger.warning(f"Could not load cookies from {c_file}: {e}")
    else:
        logger.info("No YOUTUBE_COOKIES found in environment; running unauthenticated.")

    # Configure proxy if available
    proxy_url = os.getenv("YOUTUBE_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    proxy_config = None
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
        proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)

    return YouTubeTranscriptApi(proxy_config=proxy_config, http_client=session)


# ---------------------------------------------------------------------------
# transcriptapi.com helpers
# ---------------------------------------------------------------------------

def _transcriptapi_key() -> str:
    """Returns the transcriptapi.com key, preferring env var over config module."""
    key = os.getenv("TRANSCRIPTAPI_KEY", "")
    if not key:
        try:
            from app.config import settings
            key = settings.transcriptapi_key
        except Exception:
            pass
    return key.strip()


def _fetch_transcriptapi(vid: str) -> list[dict] | None:
    """
    Primary transcript source: transcriptapi.com
    Returns list of {text, start, duration} dicts, or None on failure/missing key.
    """
    key = _transcriptapi_key()
    if not key:
        return None

    try:
        resp = requests.get(
            _TRANSCRIPTAPI_BASE,
            params={"video_url": vid, "format": "json"},
            headers={"Authorization": f"Bearer {key}"},
            timeout=_TRANSCRIPTAPI_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            segments = data.get("transcript") or []
            if segments:
                logger.info(f"transcriptapi.com returned {len(segments)} segments for {vid}")
                return [{"text": s["text"], "start": s["start"], "duration": s["duration"]}
                        for s in segments]
            logger.warning(f"transcriptapi.com returned empty transcript for {vid}")
        else:
            logger.warning(f"transcriptapi.com returned HTTP {resp.status_code} for {vid}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"transcriptapi.com error for {vid}: {e}")

    return None


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

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
    """Search YouTube with bounded, actively-evicted in-memory caching."""
    q_norm = query.strip().lower()
    total_needed = offset + n
    cache_key = f"{q_norm}:{max(total_needed, 24)}"
    now = time.time()

    _evict_cache(_SEARCH_CACHE, _SEARCH_CACHE_MAX)

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


def _fetch_oembed_info(vid: str) -> dict:
    """Fallback metadata extractor using YouTube official oEmbed API and page scraping."""
    info = {
        "video_id": vid,
        "title": "YouTube Video",
        "channel": "YouTube Creator",
        "duration": 0,
        "thumbnail": _thumb(vid),
        "views": None,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "chapters": []
    }
    # 1. Try YouTube oEmbed endpoint (reliable, public, no IP blocks)
    try:
        r = requests.get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        if r.status_code == 200:
            d = r.json()
            if d.get("title"): info["title"] = d["title"]
            if d.get("author_name"): info["channel"] = d["author_name"]
            if d.get("thumbnail_url"): info["thumbnail"] = d["thumbnail_url"]
    except Exception as e:
        logger.warning(f"oEmbed fetch failed for {vid}: {e}")

    # 2. Try scraping playerResponse or transcriptapi duration
    try:
        r_page = requests.get(
            f"https://www.youtube.com/watch?v={vid}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=8
        )
        if r_page.status_code == 200:
            m = re.search(r'var ytInitialPlayerResponse\s*=\s*({.+?});(?:var|</script>)', r_page.text)
            if m:
                import json
                pdata = json.loads(m.group(1))
                vdetails = pdata.get("videoDetails", {})
                if vdetails.get("title") and info["title"] == "YouTube Video":
                    info["title"] = vdetails["title"]
                if vdetails.get("author") and info["channel"] == "YouTube Creator":
                    info["channel"] = vdetails["author"]
                if vdetails.get("lengthSeconds"):
                    info["duration"] = int(vdetails["lengthSeconds"])
                if vdetails.get("viewCount"):
                    info["views"] = int(vdetails["viewCount"])
    except Exception as e:
        logger.warning(f"PlayerResponse scrape failed for {vid}: {e}")

    # 3. If duration is still 0, check transcriptapi.com length_seconds
    if not info.get("duration"):
        key = _transcriptapi_key()
        if key:
            try:
                rt = requests.get(
                    _TRANSCRIPTAPI_BASE,
                    params={"video_url": vid, "format": "json"},
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=8
                )
                if rt.status_code == 200:
                    dt = rt.json()
                    if dt.get("length_seconds"):
                        info["duration"] = int(dt["length_seconds"])
            except Exception:
                pass

    return info


def video_info(vid: str) -> dict:
    """Fetch video metadata using lightweight oEmbed API + page scraping."""
    now = time.time()
    _evict_cache(_VINFO_CACHE, _VINFO_CACHE_MAX)
    if vid in _VINFO_CACHE:
        ts, cached = _VINFO_CACHE[vid]
        if now - ts < _CACHE_TTL:
            return cached

    info = _fetch_oembed_info(vid)
    _VINFO_CACHE[vid] = (now, info)
    return info


# ---------------------------------------------------------------------------
# Transcript fetching — 3-tier fallback chain
# ---------------------------------------------------------------------------

def list_transcripts(vid: str) -> list[dict]:
    """
    Lists available transcripts using a 3-tier fallback:
      1. transcriptapi.com  (primary — cloud-friendly, no IP blocks)
      2. youtube_transcript_api with cookies/headers
      3. yt-dlp subtitle listing
    """
    # --- Tier 1: transcriptapi.com ---
    segments = _fetch_transcriptapi(vid)
    if segments is not None:
        # We don't get language metadata from transcriptapi; default to "en"
        return [{"code": "en", "name": "English", "generated": True, "translatable": False}]

    # --- Tier 2: youtube_transcript_api (with cookies) ---
    try:
        api = _get_transcript_api()
        tl = api.list(vid)
        return [{"code": t.language_code, "name": t.language, "generated": t.is_generated,
                 "translatable": t.is_translatable} for t in tl]
    except Exception as e:
        logger.warning(f"youtube_transcript_api.list failed for {vid} ({e}). Attempting yt-dlp fallback...")

    # --- Tier 3: yt-dlp subtitles ---
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

    raise RuntimeError(
        f"Could not retrieve transcripts for {vid}. "
        "If running in cloud (Render/AWS), configure TRANSCRIPTAPI_KEY, YOUTUBE_COOKIES, or YOUTUBE_PROXY."
    )


def fetch_segments(vid: str, code: str) -> list[dict]:
    """
    Fetches caption segments using a 3-tier fallback:
      1. transcriptapi.com  (primary)
      2. youtube_transcript_api with cookies/headers
      3. yt-dlp subtitle listing (raises if nothing works)
    """
    # --- Tier 1: transcriptapi.com ---
    segments = _fetch_transcriptapi(vid)
    if segments is not None:
        return segments

    # --- Tier 2: youtube_transcript_api (with cookies) ---
    try:
        api = _get_transcript_api()
        t = api.list(vid).find_transcript([code])
        return [{"text": s.text, "start": s.start, "duration": s.duration} for s in t.fetch()]
    except Exception as e:
        logger.warning(f"youtube_transcript_api.fetch failed for {vid}/{code} ({e}). Trying yt-dlp...")

    # --- Tier 3: yt-dlp (no cookie file needed here since it's already baked in _ydl) ---
    raise RuntimeError(
        f"Could not fetch transcript segments for {vid}/{code}. "
        "Configure TRANSCRIPTAPI_KEY or YOUTUBE_COOKIES on the server."
    )
