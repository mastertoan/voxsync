---
title: Phase 1 TTS server done
date: 2026-11-11
summary: "FastAPI VieNeu server: seek 0.66s, full ep 204s, 40 tests; review caught delay-leak, spin, retime-gap bugs"
---

# Phase 1 TTS server done

## Làm gì
Server `server/` (FastAPI, VieNeu 3.8.4 ORT fp32, cache WAV theo nội dung, worker tuần tự quay vòng, timeline căn thời lượng atempo, giọng preset + clone, token + Guard ASGI).

## Kết quả thật
Seek 600 s → 0.66 s; cả tập 716 cue 204.5 s (RTF 0.143); ≈346 MB/giờ thoại; restart + cache rời rạc → 200 ngay; clone không chặn event loop; LAN bị từ chối; 40 test, mutation-check các bug chính.

## Đau đầu
- vieneu 3.8.4 không truyền `codec_dir` → engine tải codec unpinned từ HF mỗi lần start. Bọc `OnnxV3LiteEngine.__init__`; chứng minh bằng ẩn HF cache + HF_HUB_OFFLINE.
- Review vòng 1: delay chain rò rỉ (gap không hấp thụ), file cache hỏng làm worker spin 39k lần/s, body cap bỏ qua chunked + chạy trước auth, token non-ASCII 500.
- Smoke lần đầu kẹt 598/716: worker chỉ sinh cue sau playhead.
- Review vòng 2: `_retime` dừng ở khoảng trống thứ 2 → cue đã cache kẹt 202 sau restart. Test cũ chỉ dùng cache đầy nên không lộ.
- Bài học: test restart phải có cache rời rạc; exception trong ASGI receive bị FastAPI nuốt thành 400 → đọc+cap body ở Guard rồi replay.

## Còn mở
Đổi giọng giữa phim tạo khoảng 202 (chưa phát tạm giọng cũ). Lock registry giữ khi sf.info lúc prime (rẻ).

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
