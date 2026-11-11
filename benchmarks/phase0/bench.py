"""Throwaway Phase-0 benchmark. Usage: python bench.py <label> [k=v ...]  (kwargs go to Vieneu())."""
import json, os, statistics, sys, time
import numpy as np

label = sys.argv[1]
kw = {}
for a in sys.argv[2:]:
    k, v = a.split("=", 1)
    kw[k] = int(v) if v.isdigit() else v

if os.environ.get("ORT_PROVIDER"):  # force an ORT execution provider (CoreML test)
    import onnxruntime as ort
    _orig = ort.InferenceSession.__init__
    prov = os.environ["ORT_PROVIDER"]
    opts = json.loads(os.environ.get("ORT_PROVIDER_OPTS", "{}"))
    def patched(self, path, so=None, providers=None, **k):
        return _orig(self, path, so, providers=[(prov, opts), "CPUExecutionProvider"], **k)
    ort.InferenceSession.__init__ = patched

from vieneu import Vieneu

TEXTS = {
    "short10": "Tôi là ai?",
    "med50": "Nhưng tôi lại được nhào nặn bởi Stark và Banner.",
    "long150": "Muộn rồi bà con ơi, mấy người không hiểu từ đóng cửa nghĩa là gì à? Được rồi, chúng tôi đi đây. Thế thì đến St. Louis mà tìm họ hàng đi.",
}
N = int(os.environ.get("N", "5"))

t0 = time.perf_counter(); tts = Vieneu(**kw); load = time.perf_counter() - t0
t0 = time.perf_counter(); tts.infer("Xin chào.", apply_watermark=False); warm = time.perf_counter() - t0
res = {"label": label, "kw": kw, "load_s": round(load, 2), "warmup_s": round(warm, 2),
       "backend": getattr(tts, "backend", "?")}
for name, text in TEXTS.items():
    lat, rtf = [], []
    for _ in range(N):
        t0 = time.perf_counter(); wav = tts.infer(text, apply_watermark=False); dt = time.perf_counter() - t0
        dur = len(wav) / tts.sample_rate
        lat.append(dt); rtf.append(dt / max(dur, 1e-6))
    res[name] = {"lat_med_s": round(statistics.median(lat), 3), "lat_max_s": round(max(lat), 3),
                 "rtf_med": round(statistics.median(rtf), 3)}
if os.environ.get("SAVE"):
    import soundfile as sf
    out_dir = os.environ.get("OUT_DIR", ".")
    sf.write(f"{out_dir}/{label}.wav", tts.infer(TEXTS["med50"], apply_watermark=False), tts.sample_rate)
try:
    import resource; res["peak_rss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 2)
except Exception:
    pass
print("RESULT " + json.dumps(res, ensure_ascii=False), flush=True)
