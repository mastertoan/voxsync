"""G1 helper: synthesize cues of an episode VTT to <out>/<ep>/<idx>.wav (idx = order after normalization,
same rule as content.js). Usage: synth_cues.py <vtt_url> <ep> <out_dir> [max_start_s]"""
import re, sys, urllib.request
from pathlib import Path
import soundfile as sf
from vieneu import Vieneu

url, ep, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
max_start = float(sys.argv[4]) if len(sys.argv) > 4 else 1e9
ts = lambda s: sum(float(x) * m for x, m in zip(s.split(":"), (3600, 60, 1)))

def normalize(t):
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", t)
    t = re.sub(r"(^|\n)\s*-\s*", r"\1", t)
    t = re.sub(r"\.{3,}|…", ", ", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    return re.sub(r"\s+", " ", t).strip(" ,")

cues = []
for block in re.split(r"\r?\n\r?\n", urllib.request.urlopen(url).read().decode()):
    m = re.search(r"([\d:.]+) --> ([\d:.]+)[^\n]*\n(.+)", block, re.S)
    if m and (t := normalize(m.group(3))):
        cues.append((ts(m.group(1)), t))
(out / ep).mkdir(parents=True, exist_ok=True)
tts = Vieneu(backend="onnx", threads=6)
n = 0
for i, (start, text) in enumerate(cues):
    if start > max_start:
        break
    sf.write(out / ep / f"{i}.wav", tts.infer(text, apply_watermark=False), tts.sample_rate, subtype="PCM_16")
    n += 1
(out / "ping.txt").write_text("ok")
print(f"{ep}: {n}/{len(cues)} cues -> {out / ep}")
