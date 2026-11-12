# Nhật ký thay đổi – PerfTool

## 2026-11-12 – Hỗ trợ kiểm thử ứng dụng WinForms (thư mục App_TestHieuNang_WinForms)

Bản sao của PerfTool mở rộng để kiểm thử **ứng dụng WinForms/desktop gọi API máy chủ** (không điều khiển giao diện desktop:
tải được tạo bằng k6/JMeter phát lại các lời gọi API đã ghi). Dự án Web cũ chạy nguyên như trước.
- **Loại dự án** (`Project.kind` = `web` | `winforms`, chọn ở bước 1). WinForms: ô URL là "URL máy chủ API".
- **Bước 3 WinForms** (`ui/steps/s03_winforms.py`): (a) *proxy ghi* – mở mitmproxy tại cổng chọn, người dùng đánh dấu từng
  màn hình trên PerfTool khi thao tác ứng dụng, tuỳ chọn mở kèm file .exe (đặt `HTTP_PROXY`); (b) *nhập HAR* (Fiddler, Charles,
  mitmproxy, DevTools) – tách màn hình theo khoảng nghỉ, đổi tên sau; (c) bảng đổi tên / xoá màn hình đã ghi.
- **Bộ chặn ghi ở proxy** (`crawler/mitm_addon.py`): request ghi (Lưu/Xoá/Duyệt…) trả 403 ngay tại proxy, không tới máy chủ,
  vẫn được ghi nhận và tắt sẵn trong kịch bản; dùng chung quy tắc `crawler/guard.py` với Web.
- **Quy đổi "màn hình" từ API** (`crawler/winforms.py`): trường nhập = trường lá trong body ghi + tham số lọc; bảng/dòng = phản
  hồi dạng danh sách; ô chọn = danh sách nhỏ; thao tác CRUD theo method/đường dẫn. Kết quả ghi vào `crawl/record.json` (cùng định
  dạng ghi thủ công của Web) rồi `merge_winforms()` gộp → bước 4–10 dùng chung, không đổi.
- **Xác thực:** nhận diện request đăng nhập (mẫu body `{{USERNAME}}/{{PASSWORD}}`, đường dẫn token) và `Authorization` từ lưu
  lượng; mật khẩu/OTP được che, `Authorization` chỉ ở `crawl/auth.json`.
- `build_scenario` bỏ bước "Mở màn hình" (URL ảo) với dự án WinForms – chỉ phát lại API.
- Test: `tests/test_winforms.py` (6 test; tổng 516). Đã chạy thử đầu-cuối: proxy thật + demo server + client giả lập → 2 màn
  hình, request `POST /api/tasks/save` bị chặn (server ghi nhận 0 lần ghi), không có mật khẩu thật trong file.
- **Giới hạn:** chỉ ghi được HTTP(S) (REST/SOAP/WCF basicHttp/gRPC-web); không hỗ trợ WCF net.tcp, .NET Remoting, kết nối thẳng
  CSDL, ứng dụng có certificate pinning. Số trường nhập/bảng là ước lượng từ API, không phải số điều khiển thật.
  Chưa chạy trên một ứng dụng WinForms thật.

## 2026-10-02 (đợt 2) – Sửa lỗi theo báo cáo review 2026-10-02

- **Bật sẵn theo whitelist (bước 5):** request chia 3 loại *đọc / ghi / chưa rõ*; chỉ request chắc chắn là đọc được bật.
  GET trỏ tới 1 bản ghi (`?id=…`) mà tên không phải truy vấn (`/api/notify/ack?id=1`, `/api/tasks/pin?id=1`) là *chưa rõ*
  → tắt, liệt kê ở 5a để người dùng xem lại. Bước "Mở màn hình" của trang mà mở ra là ghi (`/inbox/read-all`,
  `/ho-so/xu-ly?id=1`) cũng tắt; trang danh sách `/viec-can-xu-ly`, `/HoSo/XuLy`, trang form `/VanBan/ThemMoi` vẫn bật.
  `GET …/TokenAuth/LogOut` không còn được bật (load test tự đăng xuất). Đối chiếu dữ liệu quét thật của 3 dự án: không API
  đọc nào bị tắt nhầm.
- **Bộ chặn ghi khi quét:** thêm động từ `ack, pin, touch, open, visit, follow, like, vote, star, mark, close, hide…`;
  tải trang mặc định mức *strict* (`crawler.page_load_guard`) nên POST ghi nhận lượt xem lúc tải trang bị huỷ; link menu
  có tác dụng phụ không được mở; menu ⋮ không bấm Ghim / Theo dõi / Đóng việc / Like. Server bẫy: 7 → 0 request ghi lọt.
- **Không còn chặn nhầm** GET đọc có từ ghi trong cụm danh từ: `extend-info`, `change-log`, `send-history`,
  `gia-han/danh-sach`, `loai-xu-ly`, cờ lọc `isCreate=false`, giá trị lọc `TinhTrangGiaHan=DangGiaHan`, `?d=<epoch>`.
- **Mật khẩu/OTP không bao giờ được lưu trong request ghi nhận:** body có trường mật khẩu/OTP/secret hoặc chứa mật khẩu đã
  biết được che `***` (vd đăng nhập lại giữa lúc ghi thủ công 3c, form đổi mật khẩu), request đánh dấu nhạy cảm và không
  bao giờ bật sẵn; header phiên (`x-csrf-token`, `x-xsrf-token`…) không lưu. Dự án cũ được làm sạch khi mở.
- **Cookie / `Authorization` chỉ lưu ở `crawl/auth.json`** (cùng `storage_state.json`), không còn trong `project.json`,
  `discover.json`, `analysis.json`; dự án cũ tự chuyển khi mở. Cảnh báo khi tải script có nhúng cookie/token.
- **Mã UC có ký tự cấm của Windows** (`UC:01`, `A"B`) không còn làm hỏng bước sinh script.
- **TLS mặc định kiểm tra chứng chỉ;** tuỳ chọn "Bỏ qua kiểm tra chứng chỉ HTTPS" theo từng dự án ở bước 1 (có cảnh báo),
  thông báo rõ khi gặp chứng chỉ tự ký. **SSH:** khoá máy chủ chỉ được tin cậy khi bấm "Kiểm tra kết nối" ở bước 6 (lưu
  `workspace/known_hosts`, hiện vân tay SHA256); máy lạ hoặc khoá bị đổi → từ chối.
