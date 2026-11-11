# VieNeu Live Dubbing — Phase 0: khả thi Chrome MV3

Ngày: 2026-11-11. Chỉ nghiên cứu, không code/không cài gì vào repo.
Ký hiệu: **[V]** = đã xác minh (URL nguồn hoặc output quan sát). **[I]** = suy luận, cần test thực nghiệm.

## 0. Quan sát thực tế trang đích (curl, 2026-11-11)

- [V] Top frame `chophim.lol` là Next.js. Player render bằng `<iframe src={link_embed} className="w-full h-full border-0" allowFullScreen>` (chunk `/_next/static/chunks/08c848aec12a239d.js`). **Không có `sandbox`, không có `allow="autoplay"`, không có `allow="loopback-network"`.**
- [V] `link_embed` = `https://player.phimapi.com/player/?url=https://a.kvp726.com/.../index.m3u8` → iframe **cross-origin**.
- [V] Player frame load `jwplayer.js` 8.38.3 + `provider.hlsjs.js` (ssl.p.jwpcdn.com) + bundle riêng `/player/assets/index-*.js`. CSS ẩn `.jw-icon-cc` (họ tắt caption gốc).
- [V] `index.m3u8` trả `access-control-allow-origin: *` (`curl -I -H 'Origin: https://player.phimapi.com'`). Segment chưa kiểm tra.
- [I] JW + hls.js ⇒ `<video>` thật dùng MSE (`src=blob:`), không phải canvas/WebCodecs.

---

## Q1. Content script trong iframe player cross-origin — **Có**

**Cơ chế**
- `content_scripts` với `all_frames: true`: mỗi frame được kiểm tra URL độc lập; script inject vào frame con khớp `matches`. [V] https://developer.chrome.com/docs/extensions/reference/manifest/content-scripts
- Frame `about:blank`/`srcdoc`/`data:`/`blob:`/`filesystem:` không khớp URL trực tiếp → cần `match_origin_as_fallback: true` (inject theo origin của frame tạo ra nó; bắt buộc path `*`; ưu tiên hơn `match_about_blank`). [V] https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts#injecting-in-related-frames
- Với trang đích: chỉ cần `matches: ["https://chophim.lol/*", "https://player.phimapi.com/*"]` (+ host tương ứng), không cần `<all_urls>`. iframe là URL https thường → không cần fallback; vẫn nên bật fallback để phòng player tự tạo iframe blob/about:blank. [V cấu trúc / I hành vi tương lai]
- Isolated world dùng chung **DOM** với trang, chỉ tách JS heap ⇒ đọc/ghi `video.currentTime/paused/playbackRate/volume/muted` và `addEventListener('play'|'pause'|'seeking'|'seeked'|'waiting'|'playing'|'ratechange'|'timeupdate'|'ended'|'emptied'|'loadedmetadata')` hoạt động bình thường. [V] https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts#isolated_world

**Giới hạn**
| Trường hợp | Kết luận |
|---|---|
| Closed shadow DOM | Dùng `chrome.dom.openOrClosedShadowRoot(el)` (Chrome 88+) trong content script. [V] https://developer.chrome.com/docs/extensions/reference/api/dom |
| Iframe lồng nhiều tầng | `all_frames` áp cho mọi tầng nếu URL khớp; mỗi tầng một instance script. [V doc] |
| Iframe `sandbox` | URL https vẫn match theo URL; origin opaque. [I] cần test việc inject khi thiếu `allow-scripts`. Không áp dụng cho trang đích (không sandbox). |
| Canvas/WebCodecs player | Không có `HTMLMediaElement` ⇒ không có `currentTime`/event. Chỉ còn đường API JS của player (MAIN world). Trang đích không thuộc loại này [I]. |
| `<video>` tạo trong closed shadow / tạo muộn | Cần `MutationObserver` + quét shadow root. [I] |

