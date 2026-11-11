---
title: "VieNeu Live Dubbing — Phase 0 feasibility + Go/No-Go"
description: "Nghiên cứu khả thi, benchmark backend trên M5 Max, quyết định kiến trúc, 3 spike gating trước Phase 1"
status: completed
priority: P1
effort: "1d research (xong) + ~1.5d spikes"
tags: [phase0, feasibility, tts, chrome-mv3, audio-sync]
created: 2026-11-11
---

# VieNeu Live Dubbing — Phase 0

## Overview
Phase 0 trả lời 3 câu hỏi rủi ro cao trước khi bắt tay vào code sản phẩm:
- backend inference cho VieNeu-TTS;
- đồng bộ audio với video;
- quyền điều khiển player từ Chrome extension.

Báo cáo chính nằm ở `docs/00-feasibility.md`. Bằng chứng gốc nằm ở `docs/phase0-evidence/` và `benchmarks/phase0/`.

## Verdict: **GO** (Bluetooth còn mở, không chặn)
| Câu hỏi | Quyết định | Bằng chứng |
|---|---|---|
| Backend | ORT CPU fp32, `threads=6`, 1 process. Đo được RTF 0,11–0,12, câu 150 ký tự mất 0,85 s. MPS chậm khoảng 2×, CoreML EP khoảng 2,2×, chưa có bản MLX | Đo thực, `docs/00-feasibility.md` §3 |
| Đồng bộ | Lấy `video.currentTime` làm đồng hồ chuẩn, lập lịch bằng `AudioBufferSourceNode.start(when)`, reschedule khi gặp `seeking/waiting/pause/ratechange/emptied`. Pre-generate tuần tự cả tập (RTF 0,107 trên VTT thật), không cần streaming | §5 |
| Extension | Trên chophim **không có iframe**: `<video>` nằm ở top frame, ghi được volume và rate. Dùng SW proxy tới localhost; bắt VTT bằng `PerformanceObserver`; duck bằng `video.volume` | §4, probe CDP |

G1–G3 đều pass, chi tiết ở `docs/00-feasibility.md` §6.1:
- **G1:** lệch hình–tiếng p95 41 ms (đầu câu) và 80 ms (vào giữa câu); 0 cue trong quảng cáo; đổi tập và tab nền OK.
- **G2:** 0% cue hỏng; chọn `atempo` thay vì WSOLA; giữ fp32.
- **G3:** synth xong sau seek ≤ 0,69 s; 0 stall.

## Phases
| # | Phase | Status |
|---|-------|--------|
| 1 | [Phase 0a: Research & benchmark](./phase-01-research-benchmark.md) | Completed |
| 2 | [Phase 0b: Gating spikes G1–G3](./phase-02-gating-spikes.md) | Completed |

## Success Criteria
- [x] Benchmark backend được đo thực trên M5 Max, có thể chạy lại
- [x] Cấu trúc player trên site đích được quan sát runtime
- [x] Kiến trúc MV3 có trích dẫn tài liệu chính thức
- [x] G1 sync spike pass (activation, LNA, sai số < 200 ms p95, không lồng tiếng lên quảng cáo); riêng Bluetooth chưa đo
- [x] G2 chất lượng pass (< 5% cue hỏng, stretch 1,25×). ABX int8 chờ người nghe, mặc định giữ fp32
- [x] G3 seek storm pass (≤ 1,5 s tới audio đầu tiên)

## Next
Lập plan đầy đủ cho Phase 1–5 (bộ docs 01–12, PLAN.md, ADR-001…008) dựa trên các quyết định đã chốt, rồi `/ak:cook`.

Phase 1 phải mang theo:
- **Sửa độ trễ dư +14 ms:** lập lịch tại `when − outputLatency − constBias`, đo lại trên loa trong tới khi signed mean ≈ 0;
- đo AirPods và thêm slider offset;
- bài chạy synth ≥ 10 phút với thoại dày;
- người nghe ABX int8 (tuỳ chọn).

3 tính năng bổ sung theo yêu cầu, khả thi đã kiểm (`docs/00-feasibility.md` §7b):
- **F1 Quản lý giọng:** dùng 25 preset + clone sẵn có của VieNeu; server có endpoint `/voices`; trang Options để nghe thử, đặt giọng mặc định và giọng theo phim.
- **F2 Tự nhận diện phụ đề:** `PerformanceObserver` + phân loại ngôn ngữ (token trong tên file, rồi nội dung) + ghép tập; khi không có VTT thì báo rõ hoặc cho tải file lên.
- **F3 Chặn quảng cáo pre-roll:** dùng `declarativeNetRequest` chặn `chophim.lol/public/m3u8/*` (đã test: không lỗi player) + ẩn banner bằng CSS. **Tắt mặc định**, có công tắc.

<!-- slug: vieneu-live-dubbing-phase0 -->
