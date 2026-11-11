---
title: "Phase 6: Lồng tiếng nhiều nhân vật + cảm xúc"
status: completed
---
# Phase 6: Lồng tiếng nhiều nhân vật + cảm xúc

User chọn: **A + B** (mặc định mỗi nhân vật một giọng có sẵn; tuỳ chọn clone giọng diễn viên), **chỉ tách 2–4 nhân vật chính**.

## Bằng chứng (spike 2026-11-11, `vision-truy-tim-ban-nga` tập 1)
- VTT không có tên người nói (0/760 câu); 44 câu có `- ` (2 người trong 1 câu); 48 thẻ âm thanh (`[thở dài]`, `[cười khẩy]`…).
- HLS `a.kvp726.com` không mã hoá: segment TS 8 s (h264+aac) tải 0,46 s, tức nhanh gấp khoảng 17 lần thời gian thực. Server tự tải được.
- Embedding người nói, ONNX qua `sherpa-onnx`, model 3D-Speaker ERes2Net (40 MB):
  - 163 câu xử lý trong 2,8 s; cảnh 2 người gom đúng 100%;
  - 4 nhóm lớn nhất phủ 85% số câu;
  - độ giống giữa câu và tâm nhóm của chính nó: p10 = 0,49; với tâm nhóm khác gần nhất: p90 = 0,43;
  - cùng một người ở hai nửa tập: 0,65–0,83; khác người: ≤ 0,57.
- WeSpeaker ResNet34 gộp tất cả thành 1 nhóm, nên loại.
- Cao độ (f0) trung vị theo nhóm: nam 107–108 Hz, nữ 172–222 Hz. *Sau đó bị loại; xem "Thay đổi".*
- Clone theo từng câu từ audio gốc: 0,61 s/câu (giọng có sẵn: 0,21 s/câu).
- VieNeu chỉ có 3 thẻ cảm xúc: `[cười]`, `[thở dài]`, `[hắng giọng]`.

## Thiết kế (đã triển khai)
- `server/voxsync_server/source.py`: playlist HLS master → variant; `align()` khớp timeline với `video.duration`; tải song song và decode AAC → PCM 16 kHz mono bằng ffmpeg; cache LRU 48 segment, tải trước 6 segment. Chỉ nhận `https:` tới địa chỉ public, có giới hạn kích thước.
- `server/voxsync_server/speakers.py`:
  - `Embedder`: ERes2Net (sherpa-onnx) + audeering giới tính (ORT); model pin theo sha256, tải lần đầu vào `~/.voxsync/models/speaker/`.
  - `Cast` theo từng phim (`~/.voxsync/cast/<title>/cast.json` + `lines.npy`):
    - gom cụm lại toàn bộ (average-linkage cosine 0,75, có ràng buộc giới tính); id ổn định theo centroid ≥ 0,8;
    - câu mới được gán tạm vào nhân vật gần nhất (≥ 0,5, cách nhân vật thứ hai ≥ 0,05);
    - top 4 của mỗi tập (≥ 3 câu) là nhân vật chính, có giọng riêng cố định theo giới tính; người khác dùng giọng chung nam/nữ.
- `Registry`:
  - episode có `mode` (`single` | `characters` | `clone`), `title`, nguồn âm thanh;
  - luồng listener đi trước playhead 30 câu; worker chỉ sinh tiếng cho câu đã được nghe;
  - nguồn lỗi 3 lần thì các câu chưa nghe dùng một giọng (`sourceError`).
- Clone: tham chiếu = 3 câu ≥ 1,2 s đầu tiên của nhân vật, ghép lại rồi cố định; cache key `clone:<sha256 ref>`; bắt buộc `cloneConsent`.
- Cảm xúc: mô tả âm thanh trong phụ đề → `[cười]` / `[thở dài]` / `[hắng giọng]`; mô tả khác bị bỏ; câu chỉ có mô tả âm thanh thì im lặng.
- API:
  - `PUT /episodes/{key}` thêm `mode`, `title`, `media`, `duration`, `cloneConsent`;
  - `GET /episodes/{key}/characters`;
  - `PUT /titles/{t}/characters/{id}` `{voice}`.