- JMeter: request mất phiên mang mã `SESSION_LOST` như k6.

## 2026-10-02 – Sửa lỗi theo báo cáo review 2026-10-01

### An toàn dữ liệu
- **GET ghi dữ liệu** (`/api/hoso/markread?id=1`, `/ho-so/xu-ly?id=1`, `Handler.ashx?action=delete`, `?markSeen=1`): GET do
  trang gọi bằng JS có từ ghi bị **chặn khi quét** và **tắt mặc định ở bước 5** (trước luôn bật → load test lặp lại N VU). Link menu
  trỏ vào 1 bản ghi cụ thể có từ ghi không được mở (trang danh sách `/HoSo/XuLy` vẫn quét). Cảnh báo ở 5a liệt kê cả GET.
  Tắt bằng `crawler.block_get_writes: false`. `POST …/view` (ghi nhận đã xem) không còn được coi là truy vấn.
- Bộ chặn ghi **fail-closed**: lỗi khi xét request → huỷ request (trước cho qua). Menu ⋮ không bấm thêm: Archive, Mark as read,
  Resend, Reopen, Undelete, Dismiss, Chấp nhận, Đồng bộ…
- **Đăng nhập thủ công không còn lưu mật khẩu/OTP thật** vào `auth.json`/`project.json`/script: mẫu body thay theo **tên trường**
  (username/password/matkhau…), OTP/CAPTCHA để trống; không gỡ được mật khẩu → không lưu mẫu (dùng cookie/header tĩnh).
- Mẫu body đăng nhập đúng với tài khoản kiểu `user01/user01`, `admin123/admin`, mật khẩu trùng số khác trong body.
- Mật khẩu cả nhóm tài khoản (`PERFTOOL_ACCOUNTS`) và mật khẩu máy chủ không còn lọt sang môi trường của k6/JMeter/collector.
- Xoá dự án xoá luôn mật khẩu đã lưu trong Windows Credential Manager.
- Tuỳ chọn `security.skip_tls_verify` (crawler, kiểm tra đăng nhập, k6) và `security.ssh_auto_add_host` (SSH monitor);
  mặc định giữ như cũ cho môi trường kiểm thử dùng chứng chỉ tự ký.
- Bộ lọc SQL chỉ-đọc chặn thêm `pg_read_binary_file`, `pg_ls_dir`, `dblink_*`, `OPENROWSET`, `pg_sleep`, `setval`…

### Đúng đắn kết quả
- **Mất phiên = lỗi:** request bị chuyển về trang đăng nhập được tính là lỗi (`SESSION_LOST`) ở cả k6 (metric
  `perftool_session_lost`) và JMeter (assertion URL); trước HTTP 200 của trang login được tính "Đạt".
- **JMeter chạy cùng hồ sơ tải với k6** (Stress 50→150% theo bậc, Spike…) bằng nhiều Thread Group có độ trễ; báo cáo ghi đúng thời
  lượng theo bậc. JMeter không ghi bước redirect con (`…-0`, `…-1`) thành request riêng (trước bị đếm trùng).
- Công cụ thoát lỗi (mã ≠ 0, k6 ≠ 99) → lượt **Lỗi**, kể cả khi có vài dòng dữ liệu; file chỉ có dòng tiêu đề không tính là dữ liệu.
- Đổi ngưỡng p95 riêng của UC ở bước 5 sau khi chạy → bước 8–10 tự tính lại. Dòng JTL cụt bị bỏ (trước thành 1 lỗi của UC rỗng).
- k6: threshold theo khoá UC an toàn (mã có `,` `}` không phá script), chỉ tính request nghiệp vụ (bỏ LOGIN) như bước 8;
  `discardResponseBodies` (soak dài không cạn RAM). JMeter: mật khẩu có `"`/`\` không phá body JSON; header token không gắn lên LOGIN.

### Ổn định
- Xoá ô Think time ở bước 5 không còn làm hỏng script và **làm dự án biến mất**; dự án có `project.json` hỏng được báo ở thanh bên.
- Mã UC trùng được khử khi import/sửa (`UC-002 (2)`) và cảnh báo ở bước 2; bước 5 không còn lỗi khoá trùng.
- Tái sử dụng PID sau khi khởi động lại máy: không còn báo "đang chạy" sai hay nút Dừng diệt nhầm tiến trình khác.
- `samples/demo_server.py` chạy được trên Python 3.10/3.11.
- Thời lượng: từ chối `1m30`, `-5m`, `0` (giữ tải), chuỗi có xuống dòng; `500ms` làm tròn lên 1s. Stress với 1 VU không còn bậc 0 VU.
- Đọc file tài khoản: dấu phân cách theo dòng đầu (`|`, `;`, tab), mật khẩu chứa dấu phân cách không bị cắt.
- Import UC: sheet trống báo lỗi rõ ràng; dòng nhóm "2. Phân hệ…" nhận đúng khi bật tự điền phân hệ; mã/tên UC chuẩn hoá NFC.

## 2026-09-30 – Sửa lỗi từ đợt kiểm thử 5 agent

### An toàn dữ liệu (bước 3 → 5 → 7)
- **Request đã bị bộ chặn ghi huỷ khi quét không còn được bật sẵn trong kịch bản test.** Trước đây
  `POST /api/tasklist/delete/5` (có "list" trong đường dẫn) bị coi là truy vấn → load test sẽ xoá dữ liệu thật × số VU.
- **Bộ chặn ghi viết lại** (`crawler/guard.py`, `crawler.write_keywords` trong settings):
  - xét **đoạn cuối có nghĩa** của đường dẫn (bỏ mã bản ghi `/5`, GUID) **và query string** (`Handler.ashx?action=delete`);
  - tách từ theo `- _ /` và chữ hoa: `CheckAndApprove` là ghi (trước bị cho qua vì bắt đầu bằng "check"),
    `GetForEdit` / `GetUsersByRole` vẫn là đọc;
  - thêm động từ tiếng Việt không dấu: *cập nhật, trình, gửi, huỷ, ký số, trả lại, thu hồi, từ chối, xác nhận, ban hành, xử lý,
    chuyển xử lý, đánh dấu đã xem…*; cú pháp `"xoa*"` (từ bắt đầu bằng), `"=read"` (nguyên từ) để tránh khớp nhầm (guid, thread);
  - có từ ghi → **chặn ở mọi mức**; mức *strict* không còn lỏng hơn mức *keyword* (`/api/TaskList/Remove` trước lọt ở strict).
  - Đối chiếu 102 API thật của NTQLCQS: không API đọc nào bị đổi sang "tắt".
- **Bộ chặn ghi luôn bật khi crawler tự điều hướng** (cả 3a dò menu và 3b quét không tick tương tác); khi **bấm thử** ở chế độ
  *Tự quét tương tác* tự chuyển mức **strict** (chỉ cho qua request mang tên truy vấn GetAll/Search/…).
- **Danh sách từ cấm nút/menu** không còn bị vượt qua: chuẩn hoá NFC phía trình duyệt, `&nbsp;`, tên icon `delete_forever`, so trên
  **nhãn đầy đủ** (trước bị cắt 80 ký tự làm mất `title="Xóa"`), mục menu so cả `title`/`aria-label`.
- **Crawler không mở link "Duyệt / Hoàn thành / Thoát / LogOff"** trong nội dung trang (nhiều hệ thống cũ ghi dữ liệu bằng GET);
  link đăng xuất (`/Account/LogOff`, nhãn "Đăng xuất", "Thoát") không còn bị nhận là phân hệ. Mất phiên giữa chừng → tự đăng nhập lại.
- **Bước 7:** bấm *💾 Lưu cấu hình* khi đang có lượt chờ chạy → **tự sinh lại script** theo cấu hình mới; script sinh theo cấu hình
  cũ bị cảnh báo và khoá nút *Chạy test* (trước đổi VU 10 → 2 vẫn chạy 10 VU).
- **JMeter nhiều tài khoản:** VU thứ i dùng đúng tài khoản thứ i (JSR223 đọc `accounts.csv` 1 lần + bộ đếm chung), thay CSV Data Set
  (đọc 1 dòng mỗi vòng lặp → có tài khoản dùng lặp, có tài khoản không dùng).

### Bước 3 – Quét
- So tên máy chủ không phân biệt hoa thường, bỏ cổng mặc định và `www.` (nhập `LocalHost` trước ra 0 phân hệ).
- Khử trùng màn hình: `/list`, `/list/`, `/list#top` là 1 màn hình; link trong nội dung `detail?id=1,2,3…` chỉ mở 1 đại diện
  (trước chiếm hết giới hạn trang và làm lệch việc lọc API khung). Trang từ menu vẫn giữ query (`report?id=1` ≠ `report?id=2`).