**`world: 'MAIN'`**: chia sẻ JS heap với trang ⇒ truy cập global như `jwplayer()`, `videojs`, `Hls`, `Artplayer`. Với JW 8: `jwplayer().getPosition()`, `.setVolume()`, `.on('playlistItem'|'seek'|'buffer'|'time')`. Đổi lại: không có `chrome.runtime`, trang có thể can thiệp/giả mạo. [V] cảnh báo trong manifest docs ở trên; [I] tên API JW (theo JW docs, chưa chạy thử). Khuyến nghị: ISOLATED làm chính; MAIN chỉ là shim nhỏ khi cần API JW (ví dụ volume ăn khớp UI), nói chuyện với isolated qua `window.postMessage`/`CustomEvent` trong cùng frame.

**Giao tiếp giữa frame**
- Content script → SW: `chrome.runtime.sendMessage`; SW nhận `sender.tab.id`, `sender.frameId`, `sender.documentId`, `sender.origin`. SW → frame cụ thể: `chrome.tabs.sendMessage(tabId, msg, {frameId})` hoặc `{documentId}`. Port dài hạn: `chrome.runtime.connect`. [V] https://developer.chrome.com/docs/extensions/develop/concepts/messaging
- [V] **Chrome serialize message bằng JSON** (không structured clone), max 64 MiB ⇒ **ArrayBuffer không truyền thẳng được**, phải base64 hoặc tránh truyền (xem Q3/Q4). Cùng nguồn trên.
- `window.postMessage` giữa top ↔ iframe cross-origin hoạt động và hỗ trợ transfer ArrayBuffer, nhưng trang nhìn thấy/giả mạo được ⇒ chỉ dùng cho dữ liệu không nhạy cảm, luôn kiểm `event.origin` + nonce. [I]

**Rủi ro cần test**: player.phimapi.com có CSP/`document_start` race tạo `<video>` trước script? JW có bọc video trong shadow DOM không (JW 8 thường không) — [I].

---

## Q2. Giảm âm gốc (ducking) — **Có** (khuyến nghị `video.volume`)

**(a) `video.volume` từ content script — Có, không hạn chế**
- [V] `volume` là thuộc tính DOM ghi được 0..1; isolated world ghi trực tiếp lên element dùng chung. Không có yêu cầu user activation cho đổi volume (autoplay policy chỉ chặn `play()` có tiếng). Nguồn: https://html.spec.whatwg.org/multipage/media.html#dom-media-volume
- Player "đánh lại": [I] JW lắng nghe `volumechange` để cập nhật slider, và tự set lại `video.volume` khi user kéo slider/đổi item hoặc khôi phục từ `localStorage` (`jwplayer.volume`) lúc load item mới. ⇒ Ghi đè trực tiếp có thể bị JW reset khi đổi tập, và slider JW lệch với âm thực.
- Cách giảm xung đột: (1) lưu "user volume" khi user chỉnh, áp hệ số duck `video.volume = userVol * duck`; theo dõi `volumechange` do player gây ra để cập nhật `userVol`, phân biệt bằng cờ "đang tự set". (2) Nếu JW chống trả: MAIN-world shim gọi `jwplayer().setVolume()`. [I]

**(b) `createMediaElementSource(video)` — Có điều kiện, không khuyến nghị**
- [V] Spec: node **phải xuất silence** nếu fetch của media element gắn nhãn resource là CORS-cross-origin. https://webaudio.github.io/web-audio-api/#MediaElementAudioSourceNode-security
- MSE (`src=blob:` từ `URL.createObjectURL(MediaSource)`): dữ liệu được đưa vào bằng `appendBuffer` từ fetch CORS của hls.js, không qua fetch của media element ⇒ không bị taint. [I] (m3u8 có ACAO `*` [V]; segment chưa kiểm).
- Video `src` trực tiếp file mp4/HLS native cross-origin không có `crossorigin` + ACAO ⇒ ra zero. [V spec]
- [V] Sau khi gọi, âm của element bị **reroute hoàn toàn** vào graph AudioContext. https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/createMediaElementSource
- Rủi ro [I]: context `suspended` (chưa có activation trong frame) ⇒ video câm; `close()`/script reload (extension update làm content script cũ mất kết nối) ⇒ video câm tới khi reload trang; mỗi element chỉ tạo được **một** MediaElementSourceNode (lần 2 ném `InvalidStateError`) ⇒ không phục hồi được sau khi script bị orphan. Quá xâm lấn so với lợi ích.

