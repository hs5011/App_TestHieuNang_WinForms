# HƯỚNG DẪN SỬ DỤNG PERFTOOL

**PerfTool** là ứng dụng chạy trên máy cá nhân (Windows) giúp:

1. Đọc danh sách Use Case (UC) từ file Excel/CSV.
2. Tự đăng nhập hệ thống Web, quét các phân hệ, màn hình và API thực tế.
3. Chấm điểm độ phức tạp và đề xuất 2–3 UC phù hợp nhất để kiểm thử hiệu năng.
4. Sinh script **k6** và/hoặc **Apache JMeter**, chạy test, thu thập chỉ số.
5. Phân tích, vẽ biểu đồ và xuất **báo cáo Word/Excel** theo mẫu.

Tài liệu này dành cho người dùng mới hoàn toàn. Làm lần lượt từ mục 1 đến mục 4 là chạy được.

---

## 1. Yêu cầu hệ thống

### 1.1. Phần cứng và hệ điều hành

| Hạng mục | Tối thiểu | Khuyến nghị |
|---|---|---|
| Hệ điều hành | Windows 10 64-bit | Windows 11 64-bit |
| CPU | 4 nhân | 8 nhân trở lên (khi chạy tải lớn) |
| RAM | 8 GB | 16 GB trở lên (JMeter dùng nhiều RAM khi nhiều người dùng ảo) |
| Ổ đĩa trống | 3 GB | 10 GB trở lên (mỗi lượt load test có thể tạo vài chục đến vài trăm MB dữ liệu thô) |
| Mạng | Kết nối Internet khi cài đặt; truy cập được hệ thống cần kiểm thử khi chạy | Mạng ổn định, băng thông đủ lớn (máy test không nên là điểm nghẽn) |

### 1.2. Phần mềm cần cài

| Phần mềm | Phiên bản | Bắt buộc? | Dùng để |
|---|---|---|---|
| **Python** | 3.10 trở lên (đã kiểm thử với 3.13) | Bắt buộc | Chạy ứng dụng |
| **k6** | 0.50 trở lên (đã kiểm thử với v2.2) | Cần nếu chạy test bằng k6 | Công cụ bắn tải |
| **Java (JDK/JRE)** | 11 trở lên (đã kiểm thử với 21) | Cần nếu dùng JMeter | JMeter chạy trên Java |
| **Apache JMeter** | 5.5 trở lên (đã kiểm thử với 5.6.3) | Cần nếu chạy test bằng JMeter | Công cụ bắn tải |
| Microsoft Word | Bất kỳ | Không bắt buộc | Mở báo cáo; xuất PDF (tuỳ chọn) |
| Trình duyệt Chrome/Edge | Bất kỳ | Không bắt buộc | Mở giao diện ứng dụng (địa chỉ `http://localhost:8501`) |

Cần ít nhất **một** trong hai công cụ k6 hoặc JMeter. Nên cài cả hai để đối chiếu kết quả.

Các thư viện Python và trình duyệt Chromium dùng để quét web được **tự động cài** bởi `setup.bat` (xem mục 2).

### 1.3. Quyền truy cập hệ thống cần kiểm thử

- Địa chỉ (URL) của hệ thống Web và **một tài khoản đăng nhập** hợp lệ.
- **Sự cho phép** của đơn vị quản lý hệ thống trước khi chạy tải. Kiểm thử hiệu năng tạo ra lượng truy cập lớn; không chạy load/stress lên hệ thống đang vận hành khi chưa được đồng ý.
- Nếu hệ thống có OTP, CAPTCHA hoặc đăng nhập SSO: dùng chế độ **"Tôi sẽ tự đăng nhập trên trình duyệt"** ở bước 1 (ứng dụng mở trình duyệt để bạn tự đăng nhập, sau đó dùng lại phiên đó).

---

## 2. Cài đặt

### 2.1. Cài Python

1. Tải bộ cài tại <https://www.python.org/downloads/> (chọn bản Windows 64-bit).
2. Khi cài, **tick chọn "Add python.exe to PATH"** ở màn hình đầu tiên, rồi bấm *Install Now*.
3. Mở **Command Prompt** và kiểm tra:
   ```bat
   python --version
   ```
   Kết quả phải là `Python 3.10` trở lên.

> Nếu lệnh `python` mở Microsoft Store thay vì chạy Python: vào *Settings → Apps → Advanced app settings → App execution aliases* và **tắt** hai mục `python.exe` / `python3.exe`, sau đó mở lại Command Prompt.

### 2.2. Cài k6

Mở Command Prompt và chạy:
```bat
winget install k6 --source winget
```
Đóng rồi mở lại Command Prompt, kiểm tra:
```bat
k6 version
```
Nếu không có `winget`: tải bộ cài `.msi` tại <https://grafana.com/docs/k6/latest/set-up/install-k6/>.

### 2.3. Cài Java và JMeter

