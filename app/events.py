def step(w, id, label, status, detail="", **extra):
    """Every graph node reports what it is doing; the UI renders these live."""
    w({"type": "step", "id": id, "label": label, "status": status, "detail": detail, **extra})


def fmt_ts(sec) -> str:
    sec = int(sec or 0); h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def tok(text: str) -> int:
    return int(len(text.split()) * 1.35) + 1
