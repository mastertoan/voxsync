---
phase: 5
title: "Hardening + Bluetooth + phát hành nội bộ"
status: in-progress
priority: P1
effort: "1d"
dependencies: [1, 2, 3, 4]
---

# Phase 5: Hardening, Bluetooth, phát hành nội bộ

## Overview
Đóng các mục còn mở từ Phase 0 (AirPods, chạy dài, ABX int8), xử lý các lỗi vận hành, viết tài liệu cài đặt. Đây là MVP để dùng cá nhân trên M5 Max, chưa lên Chrome Web Store.

## Requirements
- **Bluetooth:**
  - đo rVFC bằng AirPods;
  - lưu `bias` theo thiết bị đầu ra (key = `ctx.outputLatency` làm tròn 10 ms cộng `sinkId` nếu có);
  - offset tay vẫn ưu tiên cao nhất.
- **Chạy dài:** 45 phút tập 1, thoại dày, server + Chrome cùng chạy. Ghi lại số stall, RSS server, bộ nhớ tab, CPU trung bình.
- **Lỗi vận hành (mỗi lỗi có hành vi cụ thể):**
  - server crash hoặc restart: extension tự nối lại, cache trên đĩa vẫn dùng được;
  - extension reload/update: content script cũ bị orphan, phải tự dừng audio, không để video bị duck mãi;
  - đầy đĩa: server trả 507 → popup báo lỗi, ngừng sinh tiếng;
  - file cache hỏng: WAV đọc lỗi thì xoá và sinh lại;
  - đổi thiết bị âm thanh giữa chừng (`ctx.onsinkchange` hoặc `outputLatency` thay đổi): reschedule;
  - nhiều tab chophim cùng lúc: chỉ tab đang phát có tiếng lồng (mỗi tab một episode key, server ưu tiên playhead mới nhất);
  - tab nền: giữ hành vi đã kiểm ở G1.
- **Khởi động server:** `server/scripts/voxsync-server` + LaunchAgent plist tuỳ chọn (`~/Library/LaunchAgents/dev.voxsync.server.plist`) để tự chạy khi đăng nhập.
- **ABX int8** (tuỳ chọn, do user nghe): nếu không phân biệt được thì thêm setting `precision` vào server.
- **Tài liệu:**
  - `README.md` (cài đặt, chạy, ghép token, giới hạn);
  - `docs/01-architecture.md` (cập nhật theo code thật);
  - `docs/10-performance-benchmarks.md` (số đo cuối);
  - `CHANGELOG.md`.

## Related Code Files
- Modify: `extension/src/content/{scheduler,index}.ts` (bias theo thiết bị, orphan guard, nhiều tab), `server/voxsync_server/{cache,worker,main}.py` (507, file hỏng)
- Create: `server/scripts/voxsync-server`, `server/launchd/dev.voxsync.server.plist`
- Create: `README.md`, `CHANGELOG.md`, `docs/01-architecture.md`, `docs/09-testing-strategy.md`, `docs/10-performance-benchmarks.md`

## Implementation Steps
1. Đo AirPods (debug mode), chỉnh `bias` theo thiết bị, đo lại.
2. Chạy 45 phút, ghi metric. Sửa các điểm nghẽn tìm được.
3. Cài đặt từng hành vi lỗi; mỗi lỗi kiểm thủ công một lần theo checklist ở mục Requirements, ghi kết quả vào `docs/09-testing-strategy.md`.
4. Script khởi động + LaunchAgent.
5. Viết tài liệu, dọn `spikes/`.

## Success Criteria
- [ ] AirPods sau khi bù: rVFC p95 ≤ 120 ms, |signed mean| ≤ 30 ms. **Chưa đo:** máy test không có AirPods. Code đã resync khi đổi thiết bị và có slider offset.
- [x] 45 phút (46,3 phút, giọng mới, cache trống, tập 3 tự sang tập 4): 0 stall, 0 pause ngoài ý muốn, RSS server 2640–2641 MB (dao động < 2 MB), duck/khôi phục âm lượng bình thường qua lúc đổi tập.
- [x] Checklist lỗi vận hành: server restart, extension reload/disable, đầy đĩa, cache hỏng, nhiều tab đều đã thử. Đổi thiết bị âm thanh mới có trong code, chưa đo bằng thiết bị thật. Chi tiết ở `docs/02-browser-integration.md`.
- [ ] Máy sạch (user khác trên macOS): làm theo README thì chạy được trong ≤ 15 phút (không tính tải model).
- [ ] Toàn bộ success criteria trong `plan.md` đã tick.

## Risk Assessment
| Rủi ro | Dấu hiệu | Phản ứng |
|---|---|---|
| Độ trễ AirPods dao động theo codec/kết nối | p95 > 120 ms dù đã bù | Ghi nhận giới hạn; giữ slider offset; khuyến nghị dùng loa hoặc tai nghe có dây cho trải nghiệm tốt nhất |
| macOS chặn LaunchAgent gọi Python trong venv | Server không tự chạy | Chạy tay bằng script; LaunchAgent chỉ là tuỳ chọn |