- Đổi tài khoản / máy chủ ở bước 1 → không dùng lại phiên đăng nhập cũ (`storage_state.meta.json`).
- Bắt được request đăng nhập cả khi trang chuyển hướng ngay; form đăng nhập hiện chậm vẫn đăng nhập được.
- URL gợi ý `/x` tính từ **gốc máy chủ** (trước ghép sau đường dẫn của URL hệ thống → 404); quét **đủ mọi URL gợi ý** (trước chỉ 5).
  Áp dụng cả cho URL phân hệ nhập tay ở 3b.
- Trang HTTP ≥ 400 được đánh dấu lỗi; thời gian tải trang bỏ khoảng chờ 500 ms của networkidle; tiêu đề khung tiến trình hiện
  "2/3 phân hệ".

### Bước 3c – Ghi thủ công & gộp kết quả
- Tab mới: gắn bộ ghi request ngay khi tab mở (trước mất API lúc tab tải trang).
- Bấm Back / tự gõ URL không còn bị ghi thành "Trang con" mở bằng cú bấm cũ.
- Trang con đã được tự quét tính màn hình → ghi thủ công lại không tính thêm 1 màn hình; màn hình đã có ở phân hệ khác → gộp
  vào đó dù chọn "Gán vào phân hệ X"; trang con mới rơi vào phân hệ của màn hình mở ra nó.
- `record.json` sai cấu trúc không làm hỏng 3b; file hỏng được sao lưu `record.json.bak-<giờ>` thay vì bị ghi đè.
- Bỏ tick phân hệ *Ghi thao tác thủ công* được giữ sau khi gộp lại. Chú thích bảng lần ghi: "Tick ô vuông ở đầu dòng".

### Bước 4 – Ghép UC & chấm điểm
- Ghép UC ↔ màn hình: từ ghép tiếng Việt (*thông báo ≠ báo cáo ≠ cảnh báo*, *quản trị*), bỏ từ thao tác chung (*tìm kiếm, tra
  cứu…*), bỏ tiêu đề lặp trên nhiều trang ("Thông tin tìm kiếm", "CHÍNH QUYỀN SỐ"), cộng thêm chữ trên URL
  (`viec-cho-phe-duyet`). Trên NTQLCQS: hết ghép nhầm UC báo cáo/cảnh báo → "Thông Báo", "Tìm kiếm nhanh văn bản" → Giao việc,
  UC-093…097 → "Quản trị Chuyên mục"; thêm ghép đúng UC-011/017/018.
- Đếm loại thao tác theo **nguyên từ** trên văn bản bỏ dấu: không còn tính "giao diện" là giao việc, "chương trình" là trình,
  "đề xuất" là xuất file, "address" là add; chữ NFD / không dấu cho cùng kết quả. File UC nhập vào được chuẩn hoá NFC.
- Đề xuất theo phân hệ: chống trùng bộ API chỉ so trong cùng phân hệ (trước 1 API khung chặn hết UC phân hệ khác); "trùng > 60%"
  đúng như chú thích (trước loại cả khi trùng đúng 60%).
- Ô *Số UC đề xuất*, *Cột tiêu chí hiển thị*, ghép tay không còn lẫn giữa các dự án; biểu đồ 4b không còn chồng thanh;
  *Vì sao UC này được điểm* ghi rõ điểm cộng; *Nổi bật* bỏ tiêu chí trọng số 0; *Lưu lựa chọn* cập nhật ngay thanh bên.

