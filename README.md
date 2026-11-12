# ⚡ PerfTool – Phân tích Use Case & Kiểm thử hiệu năng Web (chạy local trên Windows)

> **Người dùng mới:** đọc [HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md) (bản Word: `HUONG_DAN_SU_DUNG.docx`) – cài đặt,
> cách dùng 10 bước, cấu trúc thư mục, yêu cầu hệ thống và xử lý sự cố. README này dành cho người phát triển.

Ứng dụng Python chạy **trên máy cá nhân**, dẫn dắt theo từng bước:

```
Nhập thông tin → Import UC → Đăng nhập & phân tích Web → Phân tích & chọn UC → Người dùng xác nhận
→ Chọn k6/JMeter → Cấu hình & chạy test → Thu thập kết quả → Phân tích → Biểu đồ → Xuất báo cáo
```

## 1. Cài đặt & chạy

Yêu cầu: Windows 10/11, **Python 3.10+**, **k6** và/hoặc **Apache JMeter 5.x (kèm Java 11+)**.

```bat
setup.bat      :: tạo .venv, cài thư viện, cài Chromium cho Playwright
run.bat        :: mở ứng dụng tại http://localhost:8501
```

- Cài k6: `winget install k6 --source winget`
- JMeter không nằm trong PATH → khai báo `tools.jmeter_path` trong `config/settings.yaml`
  (mặc định app tự dò thêm `C:\Tools\apache-jmeter-*`).

**Chạy thử ngay với web demo:**
```bat
.venv\Scripts\python samples\demo_server.py     :: http://127.0.0.1:8088  (admin / Admin@123)
```
Sau đó tạo dự án, URL `http://127.0.0.1:8088`, URL đăng nhập `http://127.0.0.1:8088/login`,
import `samples/uc_mau.xlsx` và đi lần lượt các bước.

## 2. Kiến trúc

```
┌─────────────────────────── Streamlit UI (app.py + perftool/ui) ───────────────────────────┐
│ 10 bước wizard · data_editor để xem/sửa/xác nhận · theo dõi tiến trình (auto refresh)     │
└──────────────┬───────────────────────────────┬────────────────────────────┬───────────────┘
               │ gọi trực tiếp                 │ tiến trình nền (jobs.py)   │ gọi trực tiếp
┌──────────────▼─────────────┐  ┌──────────────▼──────────────┐  ┌──────────▼──────────────────┐
│ uc_import  (Excel/CSV)     │  │ crawler (Playwright)        │  │ results → charts → report   │
│ analysis   (chấm điểm UC)  │  │ runner  (k6 / JMeter CLI)   │  │ (pandas · matplotlib · docx)│
│ scriptgen  (Jinja2 → .js/  │  └──────────────┬──────────────┘  └─────────────────────────────┘
│            .jmx)           │                 │ ghi JSON/CSV/JTL
└────────────────────────────┘   workspace/projects/<id>/ … (project.json + dữ liệu từng bước)
```

| Thư mục | Vai trò |
|---|---|
| `perftool/models.py` | Mô hình dữ liệu (Pydantic) – toàn bộ trạng thái dự án lưu ở `project.json` |
| `perftool/uc_import/` | Đọc Excel/CSV, tự tìm dòng tiêu đề, tự ánh xạ cột (Mã/Tên/Phân hệ/Mô tả/Số bước…), ước lượng số bước từ mô tả |
| `perftool/crawler/` | Đăng nhập (tự dò form, hỗ trợ đăng nhập thủ công khi có OTP/CAPTCHA), phát hiện phân hệ từ menu, quét màn hình: đếm input/bảng/nút CRUD, ghi lại XHR/fetch + thời gian phản hồi, chụp màn hình; bắt request đăng nhập & đường dẫn token để sinh script |
| `perftool/analysis/complexity.py` | Ghép UC ↔ màn hình (fuzzy, bỏ dấu), tính 6 tiêu chí, chuẩn hoá, tổng điểm có trọng số, đề xuất top N, sinh kịch bản request cho từng UC |
| `perftool/scriptgen/` | Template Jinja2: `k6_script.js.j2`, `jmeter_plan.jmx.j2`; hồ sơ tải smoke/load/stress/spike/soak |
| `perftool/runner/` | Chạy tuần tự các lượt test ở tiến trình nền, truyền tài khoản qua biến môi trường / file properties tạm (xoá sau khi chạy) |
| `perftool/results/` | Parser k6 CSV & JMeter JTL → DataFrame chuẩn; chỉ số (req, throughput, avg/min/max, p50/90/95/99, error rate, VUs); đánh giá ngưỡng; nhận xét tự động |
| `perftool/charts/` | Biểu đồ PNG theo phong cách mẫu báo cáo |
| `perftool/report/` | Báo cáo `.docx` theo `Template_BaoCaoHieuNang.docx` (+ Excel số liệu, PDF nếu có MS Word) |
| `perftool/jobs.py` | Quản lý tiến trình nền (trạng thái, log, dừng cả cây tiến trình) |
| `perftool/accounts.py` | Nhiều tài khoản kiểm thử: đọc danh sách (dán/CSV/Excel), kiểm tra đăng nhập, ghi/xoá file tạm `accounts.json` (k6) / `accounts.csv` (JMeter CSV Data Set) |

