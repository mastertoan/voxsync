# Site probe: chophim.lol player (Phase 0)

Môi trường: Chromium (headless, omp managed) bằng Puppeteer/CDP, 2026-11-11. Chỉ quan sát, không tải hay lưu video, không vượt qua cơ chế bảo vệ nào.
URL: `https://chophim.lol/xem/vision-truy-tim-ban-nga#tap=tap-1&sv=Song+Ng%E1%BB%AF&channel=0&server_id=0`

## Bảng quan sát (VERIFIED, đọc trực tiếp bằng `evaluate`/`requests()` trong tab)

| # | Hạng mục | Quan sát |
|---|---|---|
| 1 | Origin top-level | `https://chophim.lol`. Next.js App Router (`/_next/static/chunks/*`, turbopack, `NEXT-ROUTE-ANNOUNCER`) |
| 2 | iframe | **Không có iframe nào**: `document.querySelectorAll('iframe')` trả về `[]`, `tab.frames()` chỉ có main frame. Điều này đúng với server "Song Ngữ" lẫn "Vietsub (KK)" |
| 3 | Vị trí `<video>` | Main frame, origin `https://chophim.lol`, **không nằm trong shadow DOM** (shadow host duy nhất là `NEXT-ROUTE-ANNOUNCER`). Chuỗi cha: `VIDEO.h-full w-full object-contain` < `DIV.relative h-full w-full overflow-hidden bg-black` < `DIV.absolute inset-0 w-full h-full` < `DIV.relative w-full aspect-video`. Lúc nào cũng chỉ có 1 `<video>` |
| 4 | Nguồn video | `src` = `blob:https://chophim.lol/<uuid>`, tức **MSE**. `crossOrigin=null` |
| 5 | Thư viện player | Không thấy global nào trong số `jwplayer, videojs, Hls, artplayer, Artplayer, Plyr, DPlayer, Clappr, shaka, dashjs, mpegts, flvjs`. Player là React component tự viết, được bundle vào chunk. Kiểu request XHR m3u8/ts gợi ý có hls.js nằm trong bundle (xem INFERENCE) |
| 6 | `<track>` / `textTracks` | `<track>` = 0, `video.textTracks.length` = 0. Kết quả giống nhau trên cả 2 server và sau khi đổi tập |
| 7 | Cách render phụ đề | **DOM overlay do React vẽ, không dùng native cue.** Container `DIV.pointer-events-none absolute left-3 right-3 z-[18] flex -translate-y-1/2 justify-center` (style `top: 82%`) > `DIV.max-w-[92%] whitespace-pre-line text-center font-semibold ...` (text-shadow inline) chứa text tiếng Việt, kèm dòng phụ `DIV.mt-1 opacity-90` (style `color: rgb(255,232,107); font-size: 21px`) chứa text tiếng Anh. Đây là chế độ song ngữ. Ví dụ quan sát được ở t≈92.9s: "Từ nỗi đau thương của mình,\nWanda đã tạo nên một Vision mới." / "Through her grief,\nWanda conjured a new Vision." |
| 8 | Request tải VTT tiếng Việt | `fetch` GET 200 `https://chophim.lol/api/storage/subtitles?file=vision-truy-tim-ban-nga-tap-1-1791955231891-cce2ae46-tap_01_vietnamese.vtt`, initiator là main frame (không có frame nào khác). Song song có `...tap_01_english.vtt` và `https://chophim.lol/api/storage/webvtt?file=589ad0ed4b9b45c7b5ce24c24c783e68.vtt`; file sau là **VTT thumbnail sprite** (cue chứa URL ảnh `p16-lp-sg.ibyteimg.com/...#xywh=`), không phải phụ đề |
| 9 | Nội dung VTT VI | `WEBVTT`, 54 658 ký tự, 760 cue, cue đầu `00:00:17.833 --> 00:00:18.917 "Tôi là ai?"`. Phụ đề tải bằng `fetch` từ JS app, tức là **có thể chặn hoặc đọc được ở tầng network** (webRequest URL pattern `*://chophim.lol/api/storage/subtitles*`) |
| 10 | Manifest / segment (server Song Ngữ) | Pre-roll: XHR `https://chophim.lol/public/m3u8/3c8eabe624134233a22569ee4fe896dc.m3u8` (VOD, `EXT-X-BYTERANGE`, segment trên `https://p16-lp-sg.ibyteimg.com/origin/tos-alisg-i-375lmtcpo0-sg/<hash>`). Phim chính: `POST https://chophim.lol/api/playback/grant/p2.stringdeptrai.<token>` (JSON) rồi `POST https://chophim.lol/api/playback/manifest/p3.stringdeptrai.<token>` (`application/octet-stream`, 69 741 B, manifest dạng nhị phân hoặc mã hóa nên không đọc trực tiếp). Segment phim: `https://node01.chophim-cdn.fun/<id>/<hash>.png` (206, đội lốt .png). Có thêm một GET 404 tới `/api/playback/manifest/p2...` trước đó |
| 11 | Manifest / segment (server Vietsub (KK)) | XHR `https://a.kvp726.com/20261016/NsQtuo2V/index.m3u8` → `.../3500kb/hls/index.m3u8` → `.../3500kb/hls/<id>.ts`. Origin CDN khác hẳn, nhưng video vẫn là blob MSE trên `chophim.lol` |
| 12 | Quảng cáo / pre-roll | Có **pre-roll video** dài 62.33s chạy ngay trên **cùng phần tử `<video>`**, nút "Bỏ qua" là `BUTTON` trong `DIV.pointer-events-none absolute bottom-12 right-2 z-10`. Hết pre-roll thì có `pause`, `ended`, rồi `emptied` và `src` đổi sang blob mới (phim, duration 3201.38s), sau đó `loadedmetadata, play, waiting, seeking(0.10), seeked, canplay, playing`. Ngoài ra trang có banner cờ bạc phủ lên vùng player và popup "789BET" (xem screenshot). Lần đổi tập và đổi server sau đó **không** có pre-roll |
| 13 | Tự động phát | Video tự phát khi load (headless, không cần click) |
| 14 | Đọc state | `currentTime`, `paused`, `duration`, `readyState` đọc được từ main world, không có giới hạn nào |
| 15 | Ghi volume/rate | `volume=0.3` và `playbackRate=1.25` đều được giữ nguyên sau 500ms (player không ghi đè lại). Restore về 1/1 thành công. Event bắn ra: `volumechange`, `ratechange` |
| 16 | Seek +30s | Gán `currentTime = 39.03+30`. Event: `seeking`(t=69.03) và `waiting` cùng ms, `seeked` **+19ms**, `canplay` và `playing` +20ms (buffer sẵn có). Sau 6s t=74.96, đang phát |
| 17 | Đổi tập (click "Tập 02" trong danh sách) | Hash đổi thành `#tap=tap-2&...`, **không reload document** (marker `window.__navMark` vẫn còn), **vẫn cùng phần tử `<video>`** (marker `__mark` vẫn còn). Chỉ `src` blob đổi. Event: `emptied, loadstart, durationchange, loadedmetadata, play, waiting, canplay, playing`. Phụ đề mới: `/api/storage/subtitles?file=vision-truy-tim-ban-nga-tap-2-1791955232932-b480e5af-tap_02_vietnamese.vtt`, tức pattern `<slug>-tap-<N>-<timestamp>-<hash8>-tap_<NN>_vietnamese.vtt` (timestamp/hash **không đoán được**, phải bắt từ network hoặc API). Thumbnail VTT mới: `webvtt?file=ccbcae3f...vtt` |
| 18 | Đổi hash thủ công (`location.hash = 'tap=tap-3...'`) | **Không có tác dụng.** Player không lắng nghe `hashchange`, tiêu đề vẫn là "Tập 02", không có event media nào. Hash chỉ là output của state trong app |
| 19 | Đổi server (`sv=`) | Danh sách server: "Song Ngữ" (8 tập) và "Vietsub (KK)" (7 tập). Click "Vietsub (KK)" làm hash thành `#tap=tap-02&sv=Vietsub+%28KK%29&channel=1&server_id=0` (lưu ý `tap-02` có số 0 đứng đầu). Vẫn **không có iframe, cùng `<video>` element, cùng origin `chophim.lol`**. Chỉ origin CDN media đổi (`a.kvp726.com`). Player tự seek về vị trí cũ (`seeking` ngay sau `loadedmetadata`) |
| 20 | Server Vietsub (KK) và phụ đề | Không có request `/api/storage/subtitles`, overlay `z-[18]` rỗng trong 10s theo dõi, `textTracks=0` |
| 21 | API phụ khác | `GET /api/skip-times?movie_slug=...&episode_slug=tap-N` (intro skip), `GET /api/backup-servers?...`, `POST /api/movie/views` |

