---
phase: 4
title: "Chặn quảng cáo (F3)"
status: completed
priority: P2
effort: "0.5d"
dependencies: [2]
---

# Phase 4: Chặn quảng cáo pre-roll + banner trên chophim

## Overview
Phase này chặn quảng cáo video mở đầu (pre-roll) và ẩn banner/popup đè lên player. Tính năng **tắt mặc định**, có công tắc trong popup.

Phase 0 đã kiểm: chặn `chophim.lol/public/m3u8/*` thì nút "Bỏ qua" hiện khoảng 5 s rồi phim tự phát, `video.error` = null, phụ đề vẫn tải bình thường.

## Requirements
- Static ruleset `declarativeNetRequest` (`rules/chophim-ads.json`), mặc định `enabled: false`; bật/tắt bằng `updateEnabledRulesets`.
- Rule chặn:
  - `||chophim.lol/public/m3u8/^` (resourceType `xmlhttprequest`, `media`);
  - segment pre-roll trên `p16-lp-sg.ibyteimg.com/origin/tos-alisg-i-375lmtcpo0*`, **chỉ khi initiator là chophim.lol**, vì CDN này còn chứa thumbnail sprite của phụ đề;
  - các domain popup/redirect đã thấy (`zo88.qpon`, các link `789bet`). Chặn `main_frame` + `sub_frame` để click nhầm không mở được.
- CSS cosmetic do content script inject khi công tắc bật: ẩn banner cờ bạc phủ player và popup "789BET". Selector để trong một file `chophim-cosmetic.css` cho dễ sửa.
- Nút "Bỏ qua" còn sót: content script tự bấm khi nó xuất hiện (chỉ khi công tắc bật).
- Không đụng tới: stream phim (`/api/playback/*`), manifest `p3`, VTT, thumbnail.
- Popup: công tắc "Chặn quảng cáo trên chophim". Lần đầu bật thì hiện cảnh báo một lần về điều khoản/nguồn thu của site.
- Quyền thêm: `declarativeNetRequest`. **Không** dùng `declarativeNetRequestFeedback`/`webRequest`.

## Related Code Files
- Create: `extension/rules/chophim-ads.json`, `extension/src/content/cosmetic.ts`, `extension/src/content/chophim-cosmetic.css`
- Modify: `extension/manifest.json` (`declarative_net_request.rule_resources`, `permissions`), `extension/src/sw/settings.ts`, `extension/src/popup/*`
- Create: `extension/test/rules.test.ts`. Kiểm bằng matcher thuần trên bộ URL thật lấy từ Phase 0:
  - các URL phải bị chặn: pre-roll m3u8, segment ad;
  - các URL **không** được bị chặn: `/api/storage/subtitles`, `/api/storage/webvtt`, `/api/playback/*`, `a.kvp726.com/*.m3u8`, `node01.chophim-cdn.fun/*`.

## Implementation Steps
1. Thu danh sách URL quảng cáo bằng harness Phase 0 (headless, chỉ quan sát), lưu `docs/phase0-evidence/ad-urls.txt`.
2. Viết ruleset + test kiểm danh sách chặn/không chặn.
3. Thêm công tắc vào SW settings (`updateEnabledRulesets`) và popup.
4. `cosmetic.ts`: inject/gỡ `<style>`; MutationObserver chờ nút "Bỏ qua".
5. E2E (Chrome for Testing): bật thì không pre-roll, phim phát và có tiếng lồng; tắt thì có pre-roll, 0 cue phát trong quảng cáo, phim phát bình thường sau đó.

## Success Criteria
- [x] Test ruleset xanh. Mutation (thêm rule chặn `/api/`) làm 4 test fail.
- [x] Bật: 3/3 lần load không có pre-roll, `video.error` null, phụ đề tải được, trạng thái armed.
- [x] Bật: không còn banner/popup cờ bạc (0 ảnh `/ads/` hiển thị, screenshot sạch).
- [x] Tắt: pre-roll 62 s vẫn phát, 0 câu lồng tiếng trong lúc quảng cáo (đã đo ở E2E Phase 2).
- [ ] Server KK/Thuyết Minh khi bật chặn: chưa đo riêng. Rule không chạm tới `a.kvp726.com`/`/api/playback` (có test), nên rủi ro thấp.

## Risk Assessment
| Rủi ro | Dấu hiệu | Phản ứng |
|---|---|---|
| Site đổi path quảng cáo | Pre-roll quay lại | Cập nhật `chophim-ads.json`; arm-gate vẫn đảm bảo không lồng tiếng lên quảng cáo |
| Site phát hiện adblock và chặn phim | Player báo lỗi/trang cảnh báo | Tắt rule tương ứng; giữ phần cosmetic |
| Rule ibyteimg chặn nhầm thumbnail | Seek preview mất ảnh | Thu hẹp rule theo path và initiator; có test |