**(c) `chrome.tabCapture` + offscreen — Có điều kiện, không khuyến nghị**
- [V] Chỉ gọi được sau khi user *invoke* extension (bấm action, tương tự `activeTab`). https://developer.chrome.com/docs/extensions/reference/api/tabCapture
- [V] Khi tab bị capture, **âm tab ngừng phát ra loa**; phải tự phát lại qua `AudioContext.createMediaStreamSource(stream)` → `destination`. Cùng nguồn.
- [V] `getMediaStreamId()` trả ID dùng 1 lần, hết hạn sau vài giây; từ Chrome 116 ID lấy ở SW dùng được trong offscreen document (cùng origin + render process). `targetTabId` phải là tab đã có `activeTab`. Cùng nguồn.
- [V] Capture giữ qua điều hướng trong tab, dừng khi tab đóng/stream đóng (`capture()`). Offscreen cần reason `USER_MEDIA`. https://developer.chrome.com/docs/extensions/reference/api/offscreen
- Hệ quả [I]: bắt toàn bộ audio tab (cả quảng cáo, TTS của chính mình nếu phát trong tab), thêm độ trễ vòng capture→replay (ước lượng hàng chục ms, + `outputLatency`), hiện chỉ báo capture, phải bấm lại mỗi phiên.

**Khuyến nghị (xếp hạng)**
1. **`video.volume` × hệ số duck từ content script isolated trong frame player** — xâm lấn thấp nhất, không phụ thuộc CORS, thất bại an toàn (tệ nhất là âm gốc to).
2. MAIN-world `jwplayer().setVolume()` nếu JW reset volume.
3. `createMediaElementSource` chỉ khi cần lọc tần số (ví dụ giảm dải giọng nói thay vì giảm cả track) — chấp nhận rủi ro câm.
4. tabCapture — loại.

---

## Q3. Phát audio và lập lịch ở đâu — **Content script trong frame player** (Có điều kiện: cần user activation trong frame đó)

**So sánh**
| Vị trí | Ưu | Nhược |
|---|---|---|
| Content script, frame chứa `<video>` | Cùng thread/clock với `video.currentTime`, phản ứng `seeking/pause/ratechange` tức thì, không hop IPC | Tuân autoplay policy của document đó |
| Offscreen doc (`AUDIO_PLAYBACK`) | Autoplay được (frame top-level là extension page) | Khác process với video ⇒ mọi tín hiệu sync đi qua IPC (SW relay, vì offscreen chỉ có `chrome.runtime`); tự đóng sau 30s không có âm |
| Service worker | — | Không có `AudioContext`/DOM; bị suspend khi idle. Loại. |

