# Kinh nghiệm review PerfTool

Skill `perftool-review` đọc file này ở Bước 0 và cập nhật ở Bước 5. Mới nhất ở cuối mỗi mục.

## Phương pháp

- [2026-10-01] Pytest pass 100 % (414 test) nhưng vẫn có 4 lỗi mức Cao. **Vì sao:** test không chạy
  luồng thật (crawl demo, k6/JMeter thật) và không import `samples/` – luôn kiểm thử động, không tin số test.
  [2026-10-02] Lặp lại: 470 test pass nhưng server bẫy vẫn tìm ra 2 lỗi Cao.
- [2026-10-01] Server bẫy (GET có tác dụng phụ, `/api/_stats` đếm request ghi) là cách hiệu quả nhất để
  tìm lỗ hổng của guard chặn ghi. **Vì sao:** đọc mã không thấy được, chạy thật phát hiện ngay 5 request lọt.
  [2026-10-02] Bẫy phải có cả **động từ chưa có trong `write_keywords`** (`ack`, `pin`, `touch`, `open`, `visit`),
  POST lúc tải trang và link menu trái có tác dụng phụ – không chỉ các mẫu của báo cáo cũ. **Vì sao:** bản sửa theo
  báo cáo cũ chặn đúng các mẫu cũ, lỗ hổng nằm ở mẫu mới.
- [2026-10-01] Kiểm tra với Python thấp nhất README cam kết (3.10). **Vì sao:** `demo_server.py` dùng
  f-string chỉ hợp lệ từ 3.12, máy dev chạy bản mới nên không ai thấy.
  [2026-10-02] Máy này chỉ có 3.13 và người dùng từ chối tải 3.10 qua `uv` → hỏi sớm, nếu không được thì kiểm tĩnh
  (test dò `\` trong biểu thức f-string có trong `tests/edge/test_edge_review_20261001.py`) và ghi rõ trong báo cáo.
- [2026-10-01] Luôn thử các "đường tắt" của UI: ô để trống (NaN), bỏ trống mật khẩu khi tự đăng nhập,
  mã trùng, tài khoản kiểu `user01/user01`. **Vì sao:** 3 lỗi Cao/TB đến từ các đầu vào này.
  [2026-10-02] Thêm: mã UC có ký tự cấm của Windows (`:` `*` `"`) – mã UC được dùng làm tên thư mục.
- [2026-10-01] Với công cụ đo hiệu năng, hỏi "báo cáo có thể xanh khi hệ thống đang lỗi không?"
  (mất phiên, 200 trang lỗi, không correlation). **Vì sao:** đây là rủi ro lớn nhất, không lộ ra qua crash.
- [2026-10-02] Mỗi luồng thu request (quét 3a/3b, ghi thủ công 3c, đăng nhập) phải kiểm riêng xem bí mật có bị lưu
  không; với 3c thử "đăng nhập lại giữa lúc ghi" bằng Playwright + `merge.apply_record` (không cần người thao tác).
  **Vì sao:** bản sửa trước chỉ lo luồng đăng nhập chính, 3c vẫn lưu mật khẩu nguyên văn.
- [2026-10-02] Khi review sau một đợt sửa, tìm cả **dương tính giả / hồi quy do chính bản sửa** (vd chặn GET làm
  hỏng popup `extend-info` trên demo). **Vì sao:** bộ chặn chặt hơn có cái giá riêng, test đơn vị không thấy.
- [2026-10-02] Chạy crawler trong tiến trình bằng cách đặt `perftool.storage.PROJECTS_DIR` sang thư mục tạm rồi gọi
  `perftool.crawler.cli.main()` (discover → gán `project.modules` từ `discover.json` → analyze, `headless=True`).
  **Vì sao:** không đụng `workspace/` thật của người dùng, chạy lặp lại nhanh (~40 s/lượt).

- [2026-10-02] Mọi thay đổi quy tắc phân loại đọc/ghi phải đo trên **dữ liệu quét thật** trong
  `workspace/projects/*/project.json` (chỉ đọc) trước khi đề xuất/chấp nhận. **Vì sao:** quy tắc đúng với mẫu tự nghĩ
  vẫn tắt nhầm API đọc thật (`gia-han/danh-sach`, `isCreate=false`, `?d=<epoch>`) và trang menu thật (`viec-can-xu-ly`).
- [2026-10-02] Mở app Streamlit sẽ nạp (và có thể di chuyển dữ liệu) MỌI dự án trong `workspace/` – sao lưu
  `project.json` + `crawl/auth.json` trước và khôi phục sau khi kiểm tra giao diện. **Vì sao:** không đổi dữ liệu thật
  của người dùng khi họ chưa tự mở app.

## Vùng hay có lỗi

- [2026-10-01] `crawler/web_crawler.py` (bắt login, thay placeholder), `crawler/guard.py`,
  `ui/steps/s05_confirm.py`, `runner/` (mã thoát, PID), `results/` (cache, parser JTL).
- [2026-10-02] Phân loại đọc/ghi nằm rải rác: `guard.is_write_request`, `guard.is_query_request`,
  `web_crawler._excluded` và bước "Mở màn hình" luôn bật trong `complexity.build_scenario` – kiểm cả 4 chỗ.
  `RequestRecorder` (lưu `post_data`/header không lọc) là nguồn rò bí mật chung.

## Góp ý của người dùng

- [2026-10-02] Báo cáo viết bằng tiếng Việt; người dùng tự quyết định khi nào commit/push báo cáo.
- [2026-10-02] Người dùng chọn review **tuần tự, không dùng subagent** và **không tải thêm Python 3.10**.