**Vì sao tách tiến trình nền?** Playwright và k6/JMeter chạy lâu; tách tiến trình giúp UI không treo,
tránh xung đột event-loop của Playwright trên Windows, và có thể **Dừng** bất cứ lúc nào.

## 3. Tiêu chí chấm điểm UC (chỉnh trong UI hoặc `config/settings.yaml`)

| Tiêu chí | Nguồn số liệu |
|---|---|
| Số bước | Cột “Số bước” hoặc đếm bước trong mô tả |
| Số màn hình | Số màn hình ghép được với UC |
| Số API/request | Số endpoint XHR/fetch khác nhau trên các màn hình |
| Thao tác CRUD | Số **loại** thao tác ghi (`scoring.operation_groups.crud`) trong tên/mô tả UC + nhãn nút, + tối đa 2 cho API ghi |
| Xử lý dữ liệu | Số **loại** thao tác xử lý (`operation_groups.data_processing`) + có bảng + bảng ≥ 50 dòng + phản hồi > 100 KB |
| Thời gian phản hồi | ~p90 thời gian phản hồi API đo được khi quét |

Mỗi tiêu chí quy về 0–1 bằng cách chia cho mức trần: `caps_mode: auto` = p90 của danh sách UC trong dự án (≥ `cap_floor`),
`fixed` = `scoring.caps`. Tổng điểm = trung bình có trọng số × 100 (`complexity.contributions()` trả về phần đóng góp từng tiêu chí).

## 4. An toàn

- Crawler **chỉ điều hướng qua link/menu**, không bấm Lưu/Xoá, không gửi form nghiệp vụ; bỏ qua link chứa từ khoá nguy hiểm (`crawler.exclude_keywords`).
- Tự quét tương tác (tuỳ chọn, `perftool/crawler/interactions.py`): bấm tab / Tìm kiếm / trang 2 / nút Xem-Thêm-Sửa / menu ⋮
  dòng đầu bảng, không bấm nút trong popup và mục thuộc `crawler.popup_blacklist`. Bộ chặn ghi (`perftool/crawler/guard.py`)
  huỷ request ghi dữ liệu trong trình duyệt (vẫn ghi nhận `blocked=True`). Ghi thao tác thủ công: `perftool/crawler/manual.py`
  (`--mode record`), lưu `crawl/record.json`, gộp bằng `merge.apply_record()` (idempotent, giữ lại khi phân tích lại).
- Request ghi dữ liệu (POST/PUT/DELETE) trong kịch bản **mặc định bị tắt**; người dùng chủ động bật khi cần.
- Mật khẩu **không** lưu vào `project.json`; chỉ giữ trong phiên hoặc Windows Credential Manager (tuỳ chọn).
- Streamlit chỉ lắng nghe `127.0.0.1`.
- Chỉ kiểm thử hệ thống bạn có quyền; tránh chạy tải lớn lên môi trường production khi chưa được phép.

## 5. Mở rộng

- **Thêm công cụ test** (Locust, Gatling…): thêm template trong `scriptgen/templates`, hàm `generate_x`, nhánh trong `runner/executor.build_command` và parser trong `results/parsers.py` trả về DataFrame chuẩn.
- **Thêm tiêu chí chấm điểm**: bổ sung vào `CRITERIA` + `compute_raw` trong `analysis/complexity.py`.
- **Đổi mẫu báo cáo**: thay file ở `report.template`; cấu trúc nội dung nằm trong `report/docx_report.py`.
- **Thêm nguồn số liệu máy chủ** (bước 9): viết hàm trả về danh sách `(thời điểm, metric, giá trị)` trong `monitor/sources.py`, ghi bằng `monitor.store.CsvSink`; metric chuẩn khai báo ở `monitor/__init__.py` (`METRICS`).

## 6. Kiểm thử mã nguồn

```bat
.venv\Scripts\python -m pytest -q
```


## Kiểm thử ứng dụng WinForms (gọi API)

Thư mục này là bản mở rộng của PerfTool cho ứng dụng WinForms/desktop có kiến trúc *client gọi API máy chủ*.
Chọn **Loại ứng dụng = WinForms** ở bước 1. Bước 3 không dùng Playwright mà ghi lưu lượng HTTP(S) của ứng dụng:

1. **Proxy ghi** (`perftool/crawler/winforms_cli.py` + `mitm_addon.py`, cần `mitmproxy`): trỏ ứng dụng qua `127.0.0.1:<cổng>`
   (biến `HTTP_PROXY`, proxy hệ thống Windows hoặc `<defaultProxy>` trong App.config); HTTPS cần cài chứng chỉ mitmproxy
   vào máy kiểm thử. Bấm "📍 Bắt đầu" mỗi khi sang màn hình mới để tạo mốc.
2. **Nhập HAR** từ Fiddler/Charles/mitmproxy/DevTools.
3. `crawler/winforms.py` chia màn hình, ước lượng số trường/bảng/dòng từ nội dung API → `crawl/record.json`
   → `winforms_merge.merge_winforms()` gộp vào dự án. Bước 4–10 (chấm điểm, k6/JMeter, kết quả, báo cáo) dùng chung với Web.

Giới hạn: chỉ HTTP(S); không hỗ trợ net.tcp/.NET Remoting/SQL trực tiếp/certificate pinning. Xem `CHANGELOG.md` 2026-11-12.