**Autoplay**
- [V] AudioContext tạo trước user gesture sẽ ở `suspended`; cần `resume()` sau gesture. https://developer.chrome.com/blog/autoplay
- [V] Iframe cross-origin chỉ được autoplay khi top frame delegate `allow="autoplay"`; trang đích **không** có attribute này (quan sát §0).
- [I] Content script chạy trong document của trang ⇒ dùng **user activation của chính frame đó**; click vào action của extension **không** tạo activation cho trang. Nhưng user click nút Play của JW **bên trong iframe** ⇒ iframe có sticky activation ⇒ `audioCtx.resume()` trong content script frame đó thành công. Cách làm: tạo context lười, `resume()` trong handler `play`/`pointerdown` (capture phase) của frame player. Cần test: phím tắt/autoplay của JW khi không có click.
- [V] Offscreen doc: Chromium force-allow autoplay khi frame ngoài cùng là extension (`kAutoplayFlagForceAllow`). https://chromium.googlesource.com/chromium/src/+/main/extensions/browser/extension_web_contents_observer.cc
- [V] Offscreen `AUDIO_PLAYBACK`: đóng sau 30s không audible; timer 30s đặt ngay khi tạo, reset khi audible. https://developer.chrome.com/docs/extensions/reference/api/offscreen ; https://chromium.googlesource.com/chromium/src/+/main/extensions/browser/api/offscreen/audio_lifetime_enforcer.cc. [I] "audible" = chỉ báo loa của Chrome ⇒ phát silence không tính; khoảng lặng giữa câu thoại >30s sẽ giết offscreen.

**Clock**
- `video.currentTime`: [V] Chromium dùng audio clock làm nguồn cho `currentTime`; `rVFC.mediaTime` = PTS của frame đã present. https://web.dev/articles/requestvideoframecallback-rvfc
- `requestVideoFrameCallback`: [V] chạy theo nhịp present (min(fps, refresh)), có thể trễ 1 vsync. https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback. [I] Gắn với rendering loop ⇒ **không fire ở tab nền** (như rAF). Dùng làm tinh chỉnh khi foreground, không làm nguồn duy nhất.
- `AudioContext.currentTime` + `getOutputTimestamp()` (Chrome 57+) quy đổi context time ↔ `performance.now()`; spec khuyên dùng `outputLatency` (Chrome 102+) thay vì tự suy; giá trị có thể đổi khi đổi thiết bị ⇒ đọc thường xuyên. [V] https://webaudio.github.io/web-audio-api/#dom-audiocontext-getoutputtimestamp ; mdn/browser-compat-data `api/AudioContext.json`.
- Thuật toán gợi ý [I]: mỗi tick, ánh xạ `mediaT = video.currentTime` ↔ `ctxT = audioCtx.currentTime`; cue bắt đầu ở `mediaStart` ⇒ `source.start(ctxT + (mediaStart - mediaT)/playbackRate - outputLatency_TTS + outputLatency_video_bù)`. Vì video và TTS cùng ra một thiết bị, độ trễ output gần như triệt tiêu nhau; chỉ cần bù khi khác đường. Nếu `|drift| > ~80ms` hoặc `seeking/ratechange/pause` ⇒ stop & reschedule; `playbackRate≠1` ⇒ đặt `source.playbackRate` hoặc bỏ qua cue.
- macOS + Bluetooth: [I] `outputLatency` AirPods thường 150–250ms, built-in ~10–30ms; video element cũng chịu cùng độ trễ output (Chromium audio renderer có bù A/V sync cho video) ⇒ nếu cả hai qua cùng sink, lệch tương đối nhỏ. Cần đo: log `baseLatency`, `outputLatency` trên M5 Max với loa trong + AirPods.

**Tab nền**
- [V] Timer ở tab nền bị gom ≤1 lần/giây; **trang đang phát âm thanh audible được miễn throttling**; rAF không chạy ở nền. https://developer.chrome.com/blog/background_tabs
- [I] AudioContext tiếp tục render ở nền (rendering thread riêng); `source.start(when)` đã lập lịch vẫn chính xác. Vì video đang phát có tiếng (dù đã duck >0) ⇒ tab audible ⇒ timer không bị throttle. Rủi ro: nếu duck về `volume=0`/`muted` và TTS đang im ⇒ tab không audible ⇒ timer 1Hz ⇒ lập lịch trước ≥2s (lookahead) bằng `AudioBufferSourceNode.start(when)` để miễn nhiễm.

**Kết luận Q3**: AudioContext + scheduler trong content script isolated ở frame player; SW chỉ làm proxy mạng/điều phối; offscreen là phương án dự phòng nếu không lấy được activation trong iframe (chấp nhận sync qua IPC và luật 30s).

