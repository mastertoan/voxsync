"""Subtitle text → TTS text. Rule set validated in Phase 0 G2 (0/29 broken cues).

The cache key is built from the *output* text, so a rule change can never serve stale audio; NORM_VERSION only
needs a bump if the meaning of an identical output string changes.

Emotion: VieNeu v3 Turbo knows exactly three non-verbal tags (`[cười]`, `[thở dài]`, `[hắng giọng]`). Sound
descriptions in the subtitle that mean one of them are rewritten to that tag when they accompany spoken text;
every other description is dropped. A cue that is *only* a sound description stays silent: the original track
already carries the real sigh/laugh, and dubbing it would duck the original.
"""
import re
import unicodedata

NORM_VERSION = 1

_TAG = re.compile(r"<[^>]+>")
_SFX = re.compile(r"\[([^\]]*)\]|\(([^)]*)\)")
_DASH = re.compile(r"(^|\n)\s*-\s*|(?<=\s)-\s+")  # dialogue markers: line start, or " - " mid-line (not 50-50)
_SPACE_PUNCT = re.compile(r"\s+([,.!?;:])")
_ELLIPSIS = re.compile(r"\.{3,}|…")
_NL = re.compile(r"\s*\n\s*")
_WS = re.compile(r"\s+")
_EMOTIONS = (                                    # first match wins; matched against the lower-cased description
    (re.compile(r"cười|khúc khích|chuckle|laugh"), "[cười]"),
    (re.compile(r"thở dài|thở phào|sigh"), "[thở dài]"),
    (re.compile(r"hắng giọng|đằng hắng|e hèm|clears? throat"), "[hắng giọng]"),
)


def _emotion(desc: str) -> str:
    d = desc.lower()
    return next((tag for rx, tag in _EMOTIONS if rx.search(d)), "")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text)
    t = _TAG.sub("", t)
    spoken = _SFX.sub("", t)
    if not _WS.sub("", _DASH.sub("", spoken)).strip(" ,.-"):
        return ""                                # sound description only: silent
    t = _SFX.sub(lambda m: f" {_emotion(m.group(1) or m.group(2) or '')} ", t)
    t = _DASH.sub(lambda m: m.group(1) or "", t)
    t = _ELLIPSIS.sub(", ", t)
    t = _NL.sub(" ", t)
    t = _WS.sub(" ", t)
    t = _SPACE_PUNCT.sub(r"\1", t)
    return t.strip(" ,")