### Báo cáo
- Mục **1.3 Cấu hình** lấy theo **các lượt đưa vào báo cáo** (vd "Smoke Test / Load Test", "5 / 9 VU"), không lấy cấu hình 7a hiện tại.

### Lỗi mức Thấp
- **Bước 3:** bấm ⏹ Dừng khi đang quét vẫn giữ các phân hệ đã quét xong (`analysis.json` lưu dần sau mỗi phân hệ, ghi nguyên tử);
  quét xong thanh bên hiện ✅ ngay; đọc thêm menu nằm trong iframe/frameset.
- **3c:** chia request giữa màn hình cũ/mới theo **lúc bấm** (API tải trang khi mở popup ngay, API của popup trước khi đóng không
  còn bị gán sang màn hình kế tiếp); nhãn nút chỉ có icon không còn lặp chữ ("delete_forever xem delete_forever" → "xem").
- **Bước 4:** trọng số / số liệu NaN, âm (sửa tay settings.yaml / project.json) được coi là 0, không làm hỏng tổng điểm.
- **Bước 8–10:** lượt chạy gộp ghi rõ số VU dành cho từng UC ("2 (lượt chạy gộp: tổng 6 VU chia cho các UC)"), nhận xét không còn
  ghi tổng VU của cả lượt như của riêng UC; biểu đồ *So sánh p95* > 8 kịch bản chuyển sang thanh ngang, không chồng chữ; bảng
  tiêu chí chấm điểm trong báo cáo dùng trọng số mặc định khi dự án chưa lưu trọng số (trước hiện 0%).
- Test JMX ký tự đặc biệt cập nhật theo cách escape mới; hết `SyntaxWarning` trong `tests/test_runner.py`.

### Kiểm chứng trên localhost (bản sao riêng, không đụng dữ liệu dự án)
- Quét có tự quét tương tác trên trang "đối nghịch" của agent kiểm thử: máy chủ nhận **0 request ghi** (trước sửa: 4).
- JMeter Smoke 5 VU, 6 tài khoản: 5 VU đăng nhập bằng 5 tài khoản khác nhau (admin, user01…04); không còn file tạm chứa mật khẩu.
- Chấm lại dữ liệu NTQLCQS (bản sao): 32 UC ghép màn hình, top 3 UC-024 / UC-022 / UC-015 (trước: UC-079/080/081 ghép nhầm);
  biểu đồ 4b hiển thị đủ nhãn.

Test: `tests/test_fixes_20260930.py`, `tests/edge/test_edge_step3_qa.py`, `tests/edge/test_edge_step4_props.py` (tổng 414 test).

## 2026-09-25

### Bước 3c – Ghi thủ công nhận ra mục menu ⋮ đã bấm
- Trước đây bộ ghi chỉ tách màn hình/popup, không biết người dùng bấm nút nào: popup ghi thủ công luôn có *Nút / mục đã bấm =
  (ghi thủ công)*, trang con mở từ menu ⋮ (vd *Phân công*) thành màn hình rời, không gắn với màn hình danh sách.
- Nay bộ ghi theo dõi từng cú bấm (nút ⋮ / … trên dòng bảng, mục trong menu vừa mở, nút thường) và đặt tên giống khi tự quét:
  **"dòng 1 · ⋮ › Xin gia hạn"**. Ở bảng *Popup / trang con / tương tác* (3d) của màn hình danh sách:
  - popup mở từ mục menu → *Popup*, cột *Nút / mục đã bấm* = "dòng 1 · ⋮ › Xin gia hạn";
  - trang con mở từ mục menu → *Trang con* (có URL; không tính màn hình lần nữa vì trang con đã là 1 màn hình riêng);
  - mục menu không mở gì (vd *Hoàn thành* – yêu cầu ghi bị chặn) → *Thao tác*.
  Mục đã được tự quét tìm thấy với cùng tên thì không thêm trùng. Chi tiết lần ghi ở 3c có thêm cột *Mở bằng*; nhật ký ghi in
  "· mở bằng: …".
- Chỉ áp dụng cho **lần ghi mới** – các lần ghi trước đây không lưu cú bấm. Kiểm chứng trên web demo bằng bộ ghi thật (thao tác
  mô phỏng): ghi đúng "dòng 1 · ⋮ › Xin gia hạn / Phân công / Hoàn thành". Mã nguồn: `crawler/manual.py` (`CLICK_JS`,
  `_on_click`), `crawler/merge.py` (`_link_manual_clicks`), `ui/steps/s03_crawl.py`; test `tests/test_interactions.py`.

### Bước 3c – Xem và xoá từng lần ghi thao tác
- Khung *🎥 Dữ liệu đã ghi* có bảng tóm tắt **từng lần ghi** (Lần 1 = lần sớm nhất): giờ bắt đầu/kết thúc, phân hệ, số màn hình,
  popup, số đoạn **đưa vào 3d** / **bỏ qua**, số API, số request ghi bị chặn.
- **Bấm vào 1 dòng** của bảng tóm tắt → hiện danh sách màn hình/popup của lần ghi đó kèm cột **Kết quả gộp** (✅ Đã đưa vào kết quả /
  ⛔ Bỏ qua – trang mới không gọi API / ↺ Trùng popup đã ghi ở trên).
- Nút **🗑 Xoá lần ghi N** (tick xác nhận) xoá riêng lần đó khỏi `crawl/record.json` và gộp lại 3d từ các lần còn lại; nút cũ đổi
  thành *🗑 Xoá tất cả lần ghi*. Mã nguồn: `crawler/merge.py` (`load_sessions`, `segment_status`, `delete_session`),
  `ui/steps/s03_crawl.py`; test `tests/test_interactions.py`.

### Sửa các lỗi mức Trung bình / Thấp còn lại (đợt 2)
**Bảo mật**
- **Bộ lọc câu truy vấn CSDL (6b.3)** không còn lách được: từ chối mọi dấu `;` giữa câu (kể cả trong chuỗi/chú thích,
  vd `SELECT '--'; DROP TABLE t`), chặn thêm `SELECT … INTO`, `INTO OUTFILE`, `COPY`, `CALL`, `pg_terminate_backend`, `xp_…`/`sp_…`…
  Kết nối PostgreSQL mở phiên chỉ đọc (`default_transaction_read_only`), MySQL chạy `SET SESSION TRANSACTION READ ONLY`.
  Khuyến nghị vẫn dùng tài khoản CSDL chỉ có quyền đọc.