---

## Q4. Truy cập localhost — **Có** (qua SW/offscreen); trực tiếp từ content script: **Có điều kiện**

**Sự thật**
- [V] Từ Chrome 85, fetch cross-origin của content script mang `Origin` của trang và chịu CORS như trang; Chrome 87 bỏ allowlist. https://www.chromium.org/Home/chromium-security/extension-content-script-fetches/
- [V] "Cross-origin requests are always treated as such in content scripts, even if the extension has host permissions"; SW/extension page bỏ qua CORS nếu có `host_permissions`; quyền tách theo scheme (`http://127.0.0.1/*` riêng). https://developer.chrome.com/docs/extensions/develop/concepts/network-requests
- [V] LNA ship mặc định Chrome 142: request public → loopback cần permission; Chrome 145 tách `local-network` / `loopback-network`; Chrome 147 thêm WebSocket/WebTransport; trong iframe cần top frame delegate `allow="loopback-network"`, cả hai phải secure context. https://chromestatus.com/feature/5152728072060928 ; https://developer.chrome.com/blog/local-network-access ; LNA adoption guide https://docs.google.com/document/d/1QQkqehw8umtAgz5z0um7THx-aoU251p705FbIQjDuGs
- [V] Adoption guide (cập nhật 2026-05-18): *"We do not currently have plans to apply LNA restrictions to extensions. Currently, extensions that have the necessary host permissions are allowed to make local network requests."* ⇒ fetch từ **SW/offscreen** tới `http://127.0.0.1:PORT` với `host_permissions: ["http://127.0.0.1/*"]` không bị LNA prompt, không cần CORS từ server.
- PNA (preflight) đã bị thay thế, không còn liên quan. [V] cùng blog.

**Content script fetch trực tiếp** [I, cần test]
- Request được coi là của origin trang (`player.phimapi.com`) ⇒ là LNA request public→loopback ⇒ (1) cần prompt `loopback-network` gán cho origin top frame; (2) iframe player **không** có `allow="loopback-network"` ⇒ gần như chắc bị chặn; (3) server localhost phải trả `Access-Control-Allow-Origin`.
- Mixed content: `http://127.0.0.1`/`localhost` là "potentially trustworthy" nên không bị chặn như mixed content [V] https://w3c.github.io/webappsec-secure-contexts/#is-origin-trustworthy ; LNA cũng miễn mixed content cho loopback literal [V blog]. Nhưng LNA ở trên mới là chốt chặn.

**Routing khuyến nghị**
1. Content script gửi `{op:'tts', cueId, text}` (không gửi URL tùy ý — tránh open proxy) → SW fetch `http://127.0.0.1:PORT/...` → trả audio về.
2. Vì messaging JSON: trả **base64** (clip TTS vài chục–vài trăm KB, xa dưới 64 MiB) rồi `decodeAudioData` ở content script. Chi phí base64 ~33% + encode/decode, chấp nhận được. [V giới hạn; I hiệu năng]
3. Prefetch theo cửa sổ (ví dụ 30–60s phía trước theo VTT) để mạng/TTS không nằm trên critical path.
4. Thay thế nếu base64 thành nút cổ chai [I]: iframe `chrome-extension://…/bridge.html` (web_accessible_resources) chèn ẩn vào frame player — origin extension nên fetch localhost được — rồi `postMessage` transfer ArrayBuffer cho content script. Phức tạp hơn, để sau.
- SW suspend sau ~30s idle: mỗi message/fetch đánh thức lại; giữ `runtime.connect` port trong lúc phát. [I]

---

## Q5. Đổi tập trong SPA — **Có**

