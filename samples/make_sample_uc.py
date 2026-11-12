"""Tạo file samples/uc_mau.xlsx minh hoạ định dạng danh sách Use Case."""
from pathlib import Path

import pandas as pd

rows = [
    ("UC-CV-001", "Xem danh sách công việc", "Quản lý công việc", "Chuyên viên",
     "1. Chọn menu Danh sách công việc\n2. Hệ thống hiển thị danh sách\n3. Lọc theo trạng thái", None, "Cao", "Trung bình"),
    ("UC-CV-002", "Thêm mới công việc", "Quản lý công việc", "Lãnh đạo",
     "1. Chọn Thêm mới\n2. Nhập thông tin\n3. Chọn người xử lý\n4. Đính kèm tệp\n5. Lưu", None, "Trung bình", "Phức tạp"),
    ("UC-CV-003", "Thống kê giao việc theo phòng ban", "Quản lý công việc", "Lãnh đạo",
     "1. Chọn menu Thống kê\n2. Chọn kỳ thống kê\n3. Hệ thống tổng hợp số liệu\n4. Xem biểu đồ\n5. Xuất Excel", None, "Cao", "Phức tạp"),
    ("UC-VB-001", "Xem danh sách văn bản đến", "Quản lý văn bản", "Văn thư",
     "1. Chọn menu văn bản đến\n2. Xem danh sách", None, "Cao", "Đơn giản"),
    ("UC-VB-002", "Tra cứu văn bản", "Quản lý văn bản", "Chuyên viên",
     "1. Chọn Tra cứu\n2. Nhập từ khoá, lọc theo loại\n3. Tìm kiếm\n4. Xem kết quả", None, "Cao", "Trung bình"),
    ("UC-VB-003", "Chuyển xử lý văn bản", "Quản lý văn bản", "Văn thư",
     "1. Chọn văn bản\n2. Chọn Chuyển xử lý\n3. Chọn người nhận\n4. Gửi", None, "Trung bình", "Trung bình"),
    ("UC-BC-001", "Xem báo cáo tổng hợp", "Báo cáo", "Lãnh đạo",
     "1. Chọn Báo cáo tổng hợp\n2. Chọn tiêu chí\n3. Hệ thống tổng hợp dữ liệu\n4. Hiển thị biểu đồ\n5. In/Xuất Excel", None,
     "Trung bình", "Phức tạp"),
]
df = pd.DataFrame(rows, columns=["Mã UC", "Tên UC", "Phân hệ", "Tác nhân", "Mô tả", "Số bước", "Tần suất", "Mức độ"])
out = Path(__file__).with_name("uc_mau.xlsx")
with pd.ExcelWriter(out, engine="openpyxl") as xw:
    pd.DataFrame([["DANH SÁCH USE CASE HỆ THỐNG DEMO"]]).to_excel(xw, index=False, header=False, startrow=0)
    df.to_excel(xw, index=False, startrow=2)
print("Đã tạo", out)
