---
phase: 3
title: "Popup + quản lý giọng (F1)"
status: pending
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
- Đổi giọng giữa phim: gửi `episode.put` với voice mới; server sinh lại từ playhead (đã có ở Phase 1).
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
- [ ] Từ popup đổi giọng giữa phim: câu tiếp theo (sau tối đa ~2 cue) dùng giọng mới, không trống tiếng quá 2 s.
- [ ] Nghe thử một preset: có tiếng trong < 2 s (server đã warm).
- [ ] Clone clip 5 s: giọng xuất hiện trong danh sách, dùng được ngay để lồng tiếng; thiếu consent thì không gửi được.
- [ ] Giọng theo phim: mở phim A rồi phim B, mỗi phim dùng đúng giọng đã gán.
- [ ] Offset +100 ms: rVFC signed mean dịch đúng khoảng +100 ms (±10).
- [ ] Phụ đề thủ công (`.srt` và URL) chạy được trên server KK nếu có file.
- [ ] Popup dùng được hoàn toàn bằng bàn phím.

## Risk Assessment
| Rủi ro | Dấu hiệu | Phản ứng |
|---|---|---|
| Ghi âm bị chặn quyền mic trên trang extension | `getUserMedia` reject | Chỉ dùng upload file; ghi âm là phần phụ |
| Clone chất lượng kém với clip nhiều nhạc nền | Preview méo | Gợi ý dùng clip sạch; upstream đã có denoise mặc định |