- **Token/cookie phiên không còn bị nhúng vào script** (`script.js`, `.jmx`) khi xác thực là *Mỗi VU đăng nhập 1 lần*; chỉ nhúng khi
  chọn *Dùng cookie/token phiên đã ghi nhận* (chế độ này cần token). `project.json` vẫn lưu token phiên cho chế độ đó.
- Danh sách từ cấm bấm (crawler) chuẩn hoá Unicode NFC: nút "Xóa" viết bằng dấu tổ hợp vẫn bị nhận ra và không bị bấm.

**Script k6 / JMeter**
- Tên UC/dự án có xuống dòng (Alt+Enter trong Excel), mã UC có dấu nháy, hoặc 2 UC trùng mã (`UC-1` và `UC.1`) không còn làm
  script k6 lỗi cú pháp (tên gom thành 1 dòng, mã UC viết dạng chuỗi JSON, tên hàm tự thêm hậu tố `_2`…).
- File `.jmx` luôn là XML hợp lệ: bỏ ký tự điều khiển (vd `\x0b` khi dán từ Word), tên dự án có `--` không làm hỏng chú thích.
- Mật khẩu có dấu tiếng Việt, ký tự đặc biệt hoặc khoảng trắng đầu nay truyền đúng cho JMeter (`secrets.properties` ghi dạng
  `\uXXXX` – đã kiểm chứng bằng `java.util.Properties.load`).
- Thời lượng `500ms` không còn bị hiểu là 500 phút (hỗ trợ đơn vị `ms`).
- Bước 7 cảnh báo khi chế độ *Gộp* có số VU ít hơn số UC (thực tế sẽ chạy nhiều VU hơn cấu hình) và gợi ý 3–10 VU cho Smoke Test.

**Kết quả / báo cáo**
- Nhận xét tổng quan đếm đúng: "Đã thực hiện *N lượt* kiểm thử, thu được *M kết quả (lượt × UC)*" (trước ghi số dòng thành số lượt).
- Biểu đồ thông lượng và so sánh p95 không còn gộp các lượt chạy lại cùng kịch bản: nhãn trùng được thêm giờ chạy; tiêu đề chi tiết
  ở 8e ghi *chạy lúc hh:mm:ss*.
- Báo cáo Word bỏ đánh số kép ("2.3. A. …" → "2.3. …", "2.3.1. 1. …" → "2.3.1. …").
- Đọc được file UC / danh sách tài khoản dạng Excel "Unicode Text (.txt)" (UTF-16).

**Giao diện**
- Khung *📜 Tiến trình chạy test* tự mở mỗi lần chạy lượt mới (trước chỉ mở ở lần đầu).
- Thanh bên: bước 10 có ✅ khi đã xuất báo cáo; bước 6 chỉ ✅ sau khi mở bước 6 hoặc đã sinh script. Bước 7 khi chưa xác nhận
  phạm vi vẫn xem được lịch sử lượt chạy.
- Bảng *Cách đo* (❔ Giải thích ở bước 4) không còn hiện ký tự `**`; ô trống không còn hiện chữ "None" (bảng máy chủ 6b.2, cột Mã thoát 7d).
- Bảng máy chủ 6b.2: lưu lại không làm mất tổng RAM đã đo; máy SSH bắt buộc có địa chỉ IP; bước 7 cảnh báo khi bật thu số liệu
  nhưng không có máy chủ nào thu được.
- Ô *Tên dự án* ở thanh bên tự xoá sau khi tạo dự án; dòng phân bổ tài khoản ở 7a chỉ liệt kê tới VU cuối cùng.
- Hai thẻ k6 / Apache JMeter ở 6a dùng chữ đậm thay tiêu đề lớn.
- **Giao diện tối**: tự theo chế độ sáng/tối của máy (`.streamlit/config.toml` khai báo `[theme.light]` / `[theme.dark]`).
  Hết cảnh báo lặp *Invalid color … theme.sidebar* trên console trình duyệt.

