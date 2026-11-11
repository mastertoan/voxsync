# Changelog

## 0.1.0 — 2026-11-11
Bản MVP đầu tiên, chỉ hỗ trợ chophim.lol.

### Server (`server/`)
- Server TTS local chạy VieNeu-TTS v3 Turbo (ONNX CPU fp32), chỉ bind `127.0.0.1:8765`, có token xác thực.
- Sinh tiếng tuần tự cho cả tập phim, ưu tiên phần ngay sau playhead. Cache WAV lưu theo nội dung nên vẫn dùng được sau khi server khởi động lại.
- Câu thoại dài hơn khoảng trống trước câu sau được tăng tốc bằng `atempo`, tối đa 1,25×. Phần còn dư cho tràn sang câu sau, trễ quá 0,5 s thì bỏ câu đó.
- Giọng: 25 giọng có sẵn, nhân bản giọng (bắt buộc xác nhận có quyền dùng giọng), đổi tên, xoá, nghe thử.
- Khi ổ đĩa đầy: trả `507`, tạm dừng sinh tiếng, không tính là lỗi của câu thoại.

### Extension (`extension/`)
- Tự nhận diện phụ đề tiếng Việt (theo tên file, sau đó theo nội dung) và khớp đúng tập.
- Không lồng tiếng lên quảng cáo đầu phim; xử lý đúng khi đổi tập, đổi server và quay lại server cũ.
- Đồng bộ lời thoại theo `video.currentTime`, có bù độ trễ đầu ra của thiết bị phát.
- Tự giảm âm gốc khi có thoại, giữ đúng mức âm lượng người dùng đã chỉnh.
- Tự kết nối lại khi server khởi động lại; khi extension bị tắt hoặc cập nhật, âm lượng video được trả về như cũ.
- Popup: trạng thái, giọng theo từng phim, âm lượng, chỉnh lệch đồng bộ, nạp phụ đề thủ công, chặn quảng cáo (mặc định tắt).
- Trang cài đặt: quản lý giọng, nhân bản giọng từ file hoặc micro, chế độ debug xuất số đo đồng bộ.
