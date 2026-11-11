"""G2 spike (throwaway): objective proxy for TTS quality on real VTT cues.

Human ABX cannot be automated, so this measures intelligibility with ASR
(mlx-whisper large-v3-turbo, Vietnamese) as a proxy and writes a blind ABX
packet for a human listener:
  (a) fp32 vs int8 CER;
  (b) 1.25x pitch-preserving stretch via audiotsm WSOLA vs ffmpeg atempo, CER;
  (c) normalization: raw cue text vs crude normalizer -> % cues "broken"
      (CER > 0.3 or duration anomaly).
Usage: run.py <out_dir> [n_cues]
"""
import json, os, random, re, subprocess, sys, tempfile, unicodedata, urllib.request
from pathlib import Path
import numpy as np, soundfile as sf, jiwer, mlx_whisper
from audiotsm import wsola
from audiotsm.io.array import ArrayReader, ArrayWriter
from vieneu import Vieneu

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 30
URL = "https://chophim.lol/api/storage/subtitles?file=vision-truy-tim-ban-nga-tap-1-1791955231891-cce2ae46-tap_01_vietnamese.vtt"
ASR = "mlx-community/whisper-large-v3-turbo"
SR = 48_000

raw_cues = []
for block in urllib.request.urlopen(URL).read().decode().split("\n\n"):
    m = re.search(r"-->[^\n]*\n(.+)", block, re.S)
    if m:
        raw_cues.append(m.group(1).strip())

def normalize(t: str) -> str:
    """Crude normalizer under test: tags, SFX, dialogue dashes, line joins."""
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", t)          # [thở dài], (cười)
    t = re.sub(r"(^|\n)\s*-\s*", r"\1", t)               # dialogue markers
    t = re.sub(r"\.{3,}|…", ", ", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    return re.sub(r"\s+", " ", t).strip(" ,")

def ref_words(t: str) -> str:
    t = unicodedata.normalize("NFC", normalize(t)).lower()
    return re.sub(r"[^\w\s]", " ", t).split() and " ".join(re.sub(r"[^\w\s]", " ", t).split())

# Hazard-weighted sample: every cue with a hazard first, then fill randomly.
random.seed(3)
hazard = [c for c in raw_cues if re.search(r"\[|\n|^-|\d|\b[A-Z][a-z]+\b", c)]
plain = [c for c in raw_cues if c not in hazard]
sample = random.sample(hazard, min(len(hazard), N * 2 // 3))
sample += random.sample(plain, N - len(sample))
sample = [c for c in sample if normalize(c)]

def asr(wav: np.ndarray) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        sf.write(f.name, wav, SR)
        r = mlx_whisper.transcribe(f.name, path_or_hf_repo=ASR, language="vi",
                                   condition_on_previous_text=False, temperature=0.0)
    t = unicodedata.normalize("NFC", r["text"]).lower()
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())

def cer(ref: str, hyp: str) -> float:
    return jiwer.cer(ref, hyp) if ref else 0.0

def wsola_stretch(wav, rate):
    reader = ArrayReader(wav[np.newaxis, :].astype(np.float32))
    writer = ArrayWriter(channels=1)
    wsola(channels=1, speed=rate).run(reader, writer)
    return writer.data[0]

def atempo(wav, rate):
    with tempfile.TemporaryDirectory() as d:
        i, o = f"{d}/i.wav", f"{d}/o.wav"
        sf.write(i, wav, SR)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", i, "-filter:a", f"atempo={rate}", o], check=True)
        return sf.read(o)[0].astype(np.float32)

fp32 = Vieneu(backend="onnx", threads=6)
int8 = Vieneu(backend="onnx", threads=6, precision="int8", onnx_dir=os.environ.get("INT8_DIR", "models/onnx_int8"))

rows, abx = [], []
for k, cue in enumerate(sample):
    ref = ref_words(cue)
    w_raw = fp32.infer(re.sub(r"\s*\n\s*", " ", cue), apply_watermark=False)   # raw text (no normalizer)
    w32 = fp32.infer(normalize(cue), apply_watermark=False)
    w8 = int8.infer(normalize(cue), apply_watermark=False)
    ws, wa = wsola_stretch(w32, 1.25), atempo(w32, 1.25)
    row = {"k": k, "cue": cue, "ref": ref,
           "cer_raw": cer(ref, asr(w_raw)), "cer_fp32": cer(ref, asr(w32)), "cer_int8": cer(ref, asr(w8)),
           "cer_wsola125": cer(ref, asr(ws)), "cer_atempo125": cer(ref, asr(wa)),
           "dur_raw": len(w_raw) / SR, "dur_fp32": len(w32) / SR}
    rows.append(row)
    # blind ABX packet: A/B order randomized, key stored separately
    flip = random.random() < 0.5
    pair = (w8, w32) if flip else (w32, w8)
    for name, w in (("A", pair[0]), ("B", pair[1])):
        sf.write(OUT / f"{k:02d}_quant_{name}.wav", w, SR)
    sf.write(OUT / f"{k:02d}_stretch_wsola.wav", ws, SR)
    sf.write(OUT / f"{k:02d}_stretch_atempo.wav", wa, SR)
    abx.append({"k": k, "quant_A_is": "int8" if flip else "fp32", "text": normalize(cue)})
    print(f"{k:02d} raw={row['cer_raw']:.2f} fp32={row['cer_fp32']:.2f} int8={row['cer_int8']:.2f} "
          f"wsola={row['cer_wsola125']:.2f} atempo={row['cer_atempo125']:.2f} | {normalize(cue)[:60]}", flush=True)

(OUT / "abx_key.json").write_text(json.dumps(abx, ensure_ascii=False, indent=1))
(OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
mean = lambda k: round(float(np.mean([r[k] for r in rows])), 3)
broken = lambda k: round(sum(r[k] > 0.3 for r in rows) / len(rows), 3)
print("G2 " + json.dumps({
    "n": len(rows),
    "cer_mean": {k: mean(k) for k in ("cer_raw", "cer_fp32", "cer_int8", "cer_wsola125", "cer_atempo125")},
    "frac_cer_gt_0.3": {k: broken(k) for k in ("cer_raw", "cer_fp32", "cer_int8", "cer_wsola125", "cer_atempo125")},
}))
