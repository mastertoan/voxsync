"""Throwaway: synthesize real VTT cues, compare speech duration vs cue slot."""
import json, re, statistics, sys, time, urllib.request
from vieneu import Vieneu

URL = "https://chophim.lol/api/storage/subtitles?file=vision-truy-tim-ban-nga-tap-1-1791955231891-cce2ae46-tap_01_vietnamese.vtt"
raw = urllib.request.urlopen(URL).read().decode()
ts = lambda s: sum(float(x) * m for x, m in zip(s.split(":"), (3600, 60, 1)))
cues = []
for block in raw.split("\n\n"):
    m = re.search(r"([\d:.]+) --> ([\d:.]+)[^\n]*\n(.+)", block, re.S)
    if m:
        text = re.sub(r"<[^>]+>|\[[^\]]+\]", "", m.group(3)).replace("\n", " ").strip(" -")
        if text:
            cues.append((ts(m.group(1)), ts(m.group(2)), text))
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 150
print("total cues", len(cues), "span_min", round(cues[-1][1] / 60, 1))
tts = Vieneu(backend="onnx", threads=6)
ratios, gen, audio = [], 0.0, 0.0
for i, (s, e, text) in enumerate(cues[:LIMIT]):
    nxt = cues[i + 1][0] if i + 1 < len(cues) else e + 2
    t0 = time.perf_counter(); wav = tts.infer(text, apply_watermark=False); gen += time.perf_counter() - t0
    d = len(wav) / tts.sample_rate; audio += d
    ratios.append((d / (e - s), d / max(nxt - s, 0.05)))
cue_r = sorted(r[0] for r in ratios); gap_r = sorted(r[1] for r in ratios)
pct = lambda a, p: round(a[min(len(a) - 1, int(p * len(a)))], 2)
out = {
    "n": len(ratios), "gen_s": round(gen, 1), "audio_s": round(audio, 1), "rtf": round(gen / audio, 3),
    "speech_over_cue_p50/p90/p99": [pct(cue_r, .5), pct(cue_r, .9), pct(cue_r, .99)],
    "speech_over_slot_until_next_cue_p50/p90/p99": [pct(gap_r, .5), pct(gap_r, .9), pct(gap_r, .99)],
    "frac_overflow_cue": round(sum(r > 1 for r in cue_r) / len(cue_r), 2),
    "frac_need_>1.25x_to_fit_slot": round(sum(r > 1.25 for r in gap_r) / len(gap_r), 2),
    "frac_need_>1.5x_to_fit_slot": round(sum(r > 1.5 for r in gap_r) / len(gap_r), 2),
}
print("FIT " + json.dumps(out, ensure_ascii=False))
