"""Subtitle text → TTS text. Rule set validated in Phase 0 G2 (0/29 broken cues). Bump NORM_VERSION on any change:
it is part of the cache key."""
import re
import unicodedata

NORM_VERSION = 1

_TAG = re.compile(r"<[^>]+>")
_SFX = re.compile(r"\[[^\]]*\]|\([^)]*\)")      # [thở dài], (cười): v3 Turbo treats [cười] as an emotion tag
_DASH = re.compile(r"(^|\n)\s*-\s*")             # dialogue markers
_ELLIPSIS = re.compile(r"\.{3,}|…")
_NL = re.compile(r"\s*\n\s*")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text)
    t = _TAG.sub("", t)
    t = _SFX.sub("", t)
    t = _DASH.sub(r"\1", t)
    t = _ELLIPSIS.sub(", ", t)
    t = _NL.sub(" ", t)
    t = _WS.sub(" ", t)
    return t.strip(" ,")
