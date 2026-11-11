import { send, type VoicesList } from "../shared/messages";
import { DEFAULT_SETTINGS, type Settings, type Voice } from "../shared/types";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
let settings: Settings = DEFAULT_SETTINGS;
let voices: Voice[] = [];
let serverDefault = "";
let player: HTMLAudioElement | null = null;
let recorded: Blob | null = null;

async function save(patch: Partial<Settings>): Promise<void> {
  settings = { ...settings, ...patch };
  await chrome.storage.local.set(patch);
}

function status(id: string, text: string, ok: boolean): void {
  const el = $(id);
  el.textContent = text;
  el.className = ok ? "ok" : "bad";
}

const GENDER: Record<string, string> = { female: "Nữ", male: "Nam" };

async function loadVoices(): Promise<void> {
  const r = await send<VoicesList>({ op: "voices.list" });
  if (!r.ok) {
    $("voices").innerHTML = "";
    status("connStatus", `Không lấy được danh sách giọng (${r.code})`, false);
    return;
  }
  voices = r.data.voices;
  serverDefault = r.data.default;
  render();
}

function render(): void {
  const q = $<HTMLInputElement>("q").value.trim().toLowerCase();
  const g = $<HTMLSelectElement>("fGender").value;
  const reg = $<HTMLSelectElement>("fRegion").value;
  const current = settings.defaultVoice ?? serverDefault;
  const body = $("voices");
  body.replaceChildren();
  for (const v of voices) {
    if (q && !v.name.toLowerCase().includes(q)) continue;
    if (g && v.gender !== g) continue;
    if (reg && v.region !== reg) continue;
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = v.name + (v.kind === "user" ? " (của bạn)" : "");
    if (v.id === current) {
      const tag = document.createElement("span");
      tag.className = "default";
      tag.textContent = " · mặc định";
      name.append(tag);
    }
    tr.append(name);
    for (const t of [GENDER[v.gender] ?? "", v.region, v.style]) {
      const td = document.createElement("td");
      td.textContent = t;
      tr.append(td);
    }
    const actions = document.createElement("td");
    actions.append(button("▶ Nghe", `Nghe thử ${v.name}`, () => preview(v)));
    if (v.id !== current) actions.append(button("Đặt mặc định", `Đặt ${v.name} làm mặc định`, () => setDefault(v)));
    if (v.kind === "user") {
      actions.append(button("Đổi tên", `Đổi tên ${v.name}`, () => rename(v)));
      actions.append(button("Xoá", `Xoá ${v.name}`, () => remove(v)));
    }
    tr.append(actions);
    body.append(tr);
  }
}

function button(text: string, aria: string, fn: () => unknown): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  b.setAttribute("aria-label", aria);
  b.addEventListener("click", () => void fn());
  return b;
}

async function preview(v: Voice): Promise<void> {
  const r = await send<string | null>({ op: "voices.preview", id: v.id, text: $<HTMLInputElement>("sample").value });
  if (!r.ok || !r.data) return alert(`Không nghe thử được (${r.ok ? "rỗng" : r.code})`);
  player?.pause();
  player = new Audio(`data:audio/wav;base64,${r.data}`);
  await player.play();
}

async function setDefault(v: Voice): Promise<void> {
  await save({ defaultVoice: v.id });
  render();
}

async function rename(v: Voice): Promise<void> {
  const name = prompt("Tên mới", v.name)?.trim();
  if (!name) return;
  const r = await send({ op: "voices.rename", id: v.id, name });
  if (!r.ok) return alert(`Không đổi tên được (${r.code})`);
  await loadVoices();
}

async function remove(v: Voice): Promise<void> {
  if (!confirm(`Xoá giọng "${v.name}"? Phim đang dùng giọng này sẽ chuyển về giọng mặc định.`)) return;
  const r = await send({ op: "voices.delete", id: v.id });
  if (!r.ok) return alert(`Không xoá được (${r.code})`);
  if (settings.defaultVoice === v.id) await save({ defaultVoice: null });
  const map = Object.fromEntries(Object.entries(settings.voiceByTitle).filter(([, id]) => id !== v.id));
  await save({ voiceByTitle: map });
  await loadVoices();
}

function toB64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function clone(): Promise<void> {
  const name = $<HTMLInputElement>("cloneName").value.trim();
  const file = $<HTMLInputElement>("cloneFile").files?.[0] ?? null;
  const clip = file ?? recorded;
  if (!name) return status("cloneStatus", "Nhập tên giọng.", false);
  if (!clip) return status("cloneStatus", "Chọn file hoặc ghi âm.", false);
  if (!$<HTMLInputElement>("consent").checked) return status("cloneStatus", "Cần xác nhận quyền sử dụng giọng.", false);
  if (clip.size > 10 * 1024 * 1024) return status("cloneStatus", "File quá lớn (> 10 MB).", false);
  status("cloneStatus", "Đang tạo…", true);
  const r = await send<Voice>({ op: "voices.create", name, consent: true, mime: clip.type || "audio/wav",
                                clipB64: toB64(await clip.arrayBuffer()) });
  if (!r.ok) {
    const why: Record<string, string> = { clip_duration: "clip phải dài 2–15 giây", unsupported_audio: "định dạng không hỗ trợ",
                                          consent_required: "cần xác nhận quyền", payload_too_large: "file quá lớn" };
    return status("cloneStatus", `Lỗi: ${why[r.code] ?? r.code}`, false);
  }
  status("cloneStatus", `Đã tạo giọng "${r.data.name}".`, true);
  recorded = null;
  $<HTMLInputElement>("cloneFile").value = "";
  await loadVoices();
}

