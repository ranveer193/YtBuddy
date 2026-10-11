import re

# Only true disfluencies. The old list also removed "so/like/just/now/look/sure/right",
# which silently corrupts meaning ("like a dictionary", "right to left").
_FILLERS = ["you know", "i mean", "kind of", "sort of", "you see", "um", "uh", "uhm", "hmm",
            "basically", "honestly", "literally"]
_FILLER = re.compile(r"\b(?:%s)\b[,\s]*" % "|".join(sorted(map(re.escape, _FILLERS), key=len, reverse=True)), re.I)
_BRACKET = re.compile(r"\[[^\]]*\]|\((?:music|applause|laughter)[^)]*\)", re.I)
_STUTTER = re.compile(r"\b(\w{1,3})-\s?\1\b", re.I)
_REPEAT = re.compile(r"\b(\w+)(?:\s+\1\b)+", re.I)
_SP = re.compile(r"\s+")
_PUNCT = re.compile(r"\s+([.,!?;:])")


def clean_transcript(text: str) -> str:
    text = _BRACKET.sub(" ", text)
    text = _STUTTER.sub(r"\1", text)
    text = _FILLER.sub(" ", text)
    text = _REPEAT.sub(r"\1", text)
    text = _PUNCT.sub(r"\1", text)
    return _SP.sub(" ", text).strip()
