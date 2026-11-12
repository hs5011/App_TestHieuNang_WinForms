---
name: perftool-review
description: Review toàn diện mã nguồn PerfTool (công cụ kiểm thử hiệu năng: crawler Playwright, sinh script k6/JMeter, phân tích kết quả, báo cáo .docx, UI Streamlit) và xuất báo cáo REVIEW_<ngày>.md bằng tiếng Việt. Dùng khi người dùng yêu cầu "review", "đánh giá mã", "kiểm tra lỗi", "rà soát" dự án này, hoặc muốn kiểm tra lại các lỗi của lần review trước. Skill tự rút kinh nghiệm: đọc LESSONS.md trước khi làm và ghi bổ sung sau mỗi lần chạy.
---

# Review PerfTool

Mục tiêu: tìm lỗi **có thật, tái hiện được** – ưu tiên những lỗi làm hỏng dữ liệu hệ thống đích, rò bí mật,
hoặc khiến báo cáo hiệu năng "xanh" trong khi hệ thống đang lỗi. Không liệt kê lỗi suy đoán mà không kiểm chứng.

## Bước 0 – Nạp kinh nghiệm (BẮT BUỘC)

1. Đọc `LESSONS.md` cùng thư mục với file này. Áp dụng mọi bài học trong đó cho lần review này;
   nếu bài học mâu thuẫn với hướng dẫn bên dưới thì **bài học thắng** (nó mới hơn).
2. Tìm báo cáo cũ: `ls REVIEW_*.md` ở gốc repo. Nếu có, đọc báo cáo mới nhất và lập danh sách các lỗi
   cũ để kiểm tra lại ở Bước 3 (đã sửa / còn / sửa chưa đúng).
3. Ghi lại commit đang review: `git rev-parse --short HEAD`.

## Bước 1 – Chuẩn bị môi trường

- Tạo venv riêng ở scratchpad (không dùng `.venv` của người dùng), cài `requirements.txt`.
- Kiểm tra với **phiên bản Python thấp nhất README cam kết** (hiện là 3.10), không chỉ bản mới nhất.
- Chạy `pytest -q`, ghi số test pass/fail vào phần mở đầu báo cáo.
- `python -m py_compile` toàn bộ `samples/*.py`, `tools/*.py` (pytest không import các file này).

## Bước 2 – Bốn lượt review độc lập

Chạy 4 lượt, mỗi lượt một góc nhìn. Có thể dùng subagent cho từng lượt **chỉ khi người dùng đồng ý**;
nếu không thì làm tuần tự. Mỗi lỗi ghi: vị trí `file:dòng`, cách tái hiện, hệ quả.

1. **Bảo mật** – bí mật (mật khẩu, OTP, token, cookie) lưu ở đâu, quyền file, env truyền cho tiến trình con,
   TLS/SSH, injection vào template k6/JMX, path traversal, bộ lọc SQL read-only.
2. **Logic** – NaN/None/chuỗi rỗng từ UI, mã trùng, parse thời lượng, mã thoát tiến trình, PID tái sử dụng,
   cache không phụ thuộc đầu vào, file dữ liệu cụt.
3. **Kiến trúc & phương pháp kiểm thử** – mất phiên có bị tính là thành công không, correlation
   (ViewState/CSRF), assertion nội dung, cửa sổ đánh giá (loại ramp-up/down), tham số hoá dữ liệu,
   k6 và JMeter có thật sự chạy cùng hồ sơ tải không, độ phủ test.
4. **Kiểm thử động** – chạy `samples/demo_server.py` và một **server bẫy** (GET có tác dụng phụ, menu
   "Archive/Mark as read…", redirect về login trả 200) rồi chạy crawler/guard thật, đếm request ghi lọt qua.

Script tái hiện để trong scratchpad, **không** commit vào repo; liệt kê tên ở cuối báo cáo.

## Bước 3 – Xác nhận

- Mỗi lỗi mức Cao/TB phải được tái hiện bằng code hoặc đọc lại mã ít nhất 2 lần độc lập.
- Cột **Xác nhận** = số lượt/nguồn độc lập tái hiện được (vd `3/4`, `1/4 (có repro)`).
- Lỗi không tái hiện được → bỏ, hoặc ghi rõ "chưa xác nhận".
- Nếu có báo cáo cũ: thêm mục "Theo dõi lỗi lần trước" với trạng thái từng lỗi.

## Bước 4 – Viết báo cáo `REVIEW_<YYYY-MM-DD>.md` ở gốc repo

Giữ cấu trúc (để so sánh được giữa các lần):

```
# Báo cáo review PerfTool – <ngày>
Phạm vi / môi trường / kết quả pytest / commit
Kết luận ngắn (3–4 câu)
## 1. Lỗi phải sửa trước khi dùng trên hệ thống thật   (bảng: # | Mức | Lỗi | Vị trí | Xác nhận) + Lỗi mức Thấp
## 2. Lỗ hổng phương pháp kiểm thử                     (bảng: # | Rủi ro | Bằng chứng | Hệ quả)
## 3. Kiến trúc & bảo trì                              (Tốt / Cần cải thiện)
## 4. Những gì đã kiểm tra và thấy ổn
## 5. Lộ trình cải tiến đề xuất                        (Tuần 1 / Tuần 2–3 / Sau đó)
(## 6. Theo dõi lỗi lần trước – nếu có báo cáo cũ)
```

Mức: **Cao** = hỏng dữ liệu hệ thống đích, rò bí mật, mất dự án, không chạy được theo README;
**TB** = kết quả sai/ gây hiểu nhầm, crash có điều kiện; **Thấp** = khó chịu, cấu hình chết, hiếm gặp.

Không commit/push báo cáo trừ khi người dùng yêu cầu.

## Bước 5 – Rút kinh nghiệm (BẮT BUỘC, làm trước khi trả lời người dùng lần cuối)

Cập nhật `LESSONS.md` theo quy tắc:

- Chỉ ghi điều **dùng lại được cho lần sau**: cách tìm ra lỗi hiệu quả, chỗ đã bỏ sót, góp ý/chỉnh sửa
  của người dùng, bước tốn thời gian vô ích, công cụ/lệnh hữu ích. Không chép lại danh sách lỗi
  (đã có trong báo cáo).
- Mỗi bài học 1–3 dòng, dạng: `- [YYYY-MM-DD] <bài học>. **Vì sao:** <lý do>.`
- Trước khi thêm, kiểm tra trùng: nếu đã có bài học tương tự thì sửa/bổ sung dòng đó thay vì thêm mới.
  Bài học sai hoặc lỗi thời → xoá.
- Góp ý trực tiếp của người dùng luôn được ghi (mục "Góp ý của người dùng").
- Khi `LESSONS.md` vượt ~80 dòng: gộp các bài học đã ổn định vào thẳng các bước trong `SKILL.md`,
  rồi xoá chúng khỏi `LESSONS.md`.
- Cuối câu trả lời, báo người dùng ngắn gọn đã thêm/sửa bài học nào.
