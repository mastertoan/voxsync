# 01 — TTS server local (Phase 1 MVP)

Ngày 2026-11-11. Code nằm ở `server/`. Cách chạy xem `server/README.md`. Hợp đồng API xem `plans/261111-0814-vieneu-live-dubbing-mvp/plan.md`.

## Kết quả smoke với engine thật
Đo trên M5 Max, chạy `HF_HUB_OFFLINE=1`. Dữ liệu: VTT tập 1 *Vision: Truy Tìm Bản Ngã*, 760 cue thô, còn 716 cue sau khi normalize.

| Tiêu chí | Đo được | Ngưỡng |
|---|---|---|
| Seek tới 600 s (cache trống) → cue đầu tiên trả 200 | **0,66 s** | ≤ 1,5 s |
| RTF trung bình sau 60 s | 0,107 | ≤ 0,15 |
| Sinh xong cả tập (716 cue, cache trống) | **204,5 s**, RTF 0,143 | ≤ 8 phút |
| Dung lượng cache | 189 MB, **≈ 346 MB/giờ lời thoại** | ghi lại |
| Restart → `404 unknown_episode` → re-PUT | 0,07 s, 716/716 ready, cue trả về trong 0,9 ms, không sinh lại | dùng lại cache |
| Restart sau khi mới xem một phần (cache rời rạc, có khoảng trống) → re-PUT, seek 600 s | 20 cue liên tiếp từ cue 121 trả 200 ngay, tổng 31 ms | dùng lại ngay, không chờ lấp khoảng trống |
| Clone clip 2,5 s lúc worker đang sinh | 201 sau 1,3 s, `/health` vẫn ≤ 4 ms | không chặn event loop |
| Giọng clone còn sau restart | có | có |
| Gửi request không token, body chunked 3 MB | 401, không đọc body | 401 |
| Có token, chunked 3 MB | 413 | 413 |
| Truy cập qua IP LAN | connection refused | từ chối |
| Quyền `~/.voxsync`, file `token` | 700 / 600 | — |

Phân bố kết quả trên tập 1 (760 cue):

| Kết quả | Số cue |
|---|---|
| Phát nguyên bản | 548 |
| Phát sau khi stretch bằng `atempo` (≤ 1,25×) | 157 |
| `204` vì cue chỉ có SFX | 44 |
| `204` bị bỏ (trễ > 0,5 s) | 11, tức **1,5%** |

- 92 cue được phát trễ để tránh chồng lên câu trước. Độ trễ p50 là 0,12 s, lớn nhất 0,49 s.

## Quyết định và phát hiện trong lúc làm
- **vieneu 3.8.4 không truyền `codec_dir` xuống engine**, nên mỗi lần khởi động engine lại tải codec từ HF mà không pin revision. `engine.py` bọc `OnnxV3LiteEngine.__init__` để dùng snapshot đã pin. Đã kiểm: ẩn HF cache rồi chạy `HF_HUB_OFFLINE=1`, engine vẫn load được và clone vẫn chạy.
- **Timeline căn thời lượng** (sửa sau review):
  - độ trễ của một cue = thời điểm cue *được phát* trước đó kết thúc − `start`;
  - khoảng lặng giữa các cue hấp thụ độ trễ;
  - cue bị bỏ không làm lệch các cue phía sau;
  - extension đọc `X-Delay` của chính cue rồi phát tại `start + X-Delay`.
- **Worker:** sinh các cue phía trước playhead trước, sau đó quay lại các cue phía trước nữa. Nhờ vậy seek lùi trúng cache và cuối cùng cả tập đều có cache.
- **Bảo mật:** lớp ASGI `Guard` kiểm token *trước khi đọc body* và giới hạn số byte thật nhận được, nên chặn được cả upload chunked.