- Extension:
  - `streamLinks()` lấy link `.m3u8` theo tập từ dữ liệu trang; gửi `duration` của video;
  - setting `voiceMode`, `cloneConsent`;
  - popup: chọn chế độ (lần đầu bật clone phải xác nhận), danh sách nhân vật (giới tính, số câu, câu mẫu, chọn giọng);
  - playhead gửi mỗi 2 s.

## Thay đổi so với thiết kế ban đầu (theo số đo)
- **Nguồn âm thanh gốc:** playlist mà player tải là pre-roll quảng cáo (`/public/m3u8/…`, 25 × 2,5 s). Phim tải qua `/api/playback/manifest/…`, là token mờ chỉ player dùng được. Vì vậy dùng `link_m3u8` của server KK cùng tập, lấy từ dữ liệu trang.
  - KK dài hơn 34,8 s do một đoạn chèn ở 15:00.
  - `source.align` bỏ đúng nhóm discontinuity có độ dài bằng phần chênh lệch; còn lại trùng khớp.
  - Lệch đo bằng năng lượng giọng nói so với phụ đề: +0,10 / 0,00 / +0,05 s tại 3:20, 18:20, 40:00.
- **Giới tính:** median f0 (yin/pyin) đoán nam chính là "nữ", vì lúc kích động anh ấy nói ở 180–210 Hz. Đã thay bằng audeering wav2vec2 age/gender 6 layer (ONNX, CC BY-NC-SA): đúng 22/22 câu gán nhãn tay. P(nữ) dùng cả để ràng buộc khi gom cụm (hai câu khác giới chắc chắn thì không chung cụm).
- **Gom cụm:** gán online theo centroid làm cùng một người bị tách thành 40 cụm. Đã đổi sang gom cụm lại toàn bộ mỗi câu (≤ 200 câu, sau đó mỗi 10 câu), rồi ánh xạ id ổn định theo centroid (≥ 0,8).
- **Clone:** clone từng câu từ chính câu đó cho độ giống diễn viên 0,13–0,47. Ghép 3 câu đầu của nhân vật làm tham chiếu cho 0,44–0,60. Tham chiếu được cố định, nên mọi câu của nhân vật dùng chung một giọng.
- **Nhân vật chính:** chọn top 4 *theo từng tập*, vì giới hạn 4 cho cả phim khiến vai chính tập 2 không có giọng riêng.

## Success Criteria
- [x] Cảnh 2 người (2:55–4:40, tập 1, chạy thật trên Chrome): **20/22 câu (91 %)** đúng giọng của nhân vật, 0 câu sai giới tính.
- [x] Đổi giọng nhân vật nữ chính qua `<select>` trong popup: 25/25 câu của nhân vật đó có audio "Thùy Dung", 0 câu của nam chính bị đổi.
- [x] Sang tập 2: nam chính tập 1 (id 0, "Hải Đăng", 35 câu) được nhận lại với 21 câu, giữ nguyên giọng. 12 nhân vật có mặt ở cả 2 tập.
- [x] Clone: từ chối consent thì giữ chế độ cũ; đồng ý thì `clone`. Server cũng trả `400` khi thiếu `cloneConsent`. Trên 29 câu lồng: độ giống với tham chiếu của nhân vật đó là 0,50 (trung vị), với nhân vật khác 0,22; 90 % câu gần đúng nhân vật hơn.
- [x] Nguồn HLS lỗi (404): 5/5 câu vẫn có tiếng bằng một giọng, `sourceError` được báo, popup hiển thị lỗi.
- [x] Đồng bộ ở chế độ nhân vật: signed mean −6,4 ms, p95 12,4 ms (169 frame).
- [x] Chạy liên tục ở chế độ nhân vật, tập 2 (8 phút, media 12:30 → 20:00): 0 stall, 0 pause; video buffer 2 lần (mạng); đồng bộ signed mean −18,4 ms, p95 25,8 ms (679 frame); RSS server 4,51–4,59 GB. RSS tăng khoảng 1,9 GB so với một giọng, do model giới tính wav2vec2-large 6 layer.