**Không đổi (có chủ đích):** chỉ mục mục ở bước 8 vẫn liên tục (8c có thể là *Tổng hợp theo phân hệ* hoặc *Bảng tổng hợp*) theo quy ước
đánh chỉ mục đã thống nhất. ~~Chuỗi `${…}` trong body request vẫn được JMeter hiểu là biến/hàm.~~ *(Đã đổi cùng ngày: tên, đường
dẫn, body lấy từ trang đích được escape `\` → `\\`, `$` → `\$` nên JMeter giữ nguyên chuỗi `${…}`, không thực thi.)*
- Test: bộ 253 test biên/bảo mật chuyển vào `tests/edge/`, thêm 3 test hồi quy → tổng **305 test đạt**.

### Sửa 4 lỗi mức Cao phát hiện khi kiểm thử toàn diện bằng agent
- **Request ghi dữ liệu bị bật sẵn trong kịch bản (bước 5).** Trước đây POST được coi là "truy vấn" nếu *bất kỳ đâu* trong URL
  có `get/list/report…` → `POST /api/budget/save` (chữ "get" trong "budget"), `/api/checklist/delete`, hoặc mọi POST khi tên máy chủ
  chứa "get" đều được bật. Nay dùng chung hàm `guard.is_query_request()`: chỉ xét **đoạn cuối đường dẫn**; POST chỉ bật khi đoạn cuối
  bắt đầu bằng từ đọc (GetAll, Search…) hoặc không chứa từ ghi và qua mức kiểm tra "strict"; POST không rõ (vd `/api/tasks`) tắt mặc định.
  Cảnh báo "Đang bật N request ghi dữ liệu" ở 5a dùng cùng hàm (bỏ qua bước lấy token như `VPUB_GetToken`) → không còn báo nhầm `POST …/search`.
  Đã so trên dữ liệu NTQLCQS: sinh lại kịch bản không làm đổi trạng thái bật/tắt request nào.
  *Lưu ý:* kịch bản đã sinh trước đây giữ nguyên trạng thái bật/tắt; bấm sinh lại kịch bản ở bước 5 để áp dụng quy tắc mới.
- **Bộ chặn ghi để lọt PUT/PATCH/DELETE.** Trước đây tiền tố đọc của đoạn cuối URL được xét trước phương thức, nên `DELETE /api/timesheet`
  ("tim"), `PUT /api/countries` ("count")… không bị chặn. Nay PUT/PATCH/DELETE **luôn bị chặn** ở cả mức từ khoá và strict.
- **Thu số liệu máy chạy test (`local`) không ra dữ liệu và để lại `typeperf` chạy mãi.**
  - Máy `local` để trống *Địa chỉ IP* bị bỏ qua → nay không cần IP.
  - `typeperf` chạy liên tục qua ống dẫn bị dồn bộ đệm ~4 KB nên lượt test ngắn không có mẫu nào → máy `local` nay đọc bằng psutil
    (cùng bộ chỉ số CPU, RAM, đĩa, mạng), không sinh tiến trình con. Máy SSH mỗi chu kỳ chạy 1 lệnh `typeperf` ngắn (2 mẫu) rồi thoát.
  - Dừng bộ thu: runner tạo file cờ `monitor/collector.stop` để bộ thu dừng êm (trước đây `terminate()` giết ngang, không dọn tiến trình con);
    quá 15 giây mới diệt cả cây tiến trình.
  - Kiểm chứng: lượt 22 giây ghi được 24 giá trị (4 mẫu × 6 chỉ số), dừng trong 0,4 giây, không còn `typeperf` sót lại.
- **Lượt bị dừng giữa chừng bị tính là "✅ Đã chạy xong".** Nay lượt đang chạy mà tiến trình nền đã tắt (bấm ⏹ Dừng, đóng ứng dụng)
  được ghi **⏹ Đã dừng**, có giờ kết thúc (= lần cuối file kết quả được ghi) và **không** được đưa vào bước 8–10.
  Đồng thời sửa lỗi đọc file JMeter `.jtl` bị cắt dòng cuối khi dừng (trước đây báo lỗi *array length … does not match*)
  và số VU bị gán lệch thời điểm khi file không theo thứ tự thời gian.
- Mã nguồn: `perftool/crawler/guard.py`, `perftool/analysis/complexity.py`, `perftool/ui/steps/s05_confirm.py`,
  `perftool/monitor/sources.py`, `collector.py`, `runtime.py`, `perftool/ui/steps/monitor_panel.py`, `perftool/ui/steps/s07_run.py`,
  `perftool/results/parsers.py`; test mới `tests/test_safety_fixes.py` (10 test; tổng 49 test đạt).

### Bước 3 – Quét đủ màn hình, nút, popup (tự quét tương tác + ghi thao tác thủ công)
- Trước đây bước 3 chỉ đi theo link menu; quét popup (tuỳ chọn) chỉ bấm nút có chữ Xem/Thêm/Sửa/Chi tiết, **không mở được menu ⋮**
  trên từng dòng (vd *Việc cần xử lý* → ⋮ → *Xử lý / Xin gia hạn / Phân công*), không bấm tab, Tìm kiếm, chuyển trang.
- **A. Tự quét tương tác** (tick ở 3b, đổi tên từ *Quét cả màn hình popup/modal*): bấm từng tab, nút Tìm kiếm, sang trang 2,
  nút Xem/Thêm mới/Sửa, và mở **menu ⋮ / … / nút thả xuống ở dòng đầu bảng** + thanh công cụ → bấm lần lượt từng mục an toàn:
  mục mở popup ghi thành popup, mục chuyển trang ghi thành **trang con** rồi quay lại. Nhãn các mục menu được cộng vào tiêu chí CRUD.
- **B. Ghi thao tác thủ công** (mục mới **3c**, tuỳ chọn – không bắt buộc): PerfTool mở trình duyệt đã đăng nhập, người dùng tự bấm; công cụ tách từng màn hình
  (đổi URL) / popup (mở hộp thoại) và gán API cho đúng màn hình. Kết thúc bằng thanh đỏ *Kết thúc ghi* trong trình duyệt, nút
  *⏹ Kết thúc ghi & lưu* hoặc đóng trình duyệt. Dữ liệu ở `crawl/record.json`, giữ lại khi phân tích lại 3b, có nút xoá.
- **Bộ chặn ghi dữ liệu** (`crawler.write_guard`): khi tự quét (và khi ghi thủ công, 3 mức: theo từ khoá / mọi request không phải
  GET / không chặn), request ghi (PUT/PATCH/DELETE, form POST, POST có `create/update/delete/assign/complete/phancong/giahan…`)
  bị huỷ trong trình duyệt, không tới máy chủ, nhưng vẫn ghi lại (đánh dấu *bị chặn*) để sinh kịch bản test.
- Danh sách mục cấm bấm (`popup_blacklist`) bổ sung Hoàn thành, Tiếp nhận, Trả lại, Khôi phục, Nhân bản…; so khớp **nguyên từ**
  (trước đây "in " khớp nhầm "X**in** gia hạn").
- Mục kết quả đổi thành **3d**: tiêu đề phân hệ ghi *N trang + M popup / trang con*; thêm bảng *Popup / trang con / tương tác đã quét*
  (Loại, Nút / mục đã bấm, Mở ra, Trường nhập, Bảng, API, Tính màn hình, Nguồn) và cột *Nguồn* ở bảng trang.
- Web demo (`samples/demo_server.py`): trang *Danh sách công việc* có tab, Tìm kiếm, phân trang, menu ⋮ mỗi dòng (Xử lý, Xin gia hạn,
  Phân công → trang con, Hoàn thành, Xóa) để kiểm thử. Kết quả kiểm chứng trên demo: tự quét 1 trang → thêm 10 tương tác
  (6 popup/trang con tính màn hình), máy chủ ghi nhận **0** lần gọi API ghi; khi cố ý cho bấm *Hoàn thành*, bộ chặn huỷ
  `POST /api/tasks/complete`. Ghi thủ công giả lập: 2 màn hình + 1 popup, *Lưu phân công* bị chặn.
- Chưa chạy trên NTQLCQS thật (chờ cho phép vì có bấm nút trên hệ thống thật).
- Mã nguồn: `perftool/crawler/interactions.py`, `guard.py`, `manual.py` (mới), `web_crawler.py`, `merge.py`, `cli.py` (`--mode record`),
  `perftool/ui/steps/s03_crawl.py`, `perftool/models.py` (`PopupInfo.kind/url/origin`, `CapturedRequest.blocked/origin`,
  `PageInfo.origin`), `config/settings.yaml`, `tests/test_interactions.py`.

## 2026-09-24

### Giao diện – Nút chọn / bỏ chọn tất cả cho mọi bảng có cột tick
- Hàm dùng chung `check_all_buttons()` trong `perftool/ui/components.py`. Cặp nút đặt **ngay trên bảng** và lưu ngay khi bấm.
- **3b** *Danh sách phân hệ*: ☑ Chọn tất cả / ☐ Bỏ chọn tất cả. Trước đây cặp nút nằm dưới bảng, nay chuyển lên trên.
- **4b** *Kết quả chấm điểm*: ☑ Chọn tất cả / ☐ Bỏ chọn tất cả.
  - Chỉ áp dụng cho các UC **đang hiển thị theo bộ lọc**; UC bị lọc ẩn giữ nguyên.
  - Đánh dấu cần xác nhận lại ở bước 5.
- **5a** *Danh sách request* (mỗi UC): ☑ Bật tất cả / ☐ Tắt tất cả.
  - Khi đang bật request ghi dữ liệu (POST/PUT/PATCH/DELETE), màn hình hiện cảnh báo.

### Giao diện – Đánh chỉ mục thống nhất cho mọi bước
- Trước đây chỉ bước 3 có chỉ mục (3a, 3b, 3c). Nay mọi mục chính của 10 bước đều đánh theo kiểu **<số bước><a, b, c…>**, vd 1a–1c, 7a–7d, 8a–8f, 10a–10c.
- Mục chỉ hiện khi đủ điều kiện (vd *Ánh xạ cột* ở bước 2, *Tổng hợp theo phân hệ* ở bước 8) vẫn được đánh liên tục, không nhảy chữ cái.
- Mục con trong khối thu số liệu máy chủ ở bước 6 đánh **6b.1, 6b.2…**.
- Thêm tiêu đề cho các phần trước đây chưa có tiêu đề:
  - 2a *Chọn file danh sách UC*;
  - 5a / 5b *Danh sách request* / *Xác nhận phạm vi*;
  - 6a *Công cụ kiểm thử*;
  - 7c *Theo dõi tiến trình*;
  - 8a / 8b *Chọn kịch bản* / *Tổng quan kết quả*;
  - 10a–10c.
- Hàm dùng chung: `section_titles()` trong `perftool/ui/components.py`.

### Sửa lỗi – Chạy lại lượt đã có kết quả bị báo "done" với dữ liệu cũ
- Lỗi: chạy lại một lượt JMeter đã có `html-report/` và `results.jtl` thì JMeter dừng với lỗi *Cannot write to '…html-report' as folder is not empty*. Công cụ vẫn ghi trạng thái **done** vì file `results.jtl` cũ còn đó, nên kết quả cũ bị báo cáo như kết quả mới (gặp ở lượt `20260923-131608_jmeter_load_UC-BC-001`).
- Trước khi chạy, công cụ tự xoá kết quả cũ của lượt đó: k6 xoá `raw.csv`, `summary.json`; JMeter xoá `results.jtl` và thư mục `html-report/`. Chỉ xoá trong thư mục của chính lượt chạy. Nhật ký ghi rõ file nào đã xoá.
- Chỉ tính lượt là có dữ liệu khi file dữ liệu thô được ghi **sau** thời điểm bắt đầu lượt chạy.
- Mã nguồn: `perftool/runner/executor.py` (`clear_previous_outputs`, `has_fresh_data`), `perftool/runner/cli.py`, `tests/test_runner.py`.

### Thu số liệu tài nguyên máy chủ (tuỳ chọn): CPU, RAM, I/O, kết nối CSDL
- Phần **cấu hình** nằm ở **bước 6** (khối *📡 Thu số liệu tài nguyên máy chủ*, dưới phần chọn k6/JMeter), gồm: công cụ giám sát, máy chủ, kết nối CSDL, lệnh `logman` cho quản trị viên máy chủ không có SSH.
- Phần **số liệu đã thu** nằm ở **bước 8** (mục *Tài nguyên máy chủ theo lượt chạy*), kèm khung *📥 Tải file Performance Monitor lên* (làm sau khi test). Ứng dụng vẫn giữ 10 bước.
- Không bắt buộc: công tắc *Thực hiện thu thập số liệu…* mặc định tắt. Khi tắt, báo cáo ghi “không thu thập” như trước.
- Chọn 1 hoặc nhiều công cụ giám sát:
  - **Windows Performance Monitor**: lấy qua SSH, trên chính máy chạy test, hoặc tải file `.csv`/`.blg` lên (có sẵn lệnh `logman` cho quản trị viên);
  - **Zabbix**: lấy lịch sử item qua API;
  - **Grafana / Prometheus**: gọi `query_range`, có sẵn câu mẫu cho windows_exporter / node_exporter.
- Khai báo **nhiều máy chủ** (web, ứng dụng, CSDL) trong 1 bảng. Mật khẩu SSH lưu ở Windows Credential Manager. Nút *Lưu & kiểm tra kết nối* đọc thử số liệu và tự lấy tổng RAM.
- **Kết nối CSDL**: chọn SQL Server / PostgreSQL / MySQL / Oracle. Ô câu truy vấn có sẵn câu mặc định theo từng loại. Chỉ cho phép 1 câu SELECT/WITH/SHOW.
- **Thu thập chạy song song khi chạy test ở bước 7**. Tiến trình thu số liệu tự khởi động và tự dừng theo lượt test. Bước 7 hiện dòng trạng thái 📡 và cảnh báo nếu thiếu mật khẩu.
- Bước 8: bảng lượt chạy có/không có số liệu, bảng trung bình/cao nhất, biểu đồ và nhận xét tự động theo các kịch bản đang chọn.
- **Bước 9 (Biểu đồ)**: thêm biểu đồ *Tài nguyên máy chủ trong thời gian kiểm thử* cho từng kịch bản.
- **Bước 10 (Xuất báo cáo)**: thêm ô *Tài nguyên máy chủ*, tự bật khi có số liệu. Báo cáo Word có thêm:
  - dòng 12 trong bảng cấu hình chung;
  - dòng "Số liệu tài nguyên máy chủ" trong bảng cấu hình từng kịch bản;
  - bảng số liệu, biểu đồ và nhận xét cho từng kịch bản;
  - kiến nghị theo mức sử dụng tài nguyên, thay cho câu "cần thu thập thêm".
- File Excel có thêm sheet *Tai nguyen may chu*.
- Thư viện mới trong `requirements.txt`: paramiko, pymssql, psycopg, pymysql, oracledb.
- Mã nguồn mới: package `perftool/monitor/` (`perfmon.py`, `sources.py`, `store.py`, `collector.py`, `runtime.py`), `perftool/ui/steps/monitor_panel.py` (dùng ở bước 6 và 8), `tests/test_monitor.py`.

### Bước 8 → 10 – Chọn kịch bản ở bước 8
- Ô chọn kịch bản chuyển từ bước Xuất báo cáo sang **bước 8**. Chọn xong bấm *🔄 Phân tích lại* để tính lại toàn bộ thống kê.
- Bước 9 (Biểu đồ) và bước 10 (Xuất báo cáo) dùng đúng lựa chọn này. Bước 10 có nút *✏️ Đổi lựa chọn*.
- Ô tuỳ chọn nội dung báo cáo xếp thành 2 hàng, nhãn đầy đủ.
- Báo cáo đổi “ngưỡng cam kết” thành **“ngưỡng đánh giá”**, và ghi rõ nguồn ngưỡng, vd *bằng 29% ngưỡng p95 (3000 ms – mặc định của công cụ)*.

### Tài liệu
- Cập nhật `HUONG_DAN_SU_DUNG.md` / `.docx`:
  - bảng 10 bước (bước 3, 7, 8);
  - mục mới **5.7 Theo dõi tiến trình và quản lý lịch sử lượt chạy**: khung tiến trình, bảng trạng thái, cách xoá lượt và ảnh hưởng, cách tính số liệu đầu bước 8;
  - bổ sung dòng xử lý sự cố;
  - thêm cấu hình `crawler.window_size`;
  - thêm `CHANGELOG.md` vào cấu trúc thư mục.

### Bước 7 – Xoá từng lượt chạy
- Bảng **Lịch sử lượt chạy** có ô tick đầu dòng. Chọn một hoặc nhiều lượt rồi bấm **🗑 Xoá N lượt** trong khung hiện ra ngay dưới bảng.
- Tuỳ chọn *Xoá luôn thư mục kết quả trên đĩa*:
  - không tick: chỉ gỡ lượt khỏi danh sách, thư mục `workspace/projects/<dự án>/runs/<lượt>` vẫn còn;
  - tick: xoá hẳn, không khôi phục được.
- Khoá nút Xoá khi lượt được chọn đang chạy hoặc chờ chạy trong tiến trình hiện tại.
- Ảnh hưởng: bước 8, 9, 10 chỉ tính các lượt còn trong lịch sử, nên lượt đã xoá không còn trong thống kê, biểu đồ và báo cáo xuất sau đó. File báo cáo đã xuất trước đó không bị ảnh hưởng.
- Gom logic xoá vào hàm `delete_runs()` trong `perftool/ui/steps/s07_run.py`, dùng chung cho xoá từng dòng, xoá theo trạng thái và xoá tất cả.

## 2026-09-23

### Bước 3 – Trình duyệt quét mở toàn màn hình
- Trước đây trang bị ép hiển thị cố định 1440×900 ở tỉ lệ 100%. Trên màn hình phóng to 150%, trang chỉ chiếm khoảng một nửa màn hình.
- Nay khi chạy có hiện trình duyệt, trang co giãn theo cửa sổ đã mở toàn màn hình (vd 2048×1145 ở tỉ lệ 150%). Khi chạy ẩn, trình duyệt giả lập màn hình 1920×1080.
- Thêm cấu hình `crawler.window_size` trong `config/settings.yaml`: `maximized` (mặc định), hoặc kích thước cố định dạng `1440x900`.

### Khung tiến trình thống nhất (bước 3 và bước 7)
- Mọi khung theo dõi tác vụ nền dùng chung `job_panel()` trong `perftool/ui/components.py`. Kiểu hiển thị cũ đã bỏ.
- Khung là nhóm thu gọn / mở rộng, trải hết chiều ngang, tự mở khi đang chạy.
- Tiêu đề ghi trạng thái và tự cập nhật mỗi 2 giây, vd `📜 Tiến trình chạy test – ⏳ Đang chạy · 3/5 · bắt đầu … · kết thúc …`.
- Nút ⏹ Dừng nằm bên trong khung.
- Log xếp **dòng mới nhất ở trên cùng**.

### Bước 7 – Trạng thái và dọn lịch sử
- Đổi nhãn "✅ Hoàn tất" thành **"✅ Đã chạy xong"**. Trạng thái này chỉ nghĩa là lượt đã chạy hết; còn Đạt / Không đạt do bước 8 đánh giá theo ngưỡng.
- **🗑 Dọn lịch sử:**
  - xoá theo trạng thái (chọn nhiều trạng thái, mặc định Lỗi và Đã dừng);
  - xoá tất cả (phải tick xác nhận);
  - có tuỳ chọn xoá luôn thư mục kết quả;
  - bị khoá khi đang chạy test.

### Giải đáp (không đổi code)
- **"Đã chạy xong" và Đạt / Không đạt:** đợt 22 lượt trên NTQLCQS ra 12 Đạt / 10 Không đạt. Cả 10 lượt trượt đều do tỷ lệ lỗi > 1% vì HTTP 429 (giới hạn tần suất khi dùng chung một tài khoản), không lượt nào trượt vì p95 (cao nhất 835 ms so với ngưỡng 3000 ms).
- **Nguồn của "Tổng request" ở bước 8:**
  - k6: đếm số dòng `http_req_duration` trong `raw.csv`;
  - JMeter: đếm số sampler trong `results.jtl`, bỏ dòng TRANSACTION;
  - con số gồm cả request đăng nhập và request lỗi, chỉ tính các lượt đã chạy xong.
