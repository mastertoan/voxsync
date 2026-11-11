# 02 — Extension core: phụ đề, đồng bộ, giảm âm gốc (Phase 2)

Ngày 2026-11-11. Code nằm trong `extension/`. Build bằng `npm run build`, sau đó load thư mục `extension/dist/` dưới dạng unpacked.

## Kiến trúc
- **Content script** (`src/content/index.ts`) chạy trên `chophim.lol/xem/*`:
  - **Bắt phụ đề:** dùng `PerformanceObserver` bắt URL `/api/storage/subtitles?file=…`, fetch lại cùng origin, parse VTT/SRT, rồi lọc ngôn ngữ (theo tên file trước, nội dung sau).
  - **Arm-gate:** chỉ bắt đầu lồng tiếng khi đủ hai điều kiện:
    - `duration > 300 s` và không có nút "Bỏ qua";
    - phụ đề thuộc media đang phát: hoặc bắt được sau lần `emptied` gần nhất, hoặc cùng (tập, server) với hash hiện tại. Trường hợp thứ hai xảy ra khi chuyển lại server cũ, vì player dùng lại VTT đã cache mà không fetch lại.
  - **Lập lịch:** trên `AudioContext`, đồng hồ chuẩn là `video.currentTime`, phát câu bằng `start(when − outputLatency − bias, offset)`.
  - **Giảm âm gốc:** chỉnh `video.volume`. Chỉ giảm khi có source thật sự đang phát (lead 100 ms, hold 300 ms).
  - **Nối lại server:** khi server offline hoặc trả `404 unknown_episode` thì PUT lại episode và playhead. Trong thời gian offline thì im lặng.
- **Service worker** (`src/sw/index.ts`): proxy với tập op cố định tới `http://127.0.0.1:8765`, đính kèm token. Đây cũng là nơi bật/tắt ruleset chặn quảng cáo.
- Các hàm tính toán thuần (`timing.ts`, `arm.ts`, `vtt.ts`, `subtitle-detect.ts`) có 34 unit test.

## E2E trên chophim thật
Môi trường: Chrome for Testing 150, extension build từ `dist/`, server thật, loa trong (`outputLatency` 16 ms).

Lấy user activation bằng một click thật lên **tiêu đề phim**, không click vào video hay link quảng cáo. Riêng phím Shift thì không đủ để player autoplay ở profile mới.

Cách đo: lấy `mediaTime` của frame đang hiển thị từ rVFC, so với mẫu âm đang ra loa theo `getOutputTimestamp`. Số đo này độc lập với clock mà scheduler dùng.

| Chỉ số | Kết quả | Ngưỡng plan |
|---|---|---|
| Lệch hình–tiếng ở đầu câu, 1× (135 frame) | **signed mean −2,9 ms**, p50 8,1, p95 **35,7**, max 41 ms | \|mean\| ≤ 5, p95 ≤ 60 ✅ |
| Vào giữa câu sau seek/resume (22 frame) | p50 33, p95 63 ms | p95 ≤ 120 ✅ |
| Tốc độ 1,25× / 1,5× (41 frame) | p50 17, p95 52 ms | — |
| Drift giữa câu | p95 32 ms, 0 lần resync | < 80 ms ✅ |
| Quảng cáo pre-roll 62 s (cùng `<video>`) | 0 câu được phát, volume giữ 1 | 0 ✅ |
| Đổi tập 1 → 2 (nút trong danh sách tập) | nhận VTT tập 2 (634 câu), arm lại ~8 s, phát đúng tập 2 | ✅ |
| Server "Vietsub (KK)" | `no-subtitle` sau 11 s, không giảm âm | ✅ |
| Quay lại "Song Ngữ" | arm lại sau 1 s (player không fetch lại VTT) | ✅ |
| Tab nền 40 s (`hidden`) | video chạy đủ 40 s, drift p95 3,4 ms, vẫn ở trạng thái armed | ✅ |
| User đổi volume sang 0,6 khi đang có thoại | lúc có thoại 0,18 (= 0,6 × 0,3), lúc lặng 0,6 | ✅ |
| Server tắt giữa chừng | `server-offline`, im lặng, volume trả về 1 | ✅ |
| Server bật lại | armed sau 2,3 s, **có tiếng lồng sau 2,5 s** | ≤ 5 s ✅ |

## Lỗi tìm thấy và đã sửa trong lúc chạy E2E
1. **Công thức đo debug sai:** trừ `outputLatency` hai lần và late-join dùng `offset × rate`. Bias thật chỉ khoảng −3 ms, nên **không cần hiệu chỉnh `biasMs`** (mặc định 0).
2. **Offset late-join ở tốc độ ≠ 1:** phải là `(độ trễ wall) × rate`, vì câu thoại phát 1× còn hình chạy theo rate. Đã thêm unit test.
3. **Volume do user đặt bị chia cho mức duck,** dẫn tới volume nhảy về 1. Sửa: lấy volume của user làm gốc rồi áp mức duck lên trên.
4. **Server offline nhưng vẫn phát câu đã decode, chiếm volume.** Sửa: khi offline thì không lập lịch, và chỉ thoát offline khi re-put thành công.
5. **Đổi server rồi quay lại thì kẹt `idle`** vì player không fetch lại VTT. Sửa: gắn phụ đề với (tập, server) trong hash.
6. **Server KK hiển thị `idle` thay vì `no-subtitle`.** Sửa: trạng thái này phụ thuộc phụ đề có thuộc media hiện tại hay không.