1. Cài Java 17 hoặc 21 (ví dụ **Eclipse Temurin**: <https://adoptium.net/>). Khi cài, chọn *Set JAVA_HOME* và *Add to PATH*. Kiểm tra: `java -version`.
2. Tải JMeter bản *Binaries (.zip)* tại <https://jmeter.apache.org/download_jmeter.cgi>.
3. Giải nén vào một thư mục, ví dụ `C:\Tools\apache-jmeter-5.6.3`.
4. Cho ứng dụng biết đường dẫn JMeter, **chọn một trong hai cách**:
   - Giải nén đúng vào `C:\Tools\apache-jmeter-*` – ứng dụng tự tìm; **hoặc**
   - Mở `config\settings.yaml`, sửa dòng:
     ```yaml
     jmeter_path: "C:\\Tools\\apache-jmeter-5.6.3\\bin\\jmeter.bat"
     ```

### 2.4. Cài PerfTool

1. Chép toàn bộ thư mục ứng dụng về máy, ví dụ `E:\MFG\App_TestHieuNang`.
2. Bấm đúp **`setup.bat`**. Script sẽ:
   - tạo môi trường Python riêng trong thư mục `.venv` (không ảnh hưởng Python chung của máy);
   - cài các thư viện trong `requirements.txt`;
   - tải trình duyệt Chromium cho Playwright (khoảng vài trăm MB, cần Internet);
   - kiểm tra đã có k6 / JMeter chưa và in thông báo.
3. Chờ tới dòng `==== Hoan tat ====` rồi nhấn phím bất kỳ để đóng.

Chỉ cần chạy `setup.bat` **một lần**. Chạy lại khi cập nhật phiên bản ứng dụng mới hoặc khi bị lỗi thiếu thư viện.

### 2.5. Chạy ứng dụng

1. Bấm đúp **`run.bat`**.
2. Cửa sổ đen (console) hiện ra và trình duyệt tự mở địa chỉ **<http://localhost:8501>**. Nếu trình duyệt không tự mở, hãy tự gõ địa chỉ này.
3. **Không đóng cửa sổ đen** trong khi dùng ứng dụng. Muốn tắt ứng dụng: đóng cửa sổ đen (hoặc nhấn `Ctrl + C` trong cửa sổ đó).

> Ứng dụng chỉ mở cho **chính máy này** (`127.0.0.1`); máy khác trong mạng không truy cập được – để bảo vệ thông tin đăng nhập.

### 2.6. Chạy thử với web demo (không cần hệ thống thật)

Để làm quen, có thể chạy một web demo có sẵn:
```bat
.venv\Scripts\python samples\demo_server.py
```
Rồi tạo dự án mới với URL `http://127.0.0.1:8088`, URL đăng nhập `http://127.0.0.1:8088/login`, tài khoản `admin` / `Admin@123`, import file `samples\uc_mau.xlsx`.

Trang *Danh sách công việc* của web demo có tab, nút Tìm kiếm, phân trang và **menu ⋮ trên từng dòng** (Xử lý, Xin gia hạn, Phân công, Hoàn thành, Xóa) – dùng để thử *Tự quét tương tác* và *Ghi thao tác thủ công* ở bước 3 (mục Hoàn thành/Xóa sẽ không được bấm).

Web demo có thêm 10 tài khoản `user01` … `user10` (mật khẩu `Test@123`, có sẵn trong file `samples\tai_khoan_demo.csv`) và giới hạn API *Báo cáo tổng hợp* ở 4 request/giây cho mỗi tài khoản – dùng để thử tính năng nhiều tài khoản: chạy cùng mức tải với 1 tài khoản sẽ gặp lỗi 429, với 10 tài khoản thì hết.

---

## 3. Sử dụng – 10 bước

Thanh bên trái hiển thị các bước; bước đã xong có dấu ✅. Có thể quay lại bước trước bất cứ lúc nào.

| Bước | Việc cần làm | Lưu ý |
|---|---|---|
| **Tạo dự án** | Thanh bên trái → *➕ Tạo dự án mới* → nhập tên → *Tạo dự án* | Mỗi hệ thống/đợt test nên là 1 dự án |
| **1. Nhập thông tin** | URL hệ thống, URL trang đăng nhập, tài khoản chính, mật khẩu → *Lưu thông tin*. (Tuỳ chọn) mục **Tài khoản kiểm thử**: dán danh sách `tài_khoản,mật_khẩu` hoặc tải file CSV/Excel → *Thêm / cập nhật tài khoản* | Tick **"Ghi nhớ mật khẩu"** để không phải nhập lại mỗi lần mở ứng dụng. OTP/CAPTCHA/SSO: tick *"Tôi sẽ tự đăng nhập trên trình duyệt"*. Tài khoản chính dùng để quét web ở bước 3; nút *Kiểm tra đăng nhập* dùng được sau bước 3 (xem mục 5.2) |
| **2. Import Use Case** | Chọn file Excel/CSV → kiểm tra ánh xạ cột → *Nhập danh sách UC* | Hỗ trợ cả mẫu phân cấp theo QĐ 671 (dòng nhóm A./I./1. và dòng giao dịch con). Có thể sửa trực tiếp bảng UC |
| **3. Đăng nhập & phân tích Web** | **3a** *Đăng nhập & phát hiện phân hệ* → **3b** tick phân hệ cần quét → (tuỳ chọn) tick *Tự quét tương tác trên màn hình* → *Phân tích … phân hệ* → xem kết quả ở **3d** → *(tuỳ chọn, không bắt buộc)* **3c** *🎥 Bắt đầu ghi* nếu 3d còn thiếu màn hình cần test | Chi tiết 4 mục của bước 3 xem bảng ngay dưới đây.  Mặc định ứng dụng **chỉ điều hướng và đọc giao diện**, không bấm nút nào. Khi bật tự quét tương tác, ứng dụng bấm thử tab, Tìm kiếm, chuyển trang, nút Xem/Thêm mới/Sửa và **menu ⋮ ở dòng đầu bảng** (Xử lý, Xin gia hạn, Phân công…), **không bấm gì bên trong popup**, không bấm mục nguy hiểm, có **bộ chặn ghi dữ liệu** (mục 5.2a). Trình duyệt Chrome quét web **mở toàn màn hình** để thấy đủ menu/chức năng. Tiến độ xem ở khung *📜 Phát hiện phân hệ* / *📜 Phân tích phân hệ* / *📜 Ghi thao tác thủ công* (mục 5.7) |
| **4. Phân tích & chọn UC** | Chỉnh trọng số, chọn mức trần, chọn đề xuất theo toàn dự án (*Số UC đề xuất*) hoặc **theo từng phân hệ** (*Số UC mỗi phân hệ*, mặc định 2) → *Tính điểm & đề xuất* → tick cột *Chọn* | Bấm **❔ Giải thích** để xem cách tính điểm (mục 5.3). UC ⭐ là UC được đề xuất. Mở *Vì sao UC này được điểm như vậy?* để xem từng tiêu chí. Nếu UC ghép sai màn hình, dùng mục *Điều chỉnh màn hình ghép* |
| **5. Xác nhận** | Xem danh sách request của từng UC → *Lưu kịch bản* → **✅ Xác nhận phạm vi kiểm thử** | Request ghi dữ liệu (POST/PUT/DELETE) mặc định **tắt** để tránh tạo dữ liệu rác |
| **6. Chọn công cụ** | Chọn k6 / JMeter / Cả hai. *(Tuỳ chọn)* khối **📡 Thu số liệu tài nguyên máy chủ**: bật công tắc → chọn công cụ giám sát → khai báo máy chủ → nhập mật khẩu SSH / thông tin CSDL → *Lưu & kiểm tra* | Màn hình báo công cụ nào đã cài. Số liệu máy chủ được thu **trong lúc chạy test ở bước 7**, nên phải bật **trước khi chạy**; không bật thì báo cáo ghi “không thu thập” (mục 5.8) |
| **7. Cấu hình & chạy test** | Chọn loại kịch bản, số người dùng ảo (VUs), thời gian, **tài khoản dùng khi chạy test** (chỉ tài khoản chính / chia đều nhiều tài khoản) → *Lưu cấu hình* → *Sinh script* → *▶ Chạy test* | Bấm **❔ Hướng dẫn** để xem giải thích 5 loại kịch bản. Luôn chạy **Smoke Test** (3–10 VUs) trước. Theo dõi ở khung *📜 Tiến trình chạy test* và bảng *Lịch sử lượt chạy* (tự cập nhật). Trạng thái **✅ Đã chạy xong** chỉ có nghĩa là lượt đã chạy hết, **chưa phải là Đạt**. Xoá lượt không cần: tick dòng trong bảng lịch sử, hoặc dùng *🗑 Dọn lịch sử* (mục 5.7) |
| **8. Kết quả & phân tích** | Chọn **kịch bản đưa vào thống kê & báo cáo** ở đầu trang → *🔄 Phân tích lại*. Xem tổng hợp theo phân hệ, bảng tổng hợp, chi tiết từng UC/API, nhận xét tự động | Mọi số liệu ở bước 8, biểu đồ ở bước 9 và báo cáo ở bước 10 chỉ tính các kịch bản đã chọn ở đây. Lượt chạy mới tự được đưa vào. Nếu bật thu số liệu máy chủ (bước 6), cuối trang có mục **Tài nguyên máy chủ theo lượt chạy** (kèm khung tải file log Performance Monitor lên cho máy chủ không có SSH). **Đạt** khi p95 ≤ ngưỡng **và** tỷ lệ lỗi ≤ ngưỡng; trượt 1 trong 2 là **Không đạt**. *Tổng request* = tổng số request k6/JMeter ghi trong file kết quả thô (mục 5.7). Có thể nhập *Nhận xét bổ sung* để đưa vào báo cáo |
| **9. Biểu đồ** | Xem biểu đồ tổng quan và từng kịch bản (theo lựa chọn ở bước 8) | Ảnh PNG được lưu trong thư mục `charts\`. Có số liệu máy chủ thì thêm biểu đồ *Tài nguyên máy chủ* |
| **10. Xuất báo cáo** | Chọn các mục → *📄 Tạo báo cáo* → tải DOCX/XLSX | Kịch bản lấy theo lựa chọn ở bước 8 (nút *✏️ Đổi lựa chọn*). ≥2 kịch bản có thêm **mục tổng quan**; ≥2 phân hệ có thêm **mục tổng hợp theo phân hệ**. Ô **Tài nguyên máy chủ** tự bật khi có số liệu: mỗi kịch bản thêm bảng + biểu đồ + nhận xét, Excel có sheet *Tai nguyen may chu* |

**Các mục trong bước 3:**

| Mục | Bắt buộc? | Làm gì |
|---|---|---|
| **3a.** Đăng nhập & phát hiện phân hệ | Có | Đăng nhập hệ thống, đọc menu để lập danh sách phân hệ, bắt request đăng nhập/token để sinh script |
| **3b.** Danh sách phân hệ cần phân tích | Có | Tick phân hệ cần quét → *🔍 Phân tích … phân hệ*: mở từng màn hình trên menu, đếm trường nhập/bảng/nút, ghi API và thời gian phản hồi. Tick thêm **Tự quét tương tác** để bấm thử tab, Tìm kiếm, chuyển trang, nút Xem/Thêm/Sửa, **menu ⋮ dòng đầu bảng** (mục 5.2a) |
| **3c.** Ghi thao tác thủ công | **Không** (tuỳ chọn) | Chỉ dùng khi 3d còn thiếu màn hình: bạn tự bấm trên trình duyệt do PerfTool mở, công cụ ghi lại màn hình/popup và API (mục 5.2a) |
| **3d.** Kết quả phân tích giao diện | – (chỉ xem) | Số màn hình, API theo phân hệ; bảng *Popup / trang con / tương tác đã quét* để kiểm tra đã đủ màn hình chưa |

**Có cần làm 3c không?** Mở bảng *Popup / trang con / tương tác đã quét* ở 3d, đối chiếu với các màn hình của UC cần test:

- Đã có đủ (vd *Xử lý*, *Xin gia hạn*, *Phân công* của màn hình *Việc cần xử lý*) → **bỏ qua 3c**, bấm *Tiếp tục ➜* sang bước 4.
- Còn thiếu → dùng 3c. Thường gặp khi: nút ⋮/menu của hệ thống có kiểu giao diện công cụ chưa nhận ra; màn hình phải qua nhiều bước mới mở được (chọn dữ liệu rồi mới bấm *Xử lý*); chức năng chỉ có ở dòng khác dòng đầu bảng; popup mở từ bên trong popup khác (tự quét không bấm gì trong popup); hoặc không được phép bật tự quét trên hệ thống thật.

**Thứ tự kiểm thử nên làm:** Smoke Test → Load Test → Stress Test/Spike Test → Soak Test.

---

## 4. Cấu trúc thư mục

```
App_TestHieuNang\
├── app.py                        ← điểm khởi động giao diện
├── setup.bat                     ← cài đặt môi trường (chạy 1 lần)
├── run.bat                       ← mở ứng dụng
├── requirements.txt              ← danh sách thư viện Python
├── README.md                     ← mô tả kỹ thuật / kiến trúc (cho người phát triển)
├── HUONG_DAN_SU_DUNG.md          ← tài liệu này
├── CHANGELOG.md                  ← nhật ký thay đổi của ứng dụng
├── Template_BaoCaoHieuNang.docx  ← file mẫu báo cáo (style, lề trang, header/footer)
├── config\settings.yaml          ← cấu hình chung (trọng số, ngưỡng, đường dẫn k6/JMeter, crawler…)
├── .streamlit\config.toml        ← cấu hình giao diện (cổng, chỉ truy cập từ máy này)
├── perftool\                     ← mã nguồn ứng dụng
│   ├── uc_import\                   (đọc file UC)
│   ├── crawler\                     (đăng nhập & quét web bằng Playwright)
│   ├── analysis\                    (ghép UC ↔ màn hình, chấm điểm, sinh kịch bản)
│   ├── scriptgen\                   (sinh script k6 / JMeter từ khuôn mẫu)
│   ├── runner\                      (chạy k6 / JMeter ở tiến trình nền)
│   ├── results\                     (đọc kết quả, tính chỉ số, nhận xét tự động)
│   ├── charts\                      (vẽ biểu đồ)
│   ├── report\                      (xuất báo cáo Word / Excel)
│   └── ui\                          (các màn hình 10 bước)
├── samples\                      ← web demo, file UC & tài khoản mẫu để chạy thử
├── tests\                        ← kiểm thử tự động của mã nguồn
├── .venv\                        ← môi trường Python riêng (setup.bat tạo, không cần sao lưu)
└── workspace\projects\<mã dự án>\   ← TOÀN BỘ DỮ LIỆU CỦA TỪNG DỰ ÁN
    ├── project.json      ← "hồ sơ" của dự án: thông tin bước 1, danh sách UC,
    │                        phân hệ & màn hình đã quét, điểm chấm, kịch bản đã
    │                        xác nhận, cấu hình tải, danh sách lượt chạy
    ├── input\            ← bản sao file UC đã import
    ├── crawl\            ← kết quả đăng nhập & quét web
    │   ├── discover.json, analysis.json   (phân hệ, màn hình, popup/tương tác, API đo được)
    │   ├── record.json                    (dữ liệu ghi thao tác thủ công – mục 3c)
    │   ├── auth.json, storage_state.json  (phiên đăng nhập đã lưu)
    │   └── screens\*.png                  (ảnh chụp màn hình)
    ├── runs\<mã lượt chạy>\   ← mỗi lượt test 1 thư mục
    │   ├── script.js / plan.jmx           (script k6 / JMeter đã sinh)
    │   ├── raw.csv / results.jtl          (dữ liệu thô từng request)
    │   ├── summary.json / html-report\    (tóm tắt của k6 / dashboard HTML của JMeter)
    │   ├── run.json, tool.log, jmeter.log (trạng thái, log)
    │   └── analysis.json, per_label.csv, ts_*.csv  (kết quả phân tích lưu sẵn)
    ├── charts\           ← toàn bộ biểu đồ PNG (kể cả overview_*)
    ├── reports\          ← báo cáo .docx / .xlsx đã xuất
    └── jobs\             ← trạng thái & log tác vụ nền (quét web, chạy test)
```

**Về dữ liệu dự án:**

- Mỗi lần *Tạo dự án mới*, ứng dụng tạo một thư mục trong `workspace\projects\` với mã = **ngày giờ tạo + tên dự án không dấu**, ví dụ `20260923-103402-chinh-quyen-so-tp-hcm-ntqlcqs`.
- Ô *Dự án* ở thanh bên trái được lấy bằng cách quét các thư mục có file `project.json` (dự án cập nhật gần nhất lên đầu). Không có cơ sở dữ liệu.
- **Sao lưu / chuyển máy:** chép thư mục dự án sang `workspace\projects\` của máy khác. Mật khẩu phải nhập lại (xem mục 5.1).
- **Xoá dự án:** thanh bên trái → *⚠️ Xoá dự án*, hoặc xoá thẳng thư mục dự án.

---

## 5. Ghi chú quan trọng

### 5.1. Mật khẩu và dữ liệu nhạy cảm

- Mật khẩu **không được lưu vào bất kỳ file nào** của ứng dụng.
  - Nếu tick *Ghi nhớ mật khẩu*: mật khẩu được lưu trong **Windows Credential Manager** (mục `PerfTool`). Xem/xoá tại *Control Panel → Credential Manager → Windows Credentials*.
  - Nếu không tick: mật khẩu chỉ giữ trong phiên làm việc; **khởi động lại ứng dụng phải nhập lại**.
  - Khi chạy test, mật khẩu được truyền cho k6/JMeter qua biến môi trường; file tạm của JMeter bị xoá ngay sau khi chạy.
- Thư mục `workspace` **có chứa dữ liệu nhạy cảm**: phiên đăng nhập, token (`crawl\storage_state.json`, `crawl\auth.json`, `project.json`), ảnh chụp màn hình, dữ liệu phản hồi của hệ thống, nội dung request (kể cả dữ liệu bạn nhập vào biểu mẫu khi ghi thao tác thủ công – `crawl\record.json`). **Không gửi thư mục `workspace` cho người ngoài** – chỉ gửi file trong `reports\`.

### 5.2. Nhiều tài khoản kiểm thử

- Khai báo ở **bước 1 → mục Tài khoản kiểm thử**; chọn cách dùng ở **bước 7 → Tài khoản dùng khi chạy test**.
- Mỗi người dùng ảo (VU) đăng nhập **1 lần** bằng một tài khoản, chia theo vòng: VU 1 → tài khoản 1, VU 2 → tài khoản 2, …, hết danh sách thì quay lại tài khoản 1. Ví dụ 50 VU và 10 tài khoản → mỗi tài khoản dùng chung cho ~5 VU.
  - **k6** chia đúng theo số thứ tự VU.
  - **JMeter** dùng *CSV Data Set* nên phân bổ **gần đều** (thứ tự các luồng lấy tài khoản phụ thuộc thời điểm khởi động).
- Bỏ chọn *"Tài khoản chính cũng tham gia chạy test"* nếu muốn dành tài khoản chính riêng cho việc quét web.
- Nút **🔑 Kiểm tra đăng nhập** gửi 1 request đăng nhập cho từng tài khoản (qua API đăng nhập nhận diện ở bước 3) để phát hiện tài khoản sai mật khẩu/bị khoá **trước** khi chạy test.
- File danh sách: CSV/TXT/Excel, dòng đầu có thể là tiêu đề (`username,password`, `Tên đăng nhập;Mật khẩu`, `Email,Password`…). Tải file mẫu bằng nút *Tải file mẫu*.
- Mật khẩu các tài khoản xử lý như tài khoản chính: không lưu vào file dự án; lưu trong Windows Credential Manager nếu tick *Ghi nhớ mật khẩu*. Khi chạy test, danh sách chỉ được ghi ra **file tạm** (`accounts.json` / `accounts.csv`) trong thư mục lượt chạy và **bị xoá ngay sau khi chạy**, kể cả khi bấm *Dừng*.
- Chỉ áp dụng với chế độ xác thực *"Mỗi VU đăng nhập 1 lần"*. Chế độ *"Dùng cookie/token phiên đã ghi nhận"* luôn dùng chung 1 phiên.

### 5.2a. Quét đủ màn hình, nút, popup (bước 3)

Nhiều màn hình không có link trên menu: popup Xem/Thêm mới/Sửa, các mục trong menu **⋮** của từng dòng (vd *Việc cần xử lý* → ⋮ → *Xử lý / Xin gia hạn / Phân công*), tab trong trang… Bước 3 có 2 cách bổ sung, nên dùng kết hợp:

**A. Tự quét tương tác** (tick ở mục 3b, chạy cùng nút *Phân tích … phân hệ*). Trên mỗi trang, ứng dụng lần lượt:

| Thao tác | Ghi nhận |
|---|---|
| Bấm từng **tab** chưa chọn (tối đa `crawler.max_tabs_per_page` = 4) | API gọi khi đổi tab |
| Bấm nút **Tìm kiếm** (1 lần) và sang **trang 2** của bảng | API tìm kiếm / phân trang |
| Bấm nút **Xem / Chi tiết / Thêm mới / Sửa** (tối đa 5, theo `crawler.popup_triggers`) | Popup: tiêu đề, số trường nhập, bảng, API |
| Mở **menu ⋮ / … / nút thả xuống** ở **dòng đầu tiên** của bảng và trên thanh công cụ (tối đa 3 menu), đọc danh sách mục, rồi bấm lần lượt **từng mục an toàn** (tối đa 8 mục/menu) | Mục mở popup → popup; mục chuyển trang → **trang con** (ghi xong tự quay lại); nhãn mọi mục → tiêu chí CRUD |

Biện pháp an toàn (vì chế độ này **có bấm nút** trên hệ thống thật – chỉ bật khi được phép):

1. **Không bấm nút nào bên trong popup** (Lưu, Gửi…); đóng popup bằng Esc / nút Đóng / tải lại trang.
2. **Không bấm nút, mục menu có nhãn nguy hiểm**: Xoá, Lưu, Gửi, Duyệt, Ký, Chuyển, Huỷ, Hoàn thành, Tiếp nhận, Trả lại, Khoá, Xuất, In, Tải… (`crawler.popup_blacklist`, so khớp nguyên từ – "In" không khớp nhầm "X**in** gia hạn"). Tab và chuyển trang không áp danh sách này vì chỉ đổi dữ liệu hiển thị.
3. **Bộ chặn ghi dữ liệu** (`crawler.write_guard`): nếu một thao tác vẫn phát sinh request ghi, request bị **huỷ ngay trong trình duyệt, không tới máy chủ**, nhưng vẫn được ghi lại (đánh dấu *bị chặn*) để sinh kịch bản test (mặc định tắt trong kịch bản). Quy tắc với request không phải GET:
   - Đoạn cuối đường dẫn bắt đầu bằng `Get…`, `Search…`, `List…`, `Find…`, `Filter…`, `Count…`, `Check…`, `Export…`… (`crawler.read_prefixes`) → cho qua (là truy vấn, vd `GetAll`, `GetForEdit`).
   - PUT / PATCH / DELETE, form POST cả trang, hoặc POST có đường dẫn chứa từ ghi (`create`, `update`, `delete`, `save`, `approve`, `assign`, `upload`, `complete`, `phancong`, `giahan`…, `crawler.write_keywords`) → **chặn**.
   - Còn lại → cho qua.

**B. Ghi thao tác thủ công** (mục **3c**, **không bắt buộc** – bỏ qua nếu bảng ở 3d đã đủ màn hình cần test) – bổ sung những gì tự quét chưa tới (popup nhiều bước, màn hình cần chọn dữ liệu trước, dòng khác dòng đầu…):

1. Chọn *Gán màn hình ghi được vào phân hệ* (hoặc để *Tự nhận diện theo URL*), *Trang mở đầu tiên*, mức *Bảo vệ dữ liệu khi ghi*:
   - *Chặn request ghi theo từ khoá* (khuyến nghị) – quy tắc như trên;
   - *Chặn mọi request không phải GET* – an toàn nhất, trừ API truy vấn (`read_keywords`, `PostData`, `ExecuteStore…`); một số màn hình tra cứu dùng POST có thể không hiện dữ liệu;
   - *Không chặn* – thao tác Lưu/Gửi/Duyệt của bạn sẽ **ghi dữ liệu thật**.
2. Bấm **🎥 Bắt đầu ghi** → trình duyệt mở, đã đăng nhập, góc dưới phải có thanh đỏ *● PerfTool đang ghi thao tác*.
3. Tự bấm qua các màn hình: mở menu ⋮ → Xử lý, Xin gia hạn, Phân công…, mở popup, đổi tab… Có thể bấm cả nút Lưu để ghi lại API lưu – với bộ chặn ghi đang bật, dữ liệu **không** được lưu thật.
4. Bấm **Kết thúc ghi** trên thanh đỏ (hoặc *⏹ Kết thúc ghi & lưu* trên PerfTool, hoặc đóng trình duyệt). Tối đa `crawler.record_max_minutes` = 60 phút mỗi lần.

Công cụ tự tách từng **màn hình** (khi URL đổi) và **popup** (khi hộp thoại mở), gán các API được gọi trong lúc đó cho đúng màn hình/popup. Màn hình trùng URL đã quét → bổ sung API/popup vào màn hình đó; URL mới → màn hình mới trong phân hệ có đường dẫn gần nhất (không có thì vào phân hệ *Ghi thao tác thủ công*); trang mới không gọi API nào không được tạo thành màn hình. Có thể ghi nhiều lần; dữ liệu lưu ở `crawl/record.json` của dự án, được **giữ lại khi phân tích lại 3b**, xem ở khung *🎥 Dữ liệu đã ghi* và xoá bằng *🗑 Xoá dữ liệu ghi thủ công*.

**Xem kết quả** ở mục **3d**: mỗi phân hệ ghi *N trang + M popup / trang con*; bảng *Popup / trang con / tương tác đã quét* liệt kê từng lần bấm (Loại, Nút / mục đã bấm, Mở ra, Trường nhập, Bảng, API, Tính màn hình, Nguồn: Tự quét / Ghi thủ công). Sau khi quét/ghi thêm, **chấm điểm lại ở bước 4**.

### 5.3. Cách chấm điểm độ phức tạp UC (bước 4)

`Điểm (0–100) = 100 × Σ(điểm tiêu chí × trọng số) ÷ Σ(trọng số)`, trong đó *điểm tiêu chí* = số liệu thô ÷ mức trần (tối đa 1).

| Tiêu chí | Cách đo |
|---|---|
| Số bước | Cột *Số bước* trong file UC → số giao dịch con (mẫu QĐ 671) → ước lượng từ mô tả |
| Số màn hình | Số trang ghép được với UC ở bước 3 (tối đa 3) **+ số popup / trang con** có biểu mẫu nhập liệu, bảng hoặc nội dung chi tiết trên các trang đó (từ tự quét tương tác hoặc ghi thao tác thủ công) |
| Số API/request | Số API riêng của các màn hình đó (đã bỏ API khung dùng chung cho mọi trang) |
| Thao tác CRUD | Số **loại** thao tác ghi (thêm mới, sửa, xoá, duyệt/ký, gửi/chuyển/giao, lưu/tải lên) + tối đa 2 điểm cho API ghi dữ liệu |
| Xử lý dữ liệu | Số **loại** thao tác xử lý (tìm kiếm/lọc, thống kê/báo cáo, xuất/nhập file, danh sách/phân trang, theo dõi/cảnh báo) + có bảng dữ liệu + bảng ≥ 50 dòng + phản hồi > 100 KB |
| Thời gian phản hồi | ~p90 thời gian phản hồi đo được khi quét ở bước 3 (1 lần đo – tham khảo) |

- **Trọng số** = mức độ quan trọng của tiêu chí, **do bạn đặt – ứng dụng không tự thay đổi**; tổng không cần bằng 1. Giá trị mặc định là quy ước của ứng dụng, không theo chuẩn bắt buộc. Mỗi dự án lưu trọng số riêng.
- Kéo trọng số hoặc đổi mức trần **chưa có tác dụng cho tới khi bấm *Tính điểm & đề xuất*** – ứng dụng hiện cảnh báo "có thay đổi chưa áp dụng", xem trước mức trần sẽ dùng, và sau khi bấm sẽ báo đã chấm lại bao nhiêu UC, top 3 thay đổi thế nào. Đổi mức trần chỉ thay đổi dòng *Mức trần đang dùng* và điểm trong bảng, không đổi trọng số.
- **Mức trần tự động** (mặc định): p90 của chính danh sách UC trong dự án, không nhỏ hơn một mức sàn → khoảng 10% UC đạt tối đa ở mỗi tiêu chí, tiêu chí nào cũng phân biệt được UC. **Mức trần cố định**: lấy trong `config\settings.yaml` (`scoring.caps`) – dùng khi cần so sánh điểm giữa các dự án.
- CRUD và Xử lý dữ liệu đếm **loại** thao tác, không đếm số lần từ khoá lặp lại – UC có mô tả dài không tự động được điểm cao. Từ khoá của từng loại chỉnh ở `scoring.operation_groups`. Nhận diện bằng từ khoá có thể nhầm (ví dụ "giao" trong "Thống kê giao việc" bị tính là thao tác giao/chuyển) – xem khung *Vì sao UC này được điểm như vậy?* để kiểm tra.
- **Popup / trang con tính là màn hình riêng** nếu có biểu mẫu nhập liệu, bảng dữ liệu hoặc nội dung chi tiết (Thêm mới, Sửa, Xem chi tiết, Xử lý, trang Phân công mở từ menu ⋮…); hộp thoại xác nhận, menu thả xuống, tab, Tìm kiếm, chuyển trang không tính màn hình (chỉ bổ sung API). Các màn hình này không có link trên menu nên chỉ được phát hiện khi bật *Tự quét tương tác* hoặc ghi thao tác thủ công ở bước 3 (mục 5.2a); API gọi khi mở cũng được đưa vào kịch bản test. Nhãn các mục trong menu ⋮ (kể cả mục không bấm như Xoá) được cộng vào tiêu chí CRUD.
- Hai UC bằng điểm: UC có mức độ phức tạp khai báo cao hơn xếp trên, rồi đến UC nhiều bước hơn. Không đề xuất hai UC dùng gần như cùng bộ màn hình hoặc bộ API (trùng > 60%) – vì vậy khi đề xuất theo phân hệ, có phân hệ nhận ít UC hơn số yêu cầu (xem khung *📊 Đề xuất theo phân hệ*); có thể tick thêm UC thủ công.
- Bảng kết quả có cột **Tên phân hệ** (theo file UC) và **Phân hệ trên web** (menu chứa màn hình đã ghép).
- Phía trên bảng có dòng tóm tắt (số UC, số phân hệ, số UC ghép được màn hình, số UC đề xuất / đang chọn), **thanh lọc** (theo Tên phân hệ, Phân hệ trên web, mã/tên UC, trạng thái đề xuất/chọn, đã ghép màn hình hay chưa, điểm tối thiểu) và **biểu đồ điểm** (Top 10/20/30/Tất cả, sắp theo điểm giảm dần, UC đề xuất tô màu cam). Bấm tiêu đề cột trong bảng để sắp xếp. Lọc chỉ ẩn bớt dòng – lựa chọn của UC bị ẩn được giữ nguyên; bấm *Lưu lựa chọn* trước khi đổi bộ lọc.
- Bảng kết quả có thể xem theo **Số liệu thô / Điểm 0–1 / Điểm đóng góp**. Báo cáo (mục 1.2) ghi lại trọng số và mức trần đã dùng.

### 5.4. Về kiểm thử hiệu năng

- **Luôn chạy Smoke Test trước** với 3–10 người dùng ảo để chắc chắn script đúng (đăng nhập được, các API trả về 200) rồi mới tăng tải.
- **Smoke Test không đạt nhưng Load Test / Stress Test lại đạt:** không được bỏ qua. Thường do *mẫu nhỏ* (vài lỗi trên ít request đã thành tỷ lệ lớn; p95 của ít mẫu dễ bị 1–2 request chậm kéo lên), *khởi động nguội* (Smoke Test chạy đầu tiên khi cache/kết nối CSDL chưa sẵn sàng – lỗi dồn ở đầu phiên), *sự cố tạm thời* của môi trường, hoặc *lỗi kịch bản/dữ liệu*. Xem mã lỗi và thời điểm lỗi ở bước 8, xử lý nguyên nhân rồi **chạy lại Smoke Test** cho tới khi sạch lỗi. Báo cáo tự nhận diện trường hợp này trong mục tổng quan và phần kiến nghị, kèm nguyên nhân khả dĩ tính từ số liệu thật.
- **Mức tải mục tiêu (số VUs) do người dùng đặt.** Nên lấy từ yêu cầu/cam kết của dự án (ví dụ "đáp ứng 1.000 người dùng đồng thời"), số liệu vận hành giờ cao điểm, hoặc ước tính: *tổng tài khoản × % đăng nhập giờ cao điểm × % đang thao tác cùng lúc*.
- Tên 5 loại kịch bản (Smoke Test, Load Test, Stress Test, Spike Test, Soak Test) theo cách gọi phổ biến của k6 và giáo trình ISTQB Performance Testing (Soak Test = *Endurance testing*). Cách tăng/giảm tải cụ thể (ví dụ các bậc Stress Test 50/100/125/150%) là quy ước của ứng dụng.
- **JMeter** chạy cùng hồ sơ tải với k6 (Stress Test/Spike Test theo bậc): mỗi bậc tăng tải là 1 Thread Group có độ trễ khởi động; riêng giai đoạn ramp-down JMeter dừng tải ngay, k6 giảm dần.
- **Lỗi HTTP 429 (Too Many Requests)** nghĩa là hệ thống đang **giới hạn tần suất** (thường theo tài khoản hoặc IP), không phải quá tải. Khi tất cả người dùng ảo dùng chung 1 tài khoản, lỗi này xuất hiện rất sớm. Muốn đo đúng năng lực: dùng **nhiều tài khoản test** (mục 5.2) nếu giới hạn theo tài khoản, hoặc đề nghị bên vận hành **nới giới hạn** cho IP máy test nếu giới hạn theo IP.
- k6 kết thúc với **mã thoát 99** khi kết quả vượt ngưỡng (threshold) – đây **không phải lỗi chạy**, dữ liệu vẫn đầy đủ.
- Thời gian phản hồi đo được khi *quét web* (bước 3) chỉ là 1 lần đo, dùng để tham khảo khi chấm điểm. Kết quả hiệu năng chính thức lấy từ k6/JMeter.
- Nếu tên menu khác hẳn tên phân hệ trong file UC (ví dụ "Kho tài liệu" ↔ menu "Chia sẻ File"), hãy ghép tay ở bước 4.

### 5.5. Xử lý sự cố thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `setup.bat` báo *Chưa cài Python* | Cài Python (mục 2.1), nhớ tick *Add to PATH*; tắt *App execution aliases* của Microsoft Store |
| `setup.bat` lỗi khi cài thư viện hoặc tải Chromium | Kiểm tra Internet / proxy của cơ quan; chạy lại `setup.bat` |
| Trình duyệt không tự mở | Tự mở `http://localhost:8501` |
| Báo cổng 8501 đang bận | Đã có một cửa sổ PerfTool khác đang chạy – dùng cửa sổ đó, hoặc đóng nó rồi chạy lại `run.bat` |
| Bước 6 báo chưa có k6 / JMeter | Cài theo mục 2.2 / 2.3; với JMeter khai báo `jmeter_path` trong `config\settings.yaml`; bấm *Dò lại công cụ* |
| JMeter chạy lỗi ngay | Kiểm tra `java -version`; xem `runs\<mã lượt>\jmeter.log` |
| Đăng nhập thất bại ở bước 3 | Kiểm tra URL đăng nhập, tài khoản; khai báo *CSS selector* ở phần *Tuỳ chọn đăng nhập nâng cao*; hoặc dùng chế độ tự đăng nhập |
| Bước 3 tìm thấy 0 phân hệ | Menu của hệ thống không dùng đường link thông thường – thêm tay phân hệ và URL vào bảng ở mục 3b |
| Bảng 3d thiếu popup / màn hình mở từ menu ⋮ | Kiểm tra đã tick *Tự quét tương tác* ở 3b rồi phân tích lại; nếu vẫn thiếu, dùng **3c Ghi thao tác thủ công** để tự bấm bổ sung |
| Log tự quét ghi *không bấm: …* | Mục menu có nhãn thuộc danh sách cấm (Xoá, Duyệt, Hoàn thành…) – cố ý không bấm để an toàn; nhãn vẫn được tính vào tiêu chí CRUD. Nếu cần API của thao tác đó, dùng 3c (bộ chặn ghi bật) |
| Khi ghi thủ công, màn hình tra cứu không hiện dữ liệu | Đang chọn mức *Chặn mọi request không phải GET* – API tra cứu dùng POST bị chặn. Ghi lại với mức *Chặn request ghi theo từ khoá* |
| Log ghi *🛡 Đã chặn request ghi dữ liệu* | Bình thường: bộ chặn ghi đã huỷ request ghi, dữ liệu hệ thống không thay đổi; request vẫn được đưa vào kịch bản (mặc định tắt ở bước 5) |
| Ghi thủ công: lỡ đóng trình duyệt / bấm ⏹ Dừng | Phần đã ghi vẫn được lưu và gộp vào 3d; bấm *🎥 Bắt đầu ghi* để ghi tiếp (các lần ghi được cộng dồn) |
| Script trả nhiều lỗi 401 | Phiên/token không hợp lệ: ở bước 3 tick *Đăng nhập lại (bỏ phiên đã lưu)* rồi quét lại; ở bước 5 bấm *Tạo lại từ dữ liệu crawl* |
| Nhiều lỗi 429 | Giới hạn tần suất – xem mục 5.4; thêm tài khoản kiểm thử (mục 5.2) |
| Bước 7 báo *tài khoản chưa có mật khẩu* | Mật khẩu chỉ giữ trong phiên và ứng dụng vừa khởi động lại – nhập lại danh sách ở bước 1 với *Ghi nhớ mật khẩu* được tick |
| *Kiểm tra đăng nhập* báo HTTP 401/403 | Sai tài khoản/mật khẩu hoặc tài khoản bị khoá – sửa rồi *Thêm / cập nhật tài khoản* lại (dòng mới ghi đè mật khẩu cũ) |
| Nút *Kiểm tra đăng nhập* bị mờ | Chưa nhận diện API đăng nhập – chạy bước 3 (*Đăng nhập & phát hiện phân hệ*) trước |
| Sau khi khởi động lại phải nhập lại mật khẩu | Tick *Ghi nhớ mật khẩu* ở bước 1 |
| Giao diện hiện dòng *File change – Rerun* | Mã nguồn vừa được cập nhật; bấm *Rerun* (hoặc *Always rerun*) |
| Bước 7 đều *Đã chạy xong* nhưng bước 8 có lượt *Không đạt* | Bình thường: *Đã chạy xong* chỉ là trạng thái chạy, bước 8 mới so ngưỡng. Mở chi tiết lượt ở bước 8 để xem trượt vì p95 hay vì tỷ lệ lỗi (thường gặp: lỗi 429) |
| Cửa sổ Chrome ở bước 3 nhỏ, không thấy hết chức năng | Kiểm tra `crawler.window_size: maximized` trong `config\settings.yaml` |
| Ổ đĩa đầy | Bước 7 → *🗑 Dọn lịch sử* tick *Xoá luôn thư mục kết quả trên đĩa* (mục 5.7), hoặc xoá dự án không dùng |

### 5.6. Cấu hình nâng cao (`config\settings.yaml`)

| Nhóm | Ý nghĩa |
|---|---|
| `tools` | Đường dẫn k6 / JMeter (để trống = tự tìm) |
| `crawler` | Thời gian chờ tải trang, số màn hình quét tối đa mỗi phân hệ, độ sâu duyệt link, từ khoá link cần bỏ qua, quét tương tác (`max_popups_per_page`, `popup_triggers`, `popup_blacklist`, `max_menus_per_page`, `max_menu_items`, `max_tabs_per_page`, `search_words`), bộ chặn ghi (`write_guard`, `write_keywords`, `read_prefixes`, `weak_read_prefixes`, `read_keywords`), thời gian ghi thủ công tối đa `record_max_minutes`, kích thước cửa sổ trình duyệt `window_size` (`maximized` = toàn màn hình – mặc định, hoặc cố định dạng `1440x900`; khi chạy ẩn dùng 1920x1080) (chế độ chạy ẩn trình duyệt chỉnh ở bước 1) |
| `scoring` | Trọng số mặc định 6 tiêu chí, số UC đề xuất, nhóm từ khoá theo loại thao tác (`operation_groups`), chế độ mức trần (`caps_mode`), mức trần cố định (`caps`) và mức sàn cho mức trần tự động (`cap_floor`) |
| `report.template` | File mẫu báo cáo Word (mặc định `Template_BaoCaoHieuNang.docx`) |

Sửa xong cần đóng và mở lại ứng dụng (`run.bat`).

### 5.7. Theo dõi tiến trình và quản lý lịch sử lượt chạy

**Khung tiến trình** (bước 3: *📜 Phát hiện phân hệ*, *📜 Phân tích phân hệ*; bước 7: *📜 Tiến trình chạy test*):

- Là nhóm **thu gọn / mở rộng** (bấm vào tiêu đề), tự mở khi đang chạy.
- Tiêu đề ghi trạng thái và tự cập nhật mỗi 2 giây, ví dụ: *📜 Tiến trình chạy test – ⏳ Đang chạy · 3/5 · bắt đầu 2026-09-23 15:58:09* (3/5 = đang chạy lượt thứ 3 trong 5 lượt). Khi xong có thêm *· kết thúc …*.
- Bên trong khung có nút **⏹ Dừng** (chỉ hiện khi đang chạy) và log, **dòng mới nhất nằm trên cùng**.

**Trạng thái lượt chạy** (bảng *Lịch sử lượt chạy*, bước 7):

| Trạng thái | Ý nghĩa |
|---|---|
| 🕓 Chờ chạy | Đã sinh script, chưa chạy |
| ⏳ Đang chạy | k6/JMeter đang chạy (cột *Tiến độ* cập nhật theo %) |
| ✅ Đã chạy xong | Chạy hết thời gian cấu hình và có file kết quả. **Không có nghĩa là Đạt** – bước 8 mới so ngưỡng |
| ❌ Lỗi | Công cụ lỗi khi chạy (xem log) |
| ⏹ Đã dừng | Bị bấm *Dừng* giữa chừng |

**Xoá lượt chạy:**

- **Xoá từng lượt:** tick ô đầu dòng trong bảng (chọn được nhiều dòng) → khung *Đã chọn N lượt* hiện ra ngay dưới bảng → **🗑 Xoá N lượt**. Bỏ tick để huỷ.
- **🗑 Dọn lịch sử:** xoá theo trạng thái (chọn một hoặc nhiều trạng thái, mặc định *Lỗi* và *Đã dừng*) hoặc **Xoá tất cả** (phải tick *Xác nhận xoá tất cả*). Không dùng được khi đang có tiến trình chạy test.
- Tuỳ chọn **Xoá luôn thư mục kết quả trên đĩa**: không tick = chỉ gỡ khỏi danh sách, thư mục `runs\<mã lượt>` vẫn còn; tick = xoá hẳn (script, log, dữ liệu thô), **không khôi phục được**.
- Không xoá được lượt đang chạy / chờ chạy trong tiến trình hiện tại.
- **Ảnh hưởng:** bước 8, 9, 10 chỉ tính các lượt còn trong lịch sử – lượt đã xoá **biến mất khỏi thống kê, biểu đồ và báo cáo xuất sau đó**. Báo cáo đã xuất trước (thư mục `reports\`) không bị ảnh hưởng. Chỉ nên xoá lượt chạy thử, chạy lỗi, chạy trùng.

**Các số liệu đầu bước 8:**

| Chỉ số | Cách tính |
|---|---|
| Kịch bản đã chạy | Số lượt *Đã chạy xong* còn trong lịch sử bước 7 |
| Đạt / Không đạt | Đạt khi **p95 ≤ ngưỡng p95 và tỷ lệ lỗi ≤ ngưỡng lỗi** (cấu hình ở bước 7); trượt 1 trong 2 là Không đạt |
| Tổng request | Cộng số request của các lượt: k6 đếm từ `raw.csv` (mỗi request 1 dòng `http_req_duration`), JMeter đếm từ `results.jtl` (mỗi sampler 1 dòng, bỏ dòng tổng của transaction). Gồm cả request đăng nhập và request lỗi |


### 5.8. Thu số liệu tài nguyên máy chủ (tuỳ chọn – cấu hình ở bước 6, xem ở bước 8)

**Số liệu thu được** (mỗi máy chủ, lấy mẫu mặc định 5 giây/lần):

| Chỉ số | Nguồn (Windows Performance Monitor) |
|---|---|
| CPU (%) | `\Processor(_Total)\% Processor Time` |
| RAM (%) | 100 − `\Memory\Available MBytes` ÷ tổng RAM (tổng RAM tự lấy khi kiểm tra kết nối; không có thì dùng `% Committed Bytes In Use`) |
| Ổ đĩa bận (%) | 100 − `\PhysicalDisk(_Total)\% Idle Time` |
| Đọc / ghi đĩa (MB/s) | `\PhysicalDisk(_Total)\Disk Read/Write Bytes/sec` |
| Mạng (MB/s) | tổng `\Network Interface(*)\Bytes Total/sec` của các card mạng |
| Kết nối CSDL | kết quả câu truy vấn đếm phiên (xem dưới) |

**Cách lấy số liệu Windows Performance Monitor** (cột *Cách lấy* trong bảng máy chủ):
- `ssh` – PerfTool đăng nhập SSH vào máy chủ và chạy lệnh `typeperf` (có sẵn trên Windows) trong suốt thời gian test. Máy chủ cần bật **OpenSSH Server**; tài khoản nên thuộc nhóm *Performance Monitor Users* (không cần quyền quản trị).
- `local` – đo chính máy đang chạy PerfTool, dùng để chứng minh máy tạo tải không bị quá tải.
- `file` – không có SSH: quản trị viên chạy các lệnh `logman` hiện ở mục *Máy chủ không có SSH – ghi log Performance Monitor* (bước 6, khối 6b) (tạo bộ ghi log → `logman start` trước khi test → `logman stop` sau khi test). Sau khi test, tải file `.csv` / `.blg` lên ở **bước 8** (khung *📥 Tải file Performance Monitor lên* trong mục Tài nguyên máy chủ theo lượt chạy). Giờ trong file là giờ máy chủ, cần đồng bộ giờ với máy chạy test.

**Zabbix / Grafana-Prometheus:** các hệ thống này tự lưu lịch sử. Sau mỗi lượt chạy, PerfTool gọi API lấy số liệu đúng khung giờ của lượt đó. Nếu thiếu, bấm *🔄 Lấy lại số liệu Zabbix/Prometheus*. Item key (Zabbix) và câu PromQL có mẫu sẵn và sửa được.
> Phần Zabbix và Prometheus chưa được thử trên hệ thống thật. Lần đầu dùng hãy bấm *Lưu & kiểm tra*.

**Kết nối CSDL:** chọn loại CSDL (SQL Server, PostgreSQL, MySQL, Oracle), nhập máy chủ, cổng, tên CSDL, tài khoản chỉ đọc. Câu truy vấn mặc định:

| CSDL | Câu truy vấn | Quyền cần có |
|---|---|---|
| SQL Server | `SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1` | VIEW SERVER STATE |
| PostgreSQL | `SELECT COUNT(*) FROM pg_stat_activity` | pg_monitor |
| MySQL | `SELECT COUNT(*) FROM information_schema.PROCESSLIST` | PROCESS |
| Oracle | `SELECT COUNT(*) FROM v$session WHERE type = 'USER'` | SELECT trên V$SESSION |

Có thể sửa câu truy vấn. Câu mới phải trả về 1 số, và **chỉ được là 1 câu SELECT/WITH/SHOW**: PerfTool từ chối mọi câu có INSERT/UPDATE/DELETE/DROP/EXEC…

**An toàn:**
- Mọi thao tác chỉ **đọc** số liệu, không cài gì và không thay đổi gì trên máy chủ.
- Mật khẩu SSH/CSDL/token lưu như mật khẩu đăng nhập (Windows Credential Manager hoặc chỉ trong phiên). Khi chạy test, chúng được truyền cho tiến trình thu số liệu qua biến môi trường, không ghi ra file.
- Số liệu lưu tại `workspace\projects\<dự án>\monitor\*.csv`. Nhật ký thu thập ở `monitor\collector.log`.

**Số liệu của một lượt chạy** = các mẫu có thời điểm nằm trong khung giờ bắt đầu → kết thúc của lượt đó. Vì vậy các lượt chạy **trước khi bật** thu số liệu không có số liệu Performance Monitor. Muốn có, phải chạy lại test hoặc tải file log ghi được đúng lúc đó lên.
---

## 6. Gỡ cài đặt

1. Đóng ứng dụng.
2. Sao lưu `workspace\projects\` nếu cần giữ dữ liệu.
3. Xoá thư mục ứng dụng.
4. (Tuỳ chọn) Xoá mật khẩu đã ghi nhớ trong *Credential Manager* (các mục bắt đầu bằng `PerfTool`) và thư mục trình duyệt Playwright `%LOCALAPPDATA%\ms-playwright`.
