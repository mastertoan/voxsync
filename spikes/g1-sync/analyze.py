"""G1 analyzer: onset sync error from a content.js dump.

For every onset that actually reached output (not cancelled by a later stop) we know
its predicted output time on the performance.now() clock (getOutputTimestamp mapping).
Video clock samples [(perfMs, mediaT, rate)] give the true media time at that instant
by linear interpolation. Expected media time = cueStart + offset*rate.
Error = actual - expected; positive = TTS late vs picture.
NOTE: scheduling/clock error only; device output-latency differences between the video
renderer and our AudioContext need ears/mic.
Usage: analyze.py dump.json [label]
"""
import bisect, json, statistics, sys

d = json.load(open(sys.argv[1]))
label = sys.argv[2] if len(sys.argv) > 2 else ""
samples = sorted(d["samples"])
perf = [s[0] for s in samples]
errs, cancelled = [], 0
for o in d["onsets"]:
    if o.get("cancelled"):
        cancelled += 1
        continue
    t = o["outPerf"]
    i = bisect.bisect_left(perf, t)
    if i == 0 or i >= len(perf):
        continue
    (p0, m0, _), (p1, m1, _) = samples[i - 1], samples[i]
    if p1 - p0 > 200 or m1 < m0:          # gap (pause/seek) around onset: unmeasurable
        continue
    media = m0 + (m1 - m0) * (t - p0) / (p1 - p0)
    errs.append((media - (o["cueStart"] + o["offset"] * o["rate"])) * 1000)
a = sorted(abs(e) for e in errs)
def stats(xs_ms):
    a = sorted(abs(x) for x in xs_ms)
    if not a:
        return None
    q = lambda p: round(a[min(len(a) - 1, int(p * len(a)))], 1)
    return {"n": len(a), "p50": q(.5), "p95": q(.95), "max": q(1.0), "signed_mean": round(statistics.mean(xs_ms), 1)}

av = d.get("av", [])  # [err_s (+ = dub late), rate, lateJoin, idx] per presented frame
print("G1 " + json.dumps({
    "label": label, "onsets": len(d["onsets"]), "cancelled": cancelled,
    "sched_err_ms(onset vs currentTime samples)": stats(errs),
    "av_ms(rVFC presented frame) rate1 cue-start": stats([e[0] * 1000 for e in av if e[1] == 1 and not e[2]]),
    "av_ms rate1 late-join": stats([e[0] * 1000 for e in av if e[1] == 1 and e[2]]),
    "av_ms rate!=1 onset": stats([e[0] * 1000 for e in av if e[1] != 1]),
    "drift_ms(mid-cue vs currentTime)": stats([x[0] * 1000 for x in d.get("drift", [])]),
    "drift_resyncs": sum(e["ev"] == "drift" for e in d["events"]),
    "outputLatency": d.get("outputLatency"), "baseLatency": d.get("baseLatency"), "ctx": d.get("ctx"),
}))
