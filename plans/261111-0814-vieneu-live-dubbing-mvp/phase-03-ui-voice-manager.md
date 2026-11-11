---
phase: 3
title: "Popup + quản lý giọng (F1)"
status: completed
priority: P1
effort: "1.5d"
dependencies: [1, 2]
---

# Phase 3: Popup + quản lý giọng

## Overview
Giao diện cho người dùng gồm hai phần:
- **Popup** (điều khiển nhanh khi đang xem);
- **trang Options** (quản lý giọng, cài đặt).

Viết bằng HTML/TS thuần, không React. Phase này làm song song được với Phase 4.

## Requirements
- **Popup:**
  - bật/tắt lồng tiếng;
  - trạng thái server (online/offline/RTF);
  - trạng thái phụ đề (`detected tập N / no-subtitle / chờ quảng cáo`);
  - chọn giọng cho phim hiện tại;
  - 2 thanh trượt: âm lượng tiếng lồng (GainNode) và mức duck;
  - offset đồng bộ ±500 ms (bước 10 ms);
  - nút "Dán URL / tải file phụ đề" (fallback F2);
  - tiến độ cache tập hiện tại.
- **Options → Giọng đọc:**
  - danh sách 25 preset + giọng user;
  - lọc theo giới tính/vùng/phong cách, tìm theo tên;
  - nghe thử (câu mẫu có thể sửa);
  - đặt giọng mặc định;
  - **giọng theo phim**: map `slug phim → voiceId`, ưu tiên hơn giọng mặc định.
- **Clone giọng:**
  - upload wav/mp3 3–15 s, hoặc ghi âm bằng `MediaRecorder` trên trang Options;
  - tên giọng;
  - checkbox bắt buộc "Tôi có quyền sử dụng giọng này";
  - đổi tên và xoá giọng user.
- **Options → Cài đặt:**
  - endpoint + token (dán token, nút "Kiểm tra kết nối");
  - giới hạn cache;
  - debug mode + nút export metric.
- Đổi giọng giữa phim: content script gửi `episode.put` với voice mới; server sinh lại từ playhead (đã có ở Phase 1).
- Settings lưu ở `chrome.storage.local`; content script lắng nghe `storage.onChanged`.
- Accessibility cơ bản: điều khiển được bằng bàn phím, có label, tương phản AA.

## Related Code Files
- Create: `extension/src/popup/{popup.html,popup.ts,popup.css}`
- Create: `extension/src/options/{options.html,options.ts,voices.ts,settings.ts,options.css}`
- Modify: `extension/manifest.json` (`action.default_popup`, `options_page`), `extension/src/sw/proxy.ts` (op `voices.*`, `settings.*`), `extension/src/content/index.ts` (GainNode, offset, chọn giọng theo slug, phụ đề thủ công)
- Create: `extension/test/voice-resolve.test.ts` (thứ tự ưu tiên: giọng theo phim > mặc định > `default_voice` của server)

## Implementation Steps
1. Mở rộng proxy SW: `voices.list/preview/create/rename/delete` (upload clip chuyển qua base64, giới hạn 10 MB).
2. Trang Options: danh sách, bộ lọc, preview (phát `audio/wav` qua `<audio>`), form clone kèm consent.
3. Popup: lấy trạng thái từ content script của tab đang active (`tabs.sendMessage`), các control ghi vào `storage`.
4. Content script áp settings ngay: gain, duck, offset (reschedule), đổi giọng (`episode.put`).
5. Phụ đề thủ công: dán URL (fetch qua SW, chỉ nhận `https:`, ≤ 2 MB) hoặc chọn file (`FileReader`). Parse dùng chung `vtt.ts`; thêm parser SRT (test).

## Success Criteria
- [x] Đổi giọng giữa đoạn thoại dày (popup/storage): câu đầu tiên bằng giọng mới phát sau **1,24 s**.
- [x] Nghe thử preset (server warm): **0,38–0,71 s**.
- [x] Clone clip 5 s: giọng `u_…` xuất hiện trong danh sách popup và dùng được để lồng tiếng. Không tick consent: UI chặn; server cũng trả `400 consent_required`.
- [x] Giọng theo phim: phim A dùng giọng clone, phim B (`bach-hoa-sat`) dùng "Trúc Ly", hai tab chạy song song. Sửa thêm: popup/options đọc lại `voiceByTitle` trước khi ghi, để hai trang mở cùng lúc không ghi đè gán giọng của nhau.
- [x] Offset +100 ms: rVFC signed mean dịch **+99,9 ms** (−18,1 → +81,8 ms; n = 67/71).
- [x] Server KK: `no-subtitle`, nạp `.srt` thì armed (760 câu) và có giảm âm khi thoại. URL `https:` nạp được; `http:` bị từ chối `https_only`.
- [x] Popup dùng bàn phím: Tab đi qua đủ 7 điều khiển, Space bật/tắt, mũi tên chỉnh slider, có focus ring 2px; 0 điều khiển thiếu label.

## Risk Assessment
| Rủi ro | Dấu hiệu | Phản ứng |
|---|---|---|
| Ghi âm bị chặn quyền mic trên trang extension | `getUserMedia` reject | Chỉ dùng upload file; ghi âm là phần phụ |
| Clone chất lượng kém với clip nhiều nhạc nền | Preview méo | Gợi ý dùng clip sạch; upstream đã có denoise mặc định |