## INFERENCE (chưa xác minh)
- Server "Vietsub (KK)" nhiều khả năng là **hardsub** (phụ đề burn vào hình), vì không có VTT nào và overlay trống. Chưa xác nhận bằng hình vì banner quảng cáo che player trong screenshot. Hệ quả: dubbing chỉ khả thi trên server có VTT tách rời ("Song Ngữ").
- Player dùng hls.js (hoặc fork) bundle trong chunk Next.js, không expose ra global. Lý do suy luận: m3u8/ts tải bằng XHR, rồi blob MSE.
- Manifest `p3.stringdeptrai` là manifest đã được obfuscate hoặc mã hóa, kèm token `grant`, tức site có cơ chế chống hotlink. Extension **không cần đụng tới** phần này: chỉ cần `<video>` + VTT.
- Vì `<video>` nằm ở main frame cùng origin `chophim.lol`, content script `world: "ISOLATED"` với `matches: ["https://chophim.lol/xem/*"]` đọc/ghi được `currentTime`/`volume` qua DOM, không cần `all_frames` hay MAIN world.
- Khi đổi tập/server, element giữ nguyên nhưng `src` đổi kèm `emptied`. Có thể dùng event `emptied`/`loadedmetadata` cùng request VTT mới làm tín hiệu reset cue. Pre-roll phát trên cùng element với duration ≈ 62s; dấu hiệu nhận biết: nút "Bỏ qua" hiện diện và/hoặc `duration` ngắn, nên cần tránh lồng tiếng lúc này.
- `currentTime` của phim bắt đầu từ 0 sau pre-roll và khớp mốc thời gian trong VTT (cue "Tôi là ai?" 17.8s). Không thấy offset, nhưng chưa đo độ lệch chính xác.

## Không quan sát được / giới hạn
- **Không kiểm tra Chrome stable với extension thật**: probe chạy trên Chromium managed qua CDP, không phải Chrome của user, không load MV3 extension.
- **Nội dung manifest `p3`** (octet-stream): không giải mã vì ngoài phạm vi và sẽ là vượt qua bảo vệ.
- **Hình ảnh khung video**: banner/popup cờ bạc phủ lên vùng player trong screenshot. Không click đóng popup để tránh trigger link quảng cáo, nên không xác nhận được bằng mắt hardsub của server KK hay vị trí phụ đề.
- **Âm thanh**: headless, không nghe được, không đo được A/V sync.
- **Initiator chi tiết** (call stack JS) của request VTT: `requests()` không trả `frameId`/initiator. Vì không có iframe nên initiator chắc chắn là main frame `chophim.lol`.
- **Pre-roll lần sau**: chỉ thấy ở lần load đầu, chưa biết có lặp lại theo phiên/cookie hay không.
- Chưa thử nút "next episode" trong control bar (chỉ dùng danh sách tập) và chưa thử trạng thái fullscreen. Overlay phụ đề nằm trong container `aspect-video`; nếu fullscreen áp lên container đó thì overlay của extension cũng phải được inject vào cùng container.
