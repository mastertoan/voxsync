# VoxSync server

Server TTS chạy local cho extension VoxSync. Dùng VieNeu-TTS v3 Turbo, backend ONNX Runtime CPU fp32. Chỉ bind `127.0.0.1`.

## Yêu cầu
- macOS Apple Silicon, Python 3.12 qua `uv`.
- **`ffmpeg`** phải có trong `PATH` (`brew install ffmpeg`), vì bước căn thời lượng dùng `atempo`. Nếu thiếu, mọi cue cần stretch sẽ trả `410`.

## Chạy

```bash
cd server
uv sync
./scripts/voxsync-server        # = uv run voxsync-server; lần đầu tải model khoảng 610 MB vào ~/.voxsync/models
./scripts/install-launchagent   # tuỳ chọn: tự chạy khi đăng nhập (--uninstall để gỡ); log ở ~/.voxsync/server.log
```

Khi ổ đĩa đầy, server xoá bớt cache của chính nó, tạm dừng sinh tiếng 30 s và trả về `507 disk_full`. Lỗi này hiện lên trong popup của extension.

- Token nằm ở `~/.voxsync/token` (quyền `0600`). Dán token này vào phần Settings của extension.
- Mọi request phải có header `X-VoxSync-Token`.
- Biến môi trường:
  - `VOXSYNC_HOME` (mặc định `~/.voxsync`)
  - `VOXSYNC_PORT` (8765)
  - `VOXSYNC_THREADS` (6)
  - `VOXSYNC_CACHE_MAX_BYTES` (5 GB)

## Kiến trúc
- Một worker sinh tiếng duy nhất. Thứ tự là theo cue, bắt đầu từ playhead của tập được dùng gần nhất. Khi seek, worker chỉ dời con trỏ.
- Cache WAV PCM16 48 kHz đặt ở `~/.voxsync/cache/<k[:2]>/<k>.wav`.
  - Key gồm: text đã chuẩn hoá, giọng, revision model, `NORM_VERSION`, và tốc độ stretch nếu có.
  - Key **không** chứa episode, nên khi server restart cache vẫn dùng lại được.
- Episode chỉ lưu trong RAM. `PUT /episodes/{key}` là idempotent. Sau khi server restart, extension gửi lại `PUT` (nhận `404 unknown_episode` thì gửi lại).
- Chế độ nhiều giọng (`mode: characters | clone`):
  - Một luồng *listener* đi trước playhead tối đa 30 câu. Với mỗi câu, nó đọc âm thanh gốc qua `source.HlsSource` (tải trước 6 segment), tính embedding người nói và P(nữ), thêm câu vào cast của phim rồi gom cụm lại.
  - Worker chỉ sinh tiếng cho câu đã được nghe, nên mỗi câu được lồng đúng giọng ngay từ đầu.
  - Khi cast đổi (nhân vật mới, người dùng đổi giọng), các câu bị ảnh hưởng được gán lại giọng. Câu nào đã có trong cache thì chỉ cần đổi key.
  - Cast lưu ở `~/.voxsync/cast/<phim>/` (`cast.json` + `lines.npy`), ghi lại tối đa 10 s một lần.
  - Âm thanh gốc dùng cho clone lưu ở `~/.voxsync/refs/<phim>/`.
- Căn thời lượng (`stretch.py`):
  - Câu vừa khe thời gian thì giữ nguyên.
  - Câu dài hơn khe thì chạy `ffmpeg atempo`, tối đa 1,25×.
  - Nếu vẫn tràn, cue sau bị đẩy lùi; tổng độ trễ quá 0,5 s thì bỏ cue sau (`204`).
- Model được pin theo revision và được snapshot ra thư mục thật. Lý do: ORT 1.31 từ chối file external data nằm sau symlink của HF cache.

## Dung lượng cache
Đo trên tập 1 *Vision: Truy Tìm Bản Ngã* (716 cue, khoảng 33 phút lời thoại). Cache đầy đủ mất **189 MB**, tương đương **≈ 346 MB/giờ lời thoại** (WAV PCM16 48 kHz mono, khoảng 96 KB/s, tính cả bản stretch).
- Sinh xong cả tập trong **209 s**, RTF trung bình 0,14.
- Với giới hạn mặc định 5 GB, cache chứa khoảng 14 giờ lời thoại.
- Chuyển sang Opus (khoảng 24 kbps) sẽ giảm dung lượng khoảng 30×. Để Phase 5 làm nếu thấy cần.
- Bảng số đo đầy đủ: `docs/01-tts-poc.md`.

## Test

```bash
uv run --group dev pytest -q
```

Test dùng engine giả với độ dài audio tỉ lệ theo text. Cache, registry, route và auth đều chạy thật.
