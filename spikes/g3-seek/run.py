"""G3 spike (throwaway): sequential pre-gen queue + cue cache under a seek storm.

Real VieNeu ONNX synthesis, simulated 1x playback clock over episode-1 VTT.
Policy under test: one worker synthesizes cues in order starting from the first
uncached cue at/after the playhead; a seek just moves that cursor (no mid-job cancel).
Metrics: time from seek until the first cue at/after the new position is cached,
stalls (a cue's start time reached while not cached), RSS over time.
"""
import json, random, re, resource, statistics, sys, threading, time, urllib.request
from vieneu import Vieneu

URL = "https://chophim.lol/api/storage/subtitles?file=vision-truy-tim-ban-nga-tap-1-1791955231891-cce2ae46-tap_01_vietnamese.vtt"
ts = lambda s: sum(float(x) * m for x, m in zip(s.split(":"), (3600, 60, 1)))
cues = []
for block in urllib.request.urlopen(URL).read().decode().split("\n\n"):
    m = re.search(r"([\d:.]+) --> ([\d:.]+)[^\n]*\n(.+)", block, re.S)
    if m:
        text = re.sub(r"<[^>]+>|\[[^\]]+\]", "", m.group(3)).replace("\n", " ").replace("- ", "").strip()
        if text:
            cues.append((ts(m.group(1)), ts(m.group(2)), text))

tts = Vieneu(backend="onnx", threads=6)
tts.infer("Xin chào.", apply_watermark=False)
cache: dict[int, float] = {}          # cue index -> audio seconds (stand-in for the WAV)
lock = threading.Lock()
state = {"pos": 0.0, "t0": time.perf_counter(), "epoch": 0, "stop": False}

def playhead():
    with lock:
        return state["pos"] + (time.perf_counter() - state["t0"])

def first_idx(pos):
    return next((i for i, c in enumerate(cues) if c[0] >= pos), len(cues))

def worker():
    while not state["stop"]:
        i = first_idx(playhead())
        while i < len(cues) and i in cache:
            i += 1
        if i >= len(cues):
            time.sleep(0.05); continue
        wav = tts.infer(cues[i][2], apply_watermark=False)
        cache[i] = len(wav) / tts.sample_rate

threading.Thread(target=worker, daemon=True).start()
random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
duration = cues[-1][1]
ttfa, stalls, rss = [], 0, []
checked: set[int] = set()

def watch(seconds):
    """Advance real time, counting cues whose start passes while uncached."""
    global stalls
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        p = playhead()
        for i in range(max(0, first_idx(p) - 3), first_idx(p)):
            if i not in checked:
                checked.add(i)
                if i not in cache:
                    stalls += 1
        time.sleep(0.02)

watch(8)  # normal start
for k in range(10):
    target = random.uniform(0, duration - 60)
    with lock:
        state["pos"], state["t0"] = target, time.perf_counter()
    checked.update(range(0, first_idx(target)))
    t_seek = time.perf_counter()
    nxt = first_idx(target)
    while nxt not in cache and time.perf_counter() - t_seek < 10:
        time.sleep(0.005)
    ttfa.append(time.perf_counter() - t_seek)
    rss.append(round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 3))
    watch(random.uniform(1.0, 6.0))  # user dwells briefly before next seek
state["stop"] = True
out = {
    "cues": len(cues), "seeks": len(ttfa),
    "ttfa_s": [round(x, 3) for x in ttfa],
    "ttfa_p50": round(statistics.median(ttfa), 3), "ttfa_max": round(max(ttfa), 3),
    "stalls": stalls, "cached": len(cache), "peak_rss_gb_series": rss,
}
print("G3 " + json.dumps(out))
