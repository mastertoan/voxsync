---
phase: 1
title: "Phase 0a: Research & benchmark"
status: completed
priority: P1
effort: "1d"
dependencies: []
---

# Phase 0a: Research & benchmark

## Overview
Thu thập dữ kiện đã kiểm chứng để ra quyết định Go/No-Go. Kết quả nằm ở `docs/00-feasibility.md`.

## Requirements
- Benchmark thực tế mọi backend khả dụng trên M5 Max, không lấy số từ README.
- Quan sát player của site đích ở runtime.
- Năng lực MV3 phải có trích dẫn nguồn chính thức.

## Related Code Files
- Create: `docs/00-feasibility.md`, `docs/phase0-evidence/site-probe.md`, `docs/phase0-evidence/mv3-research.md`
- Create: `benchmarks/phase0/bench.py`, `benchmarks/phase0/fit.py`

## Implementation Steps
1. [x] Clone VieNeu-TTS (commit `22dd2ef`, `vieneu 3.8.4`), đọc `v3turbo.py` và `onnx_runtime_lite.py` để biết đường đi của các backend.
2. [x] Benchmark ORT CPU fp32 (4/6/12 thread), int8, CoreML EP, PyTorch MPS và CPU; tìm bản MLX; chạy thử 2 process song song.
3. [x] Đo tỷ lệ độ dài tiếng ÷ độ dài cue trên 200 cue thật của tập 1.
4. [x] Probe site bằng CDP: iframe, `<video>`, VTT, quảng cáo, đổi tập, đổi server.
5. [x] Nghiên cứu MV3: iframe, ducking, AudioContext, LNA, messaging.
6. [x] Lấy review của advisor (kongming) và đưa các chỉnh sửa vào feasibility.

## Success Criteria
- [x] Bảng benchmark chạy lại được bằng lệnh trong §8 của feasibility
- [x] Mỗi claim được gắn nhãn [V] hoặc [I]
- [x] Có verdict và gating experiments

## Risk Assessment
Probe chạy trên Chromium headless, không phải Chrome stable có extension thật. Vì vậy activation, LNA và sai số đồng bộ vẫn là [I]. G1 sẽ kiểm chứng các điểm này.
