import { send, type VoicesList } from "../shared/messages";
import { DEFAULT_SETTINGS, type Settings, type TabStatus } from "../shared/types";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const PHASE_TEXT: Record<TabStatus["phase"], string> = {
  idle: "đang chờ video/phụ đề",
  "waiting-ad": "đang chờ hết quảng cáo",
  "no-subtitle": "không có phụ đề tiếng Việt tách rời (thử server Song Ngữ hoặc phụ đề thủ công)",
  armed: "đang lồng tiếng",
  "suspended-audio": "bấm vào trang (hoặc phím bất kỳ) để bật âm thanh",
  "server-offline": "không kết nối được server",
  unauthorized: "sai hoặc thiếu token — mở Cài đặt",
};

let settings: Settings = DEFAULT_SETTINGS;
let tabId: number | undefined;
let title: string | null = null;

async function save(patch: Partial<Settings>): Promise<void> {
  settings = { ...settings, ...patch };
  await chrome.storage.local.set(patch);
}

function bindRange(id: "dubVolume" | "duck" | "offsetMs", fmt: (v: number) => string): void {
  const input = $<HTMLInputElement>(id);
  const out = $<HTMLOutputElement>(id === "offsetMs" ? "offsetOut" : `${id}Out`);
  input.value = String(settings[id]);
  out.textContent = fmt(settings[id]);
  input.addEventListener("input", () => { out.textContent = fmt(Number(input.value)); });
  input.addEventListener("change", () => void save({ [id]: Number(input.value) }));
}

function tabMessage<T>(msg: unknown): Promise<T | null> {
  const { promise, resolve } = Promise.withResolvers<T | null>();
  if (tabId === undefined) resolve(null);
  else chrome.tabs.sendMessage(tabId, msg, (r: T) => resolve(chrome.runtime.lastError ? null : r ?? null));
  return promise;
}

async function refresh(): Promise<void> {
  const h = await send<{ voices: number; rtf: number | null }>({ op: "health" });
  $("serverDot").className = `dot ${h.ok ? "ok" : "bad"}`;
  $("server").textContent = h.ok ? `đang chạy (${h.data.voices} giọng)`
    : h.code === "no_token" || h.status === 401 ? "thiếu/sai token" : "không chạy";
  const st = await tabMessage<TabStatus>({ op: "tab.status" });
  if (!st) {
    $("subtitle").textContent = "mở một trang xem phim trên chophim.lol";
    $("progress").textContent = "–";
    return;
  }
  title = st.title;
  $("subtitle").textContent = st.episode ? `${st.episode} (${st.cues} câu) — ${PHASE_TEXT[st.phase]}`
                                         : PHASE_TEXT[st.phase];
  $("progress").textContent = st.cues ? `${st.ready}/${st.cues} câu, ${st.readyAhead} câu sẵn phía trước` : "–";
  const err = $("error");
  err.hidden = !st.lastError;
  err.textContent = st.lastError ?? "";
}

async function loadVoices(): Promise<void> {
  const r = await send<VoicesList>({ op: "voices.list" });
  if (!r.ok) return;
  const sel = $<HTMLSelectElement>("voice");
  for (const v of r.data.voices) {
    const o = document.createElement("option");
    o.value = v.id;
    o.textContent = `${v.name}${v.kind === "user" ? " (của bạn)" : ""} — ${[v.gender === "female" ? "Nữ" : v.gender === "male" ? "Nam" : "", v.region].filter(Boolean).join(" · ")}`;
    sel.append(o);
  }
  sel.value = (title && settings.voiceByTitle[title]) || settings.defaultVoice || "";
  sel.addEventListener("change", async () => {
    if (!title) return void save({ defaultVoice: sel.value || null });
    // re-read: another popup/options page may have changed the map since this one loaded
    const { voiceByTitle } = await chrome.storage.local.get({ voiceByTitle: {} }) as Pick<Settings, "voiceByTitle">;
    const map = { ...voiceByTitle };
    if (sel.value) map[title] = sel.value; else delete map[title];
    void save({ voiceByTitle: map });
  });
}

async function main(): Promise<void> {
  settings = (await chrome.storage.local.get(DEFAULT_SETTINGS)) as Settings;
  // In the real toolbar popup the active tab is the page; opened as a regular tab (tests), fall back to the
  // most recent chophim watch tab.
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = active?.url?.startsWith("https://chophim.lol/xem/") ? active
    : (await chrome.tabs.query({ url: "https://chophim.lol/xem/*" })).at(-1);
  if (tab?.id !== undefined) {
    tabId = tab.id;
    title = /\/xem\/([^/?#]+)/.exec(tab.url ?? "")?.[1] ?? null;
  }
  const enabled = $<HTMLInputElement>("enabled");
  enabled.checked = settings.enabled;
  enabled.addEventListener("change", () => void save({ enabled: enabled.checked }));
  const block = $<HTMLInputElement>("blockAds");
  block.checked = settings.blockAds;
  block.addEventListener("change", () => {
    if (block.checked && !confirm("Chặn quảng cáo có thể trái điều khoản của chophim và làm site mất nguồn thu. Vẫn bật?")) {
      block.checked = false;
      return;
    }
    void save({ blockAds: block.checked });
  });
  bindRange("dubVolume", (v) => `${Math.round(v * 100)}%`);
  bindRange("duck", (v) => `${Math.round(v * 100)}%`);
  bindRange("offsetMs", (v) => `${v > 0 ? "+" : ""}${v} ms`);

  $("subUrlBtn").addEventListener("click", async () => {
    const r = await tabMessage<{ error: string | null }>({ op: "tab.manualSubtitle", url: $<HTMLInputElement>("subUrl").value });
    alert(r?.error ? `Không dùng được phụ đề: ${r.error}` : "Đã nạp phụ đề.");
  });
  $<HTMLInputElement>("subFile").addEventListener("change", async (e) => {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) return alert("File quá lớn (> 2 MB).");
    const r = await tabMessage<{ error: string | null }>({ op: "tab.manualSubtitle", text: await f.text(), name: f.name });
    alert(r?.error ? `Không dùng được phụ đề: ${r.error}` : "Đã nạp phụ đề.");
  });
  $("openOptions").addEventListener("click", (e) => { e.preventDefault(); void chrome.runtime.openOptionsPage(); });

  await refresh();
  await loadVoices();
  setInterval(refresh, 1500);
}

void main();