/** Record from the mic into a WAV-compatible blob (MediaRecorder gives webm/opus; the server reads it via
 * libsndfile only if supported, so convert to 16-bit PCM WAV here). */
async function record(): Promise<void> {
  const btn = $<HTMLButtonElement>("recBtn");
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    return status("recStatus", "Không truy cập được micro — hãy dùng file.", false);
  }
  const ctx = new AudioContext();
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  proc.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(proc);
  proc.connect(ctx.destination);
  btn.disabled = true;
  for (let s = 8; s > 0; s--) {
    status("recStatus", `Đang ghi… ${s}s`, true);
    await new Promise((r) => setTimeout(r, 1000));
  }
  proc.disconnect();
  src.disconnect();
  stream.getTracks().forEach((t) => t.stop());
  const rate = ctx.sampleRate;
  await ctx.close();
  recorded = wav(chunks, rate);
  btn.disabled = false;
  status("recStatus", "Đã ghi 8 giây. Bấm “Tạo giọng”.", true);
}

function wav(chunks: Float32Array[], rate: number): Blob {
  const n = chunks.reduce((a, c) => a + c.length, 0);
  const view = new DataView(new ArrayBuffer(44 + n * 2));
  const w = (o: number, s: string) => [...s].forEach((ch, i) => view.setUint8(o + i, ch.charCodeAt(0)));
  w(0, "RIFF"); view.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); w(36, "data"); view.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (const x of c) { view.setInt16(o, Math.max(-1, Math.min(1, x)) * 0x7fff, true); o += 2; }
  return new Blob([view], { type: "audio/wav" });
}

async function exportMetrics(): Promise<void> {
  const tabs = await chrome.tabs.query({ url: "https://chophim.lol/xem/*" });
  const out = $("metricsOut");
  out.hidden = false;
  if (!tabs[0]?.id) { out.textContent = "Không có tab chophim nào đang mở."; return; }
  const m = await chrome.tabs.sendMessage(tabs[0].id, { op: "tab.metrics" }) as
    { av: [number, number, number][]; drift: number[]; outputLatency: number | null } | undefined;
  if (!m) { out.textContent = "Tab chưa sẵn sàng."; return; }
  const ms = (xs: number[]) => {
    if (!xs.length) return "n=0";
    const a = xs.map((x) => Math.abs(x * 1000)).sort((p, q) => p - q);
    const mean = xs.reduce((p, q) => p + q, 0) / xs.length * 1000;
    return `n=${xs.length} p50=${a[Math.floor(a.length * 0.5)]!.toFixed(1)} p95=${a[Math.floor(a.length * 0.95)]!.toFixed(1)} ` +
           `max=${a[a.length - 1]!.toFixed(1)} signed_mean=${mean.toFixed(1)} ms`;
  };
  const onset = m.av.filter((x) => x[1] === 1 && !x[2]).map((x) => x[0]);
  out.textContent = [
    `outputLatency: ${m.outputLatency}`,
    `A/V (đầu câu, 1x): ${ms(onset)}`,
    `A/V (vào giữa câu): ${ms(m.av.filter((x) => x[2]).map((x) => x[0]))}`,
    `A/V (tốc độ ≠ 1): ${ms(m.av.filter((x) => x[1] !== 1).map((x) => x[0]))}`,
    `Drift giữa câu: ${ms(m.drift)}`,
    onset.length >= 50 ? `Gợi ý bù trễ: biasMs = ${Math.round(settings.biasMs + onset.reduce((p, q) => p + q, 0) / onset.length * 1000)}` : "",
    "", JSON.stringify(m),
  ].join("\n");
}

async function main(): Promise<void> {
  settings = (await chrome.storage.local.get(DEFAULT_SETTINGS)) as Settings;
  $<HTMLInputElement>("endpoint").value = settings.endpoint;
  $<HTMLInputElement>("token").value = settings.token;
  $<HTMLInputElement>("biasMs").value = String(settings.biasMs);
  $<HTMLInputElement>("debug").checked = settings.debug;
  $("saveConn").addEventListener("click", async () => {
    await save({ endpoint: $<HTMLInputElement>("endpoint").value.trim() || DEFAULT_SETTINGS.endpoint,
                 token: $<HTMLInputElement>("token").value.trim() });
    const h = await send<{ voices: number }>({ op: "health" });
    status("connStatus", h.ok ? `Kết nối OK — ${h.data.voices} giọng` :
      h.status === 401 || h.code === "no_token" ? "Sai token" : `Không kết nối được (${h.code})`, h.ok);
    if (h.ok) await loadVoices();
  });
  $<HTMLInputElement>("biasMs").addEventListener("change", (e) =>
    void save({ biasMs: Number((e.target as HTMLInputElement).value) || 0 }));
  $<HTMLInputElement>("debug").addEventListener("change", (e) =>
    void save({ debug: (e.target as HTMLInputElement).checked }));
  for (const id of ["q", "fGender", "fRegion"]) $(id).addEventListener("input", render);
  $("cloneBtn").addEventListener("click", () => void clone());
  $("recBtn").addEventListener("click", () => void record());
  $("exportMetrics").addEventListener("click", () => void exportMetrics());
  if (settings.token) await loadVoices();
}

void main();
