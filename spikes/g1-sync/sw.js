// G1 spike: fixed-op proxy to the local cue server. Never fetches caller-supplied URLs.
const BASE = "http://127.0.0.1:8765";

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.op === "cue" && Number.isInteger(msg.idx) && /^[a-z0-9-]{1,40}$/.test(msg.ep)) {
    const t0 = performance.now();
    fetch(`${BASE}/${msg.ep}/${msg.idx}.wav`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        reply({ ok: true, b64: toBase64(await r.arrayBuffer()), ms: performance.now() - t0 });
      })
      .catch((e) => reply({ ok: false, err: String(e) }));
    return true;
  }
  if (msg?.op === "ping") {
    fetch(`${BASE}/ping.txt`).then((r) => reply({ ok: r.ok, status: r.status })).catch((e) => reply({ ok: false, err: String(e) }));
    return true;
  }
});