**Tín hiệu (theo thứ tự tin cậy)**
1. **Iframe player điều hướng lại**: [V] React render `<iframe src={link_embed}>`; đổi tập ⇒ `src` mới (m3u8 khác) ⇒ document mới ⇒ content script inject lại từ đầu (instance cũ chết theo document). Đây là ranh giới reset tự nhiên. [I] nếu React giữ nguyên iframe và chỉ đổi src thì vẫn là navigation mới.
2. **Top frame**: `hashchange` (URL chứa `#tap=tap-1&sv=…&server_id=0`) [V URL]; dự phòng Navigation API `navigation.addEventListener('currententrychange')` hoặc theo dõi `src` của iframe bằng `MutationObserver(attributeFilter:['src'])`. Top frame suy ra slug + tập ⇒ lấy URL VTT mới.
3. **Trong frame player**: `emptied` → `loadedmetadata`/`durationchange` mới, `video.currentSrc` (blob mới) đổi; JW `playlistItem` (MAIN world). Dùng để huỷ toàn bộ source đã lập lịch, xoá cache cue.
- Khóa phiên: `episodeKey = slug + tap + server` (top frame) gắn với `documentId` của frame player (SW biết qua `sender.documentId`) ⇒ SW bỏ qua message từ document cũ.

**Rủi ro**: cách lấy URL VTT theo tập — tên file có timestamp + UUID (`…-1791955231891-cce2ae46-tap_01_vietnamese.vtt`) ⇒ không đoán được, phải đọc từ API/JSON của chophim (fetch từ top frame cùng origin hoặc SW; ACAO `*` [V theo brief]). Cần xác định endpoint liệt kê subtitle theo tập.

---

## Kiến trúc tối thiểu đề xuất

```
top frame chophim.lol (CS isolated): theo dõi hash/iframe src → episodeKey + URL VTT
player frame player.phimapi.com (CS isolated): <video> events, AudioContext, scheduler, ducking video.volume
SW: proxy 127.0.0.1 (op cố định, trả base64), relay top↔player theo frameId/documentId
(tùy chọn) MAIN shim ở player frame: jwplayer API nếu JW reset volume
```
Manifest: `content_scripts[{matches:[chophim, player.phimapi.com], all_frames:true, match_origin_as_fallback:true, run_at:'document_start'}]`, `host_permissions: ["http://127.0.0.1/*", "https://chophim.lol/*"]`. Không cần `tabCapture`, `offscreen` ở bản đầu.

## Rủi ro tồn đọng cần test thực nghiệm (xếp theo mức chặn)
1. `audioCtx.resume()` trong content script iframe thành công sau click Play của JW (không có `allow="autoplay"`).
2. JW có reset `video.volume` khi seek/đổi item/kéo slider không.
3. Content script fetch trực tiếp `127.0.0.1` từ iframe bị LNA chặn (xác nhận để chốt routing qua SW).
4. Đo `outputLatency` + lệch A/V thực tế: loa trong vs AirPods trên M5 Max; drift sau 20 phút.
5. Tab nền khi video `volume` nhỏ: timer/scheduler còn đúng; rVFC dừng.
6. Segment HLS có ACAO (chỉ quan trọng nếu dùng `createMediaElementSource`).
7. API/endpoint liệt kê VTT theo tập.

## Giới hạn nghiên cứu
- Không chạy Chrome thật; mọi hành vi runtime (autoplay iframe, JW volume, LNA với content script, latency) là [I].
- Chưa đọc mã Chromium cho LNA-exemption extension (dựa vào adoption guide chính thức của Chrome team).
- Không đánh giá Firefox/Safari; không đụng DRM (luồng là HLS không mã hoá theo quan sát m3u8 — chưa kiểm `#EXT-X-KEY`).

## Câu hỏi chưa giải quyết
- Endpoint subtitle theo tập của chophim?
- Server TTS localhost trả định dạng gì (WAV/Opus) — ảnh hưởng kích thước base64 và `decodeAudioData`.
- Có cần hỗ trợ `playbackRate ≠ 1` không (time-stretch TTS hay bỏ cue)?
