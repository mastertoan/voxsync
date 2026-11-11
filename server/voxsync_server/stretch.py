"""Duration alignment, decided at synthesis time (the VTT already says when the next cue starts).

Stretch: need = speech / slot, slot = next.start - cue.start (floored at MIN_SLOT so tiny gaps don't ask
for absurd rates):
  need <= 1           keep
  need <= MAX_RATE    atempo=need (pitch-preserving; beat WSOLA in Phase 0 G2)
  need >  MAX_RATE    atempo=MAX_RATE and let it run long

Timeline: each played cue occupies [start + delay, start + delay + dur/rate]. A cue's delay is how far the
previous *played* cue's end runs past its own start, so gaps absorb earlier lateness. A cue that would
start more than MAX_DELAY late is dropped whole (never truncated) and does not move the timeline.
"""
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

MAX_RATE = 1.25
MAX_DELAY = 0.5
MIN_SLOT = 0.3
MIN_STRETCH_DUR = 0.4   # atempo smears very short clips; let them run long instead


def decide_rate(speech_dur: float, start: float, next_start: float | None) -> float:
    if next_start is None or speech_dur < MIN_STRETCH_DUR:
        return 1.0
    need = speech_dur / max(next_start - start, MIN_SLOT)
    return 1.0 if need <= 1.0 else round(min(need, MAX_RATE), 3)


def delay_after(prev_end: float | None, start: float) -> float:
    """Start delay of a cue given the media time at which the previous played cue ends."""
    return 0.0 if prev_end is None else max(0.0, prev_end - start)


def should_drop(delay: float) -> bool:
    return delay > MAX_DELAY


def atempo(wav: np.ndarray, sr: int, rate: float) -> np.ndarray:
    if rate == 1.0:
        return wav
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d, "i.wav"), Path(d, "o.wav")
        sf.write(src, wav, sr, subtype="FLOAT")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter:a", f"atempo={rate}", str(dst)],
                       check=True, timeout=30)
        out, _ = sf.read(dst, dtype="float32")
    return out
