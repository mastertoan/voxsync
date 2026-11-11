---
title: "Phase 0 gating spikes G1-G3: GO"
date: 2026-11-11
summary: "VieNeu Live Dubbing feasibility closed: ORT CPU fp32 backend, rVFC-measured A/V p95 41ms, seek TTFA <=0.69s"
---

# Phase 0 gating spikes G1-G3: GO

## Làm gì
Chạy 3 spike gating (G1 sync E2E trên chophim bằng Chrome for Testing 150 + extension MV3 unpacked; G2 chất lượng bằng proxy ASR mlx-whisper; G3 seek storm với synth ORT thật).

## Kết quả
- G1: activation qua keydown OK; SW→127.0.0.1 không bị LNA chặn; lệch hình–tiếng đo bằng rVFC p50 31 / p95 41 ms (cue-start), late-join p95 80 ms; pre-roll 0 cue; đổi tập + tab nền OK.
- G2: CER fp32 0.022, int8 0.032 (nhiễu), atempo 0.031 tốt hơn WSOLA 0.045; 0/29 cue hỏng.
- G3: TTFA synth p50 0.24–0.40 s, max 0.69 s, 0 stall.

## Đau đầu
- Đo lần đầu so onset với chính video.currentTime → tự chấm điểm; code-reviewer bắt được, đổi sang rVFC presented-frame.
- Duck bám theo cue đã lên lịch (3 s trước) → duck liên tục; `volumechange` async làm cờ selfVol vô dụng.
- `chrome.runtime.reload()` với --load-extension gỡ hẳn extension; click vào video trên chophim mở popup cờ bạc chiếm tab.
- int8 ONNX không load qua HF cache symlink trên ORT 1.31 (cần onnx_dir).

## Còn mở
AirPods/Bluetooth chưa đo; ABX người nghe cho int8; bài synth ≥10 phút thoại dày.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
