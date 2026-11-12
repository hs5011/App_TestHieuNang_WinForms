"""Mô hình dữ liệu của một dự án kiểm thử hiệu năng (lưu dạng JSON)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import math

from pydantic import BaseModel, Field, field_validator, model_validator


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _finite(v: Any, default: Any) -> Any:
    """NaN/None/rỗng (ô số bị xoá trên bảng của Streamlit) -> giá trị mặc định, tránh ghi "null"/NaN vào project.json
    làm dự án không nạp lại được và script k6/JMX sinh lỗi."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


# ---------------------------------------------------------------- Bước 1
class ProjectInfo(BaseModel):
    name: str = "Dự án mới"
    system_name: str = ""
    environment: str = "staging"
    author: str = ""
    organization: str = ""
    created_at: str = Field(default_factory=_now)


class LoginConfig(BaseModel):
    base_url: str = ""
    login_url: str = ""                 # để trống => dùng base_url
    username: str = ""
    # Mật khẩu KHÔNG lưu vào project.json; giữ trong phiên hoặc Windows Credential Manager
    username_selector: str = ""         # để trống => tự dò
    password_selector: str = ""
    submit_selector: str = ""
    success_url_contains: str = ""      # dấu hiệu đăng nhập thành công (tuỳ chọn)
    manual_login: bool = False          # người dùng tự đăng nhập (OTP/CAPTCHA) trên trình duyệt
    headless: bool = False
    # bỏ qua kiểm tra chứng chỉ HTTPS (chỉ cho máy chủ kiểm thử dùng chứng chỉ tự ký): crawler, kiểm tra đăng nhập, k6
    skip_tls_verify: bool = False
    # Tài khoản kiểm thử bổ sung (chỉ lưu tên đăng nhập; mật khẩu ở phiên / Windows Credential Manager)
    extra_accounts: list[str] = Field(default_factory=list)
    include_main_in_pool: bool = True   # tài khoản chính cũng tham gia chia cho các VU


# ---------------------------------------------------------------- Bước 2
class UseCase(BaseModel):
    code: str
    name: str
    module: str = ""
    actor: str = ""
    description: str = ""
    steps: Optional[int] = None
    frequency: str = ""
    complexity: str = ""                # BMT / mức độ trong file gốc
    url_hint: str = ""                  # URL/menu gợi ý (tuỳ chọn)
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- Bước 3
class CapturedRequest(BaseModel):
    method: str
    url: str
    resource_type: str = ""
    status: Optional[int] = None
    duration_ms: Optional[float] = None
    content_type: str = ""
    post_data: Optional[str] = None
    response_size: Optional[int] = None
    page_url: str = ""
    # chuỗi token: chữ ký băm (không lưu giá trị token)
    auth_sig: str = ""          # băm token trong header Authorization của request này
    token_path: str = ""        # đường dẫn token trong JSON phản hồi (nếu request này cấp token)
    token_sig: str = ""         # băm token mà request này cấp
    headers: dict[str, str] = Field(default_factory=dict)   # header tuỳ biến (không chuẩn) của ứng dụng
    blocked: bool = False       # request ghi dữ liệu bị bộ chặn ghi huỷ khi quét/ghi thao tác (không tới máy chủ)
    origin: str = ""            # "" = crawler tự quét | "manual" = ghi thao tác thủ công
    sensitive: bool = False     # body có mật khẩu/OTP/secret (đã che "***") – không bao giờ bật sẵn trong kịch bản

    @model_validator(mode="after")
    def _redact(self) -> "CapturedRequest":
        """Che mật khẩu/OTP trong body và header phiên mỗi khi tạo/nạp request (kể cả record.json, project.json cũ)."""
        from .crawler.login_capture import redact_body, redact_headers
        if self.post_data:
            body, hit = redact_body(self.post_data)
            if hit:
                self.post_data, self.sensitive = body, True
        if self.headers:
            self.headers = redact_headers(self.headers)
        return self


# Loại tương tác quét được trên 1 trang (PopupInfo.kind)
INTERACTION_KINDS = {"popup": "Popup", "page": "Trang con", "tab": "Tab", "search": "Tìm kiếm",
                     "paging": "Chuyển trang", "action": "Thao tác"}


class PopupInfo(BaseModel):
    """Tương tác trên 1 trang: popup/modal mở ra khi bấm nút (không có URL riêng), trang con mở từ menu ⋮,
    tab, tìm kiếm, chuyển trang… (kind xem INTERACTION_KINDS)."""
    trigger: str                        # nhãn nút đã bấm, vd "thêm mới", "⋮ › Xử lý"
    kind: str = "popup"
    url: str = ""                       # URL trang con (kind = page)
    origin: str = ""                    # "" = crawler tự quét | "manual" = ghi thao tác thủ công
    title: str = ""
    inputs: int = 0
    selects: int = 0
    buttons: int = 0
    tables: int = 0
    api_count: int = 0                  # số API gọi khi mở popup
    counted: bool = True                # có tính là 1 màn hình (có biểu mẫu/bảng/nội dung chi tiết)


class PageInfo(BaseModel):
    url: str
    title: str = ""
    menu_text: str = ""
    module: str = ""
    depth: int = 0
    forms: int = 0
    inputs: int = 0
    selects: int = 0
    buttons: int = 0
    tables: int = 0
    table_rows: int = 0
    crud_buttons: list[str] = Field(default_factory=list)
    links: int = 0
    load_time_ms: Optional[float] = None
    screenshot: str = ""
    requests: list[CapturedRequest] = Field(default_factory=list)   # gồm cả API gọi khi mở popup
    popups: list[PopupInfo] = Field(default_factory=list)
    error: str = ""
    origin: str = ""                    # "manual" = màn hình chỉ có trong dữ liệu ghi thao tác thủ công

    @property
    def api_requests(self) -> list[CapturedRequest]:
        return [r for r in self.requests if r.resource_type in ("xhr", "fetch")]


class ModuleInfo(BaseModel):
    name: str
    url: str
    enabled: bool = True
    pages: list[PageInfo] = Field(default_factory=list)


class AuthCapture(BaseModel):
    """Thông tin xác thực bắt được khi đăng nhập, dùng để sinh bước login trong script."""
    login_method: str = "POST"
    login_url: str = ""
    login_content_type: str = ""
    login_body_template: str = ""       # đã thay user/pass bằng {{USERNAME}} / {{PASSWORD}}
    token_json_path: str = ""           # vd: data.accessToken
    token_header: str = "Authorization"
    token_prefix: str = "Bearer "
    static_headers: dict[str, str] = Field(default_factory=dict)
    cookies: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------- Bước 4-5
class UCScore(BaseModel):
    uc_code: str
    uc_name: str
    module: str = ""
    raw: dict[str, float] = Field(default_factory=dict)       # số liệu thô từng tiêu chí
    scores: dict[str, float] = Field(default_factory=dict)    # điểm chuẩn hoá 0..1
    total: float = 0.0
    matched_pages: list[str] = Field(default_factory=list)
    api_paths: list[str] = Field(default_factory=list)       # tập API (method + path) để đánh giá trùng lặp
    match_confidence: float = 0.0
    reason: str = ""
    recommended: bool = False
    selected: bool = False
    note: str = ""


class ScenarioStep(BaseModel):
    name: str
    method: str = "GET"
    url: str
    body: str = ""
    content_type: str = ""
    think_time_s: float = 0.0
    expected_status: str = "2xx,3xx"
    enabled: bool = True
    extract_token: str = ""     # JSON path lấy token từ phản hồi, vd: result.data.accessToken
    token_var: str = ""         # tên biến lưu token trích được
    use_token: str = ""         # dùng token trong biến này làm Authorization (trống = token đăng nhập chính)
    headers: dict[str, str] = Field(default_factory=dict)   # header riêng gửi kèm (vd: origin, x-api-key...)

    @field_validator("think_time_s", mode="before")
    @classmethod
    def _v_think(cls, v: Any) -> Any:
        return max(_finite(v, 0.0), 0.0)


class TestScenario(BaseModel):
    __test__ = False  # không phải test case của pytest
    uc_code: str
    uc_name: str
    module: str = ""
    p95_threshold_ms: Optional[float] = None   # ghi đè ngưỡng chung
    steps: list[ScenarioStep] = Field(default_factory=list)

    @field_validator("p95_threshold_ms", mode="before")
    @classmethod
    def _v_thr(cls, v: Any) -> Any:
        v = _finite(v, None)
        return v if v else None


# ---------------------------------------------------------------- Bước 6-7
class TestConfig(BaseModel):
    __test__ = False
    tools: list[str] = Field(default_factory=lambda: ["k6"])
    scenario_type: str = "load"
    vus: int = 50
    duration: str = "5m"
    ramp_up: str = "1m"
    ramp_down: str = "30s"
    think_time_s: float = 1.0
    p95_threshold_ms: float = 3000
    error_rate_threshold: float = 0.01
    auth_mode: str = "login_per_vu"     # login_per_vu | static_headers | none
    http_timeout_s: int = 60
    run_mode: str = "sequential"        # sequential: chạy từng UC | combined: gộp tất cả UC cùng lúc
    account_mode: str = "multi"         # single: mọi VU dùng tài khoản chính | multi: chia đều các tài khoản cho VU
    accounts_count: int = 1             # số tài khoản thực dùng ở lượt chạy (ghi vào báo cáo, không chứa mật khẩu)

    @field_validator("think_time_s", "p95_threshold_ms", "error_rate_threshold", mode="before")
    @classmethod
    def _v_num(cls, v: Any, info) -> Any:
        return _finite(v, cls.model_fields[info.field_name].default)


# ---------------------------------------------------------------- Bước 6 (tuỳ chọn): thu số liệu tài nguyên máy chủ
MONITOR_TOOLS = {"perfmon": "Windows Performance Monitor", "zabbix": "Zabbix", "prometheus": "Grafana / Prometheus"}
SERVER_ROLES = {"web": "Máy chủ Web", "app": "Máy chủ ứng dụng", "db": "Máy chủ CSDL", "other": "Khác"}
DB_TYPES = {"sqlserver": "SQL Server", "postgresql": "PostgreSQL", "mysql": "MySQL", "oracle": "Oracle"}


class MonitorServer(BaseModel):
    name: str = ""                      # tên hiển thị trong báo cáo, VD "Web 01"
    role: str = "web"                   # web | app | db | other
    host: str = ""                      # địa chỉ IP / tên máy
    access: str = "ssh"                 # Performance Monitor: ssh | local (chính máy chạy test) | file (tải file lên)
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_key_file: str = ""              # đường dẫn khoá riêng (tuỳ chọn, thay cho mật khẩu)
    ram_total_mb: Optional[float] = None   # tổng RAM (tự lấy khi kiểm tra kết nối) để tính % RAM từ "Available MBytes"
    zabbix_host: str = ""               # tên host trong Zabbix
    prom_instance: str = ""             # nhãn instance trong Prometheus, VD "10.0.0.5:9182"


class DBMonitor(BaseModel):
    enabled: bool = False
    db_type: str = "sqlserver"          # sqlserver | postgresql | mysql | oracle
    host: str = ""
    port: int = 0                       # 0 = cổng mặc định của loại CSDL
    database: str = ""                  # tên CSDL (Oracle: service name)
    username: str = ""
    query: str = ""                     # câu truy vấn trả về 1 số = số kết nối; rỗng = câu mặc định theo loại CSDL


class MonitorConfig(BaseModel):
    enabled: bool = False               # bật thu thập số liệu tài nguyên máy chủ khi chạy test (bước 7)
    tools: list[str] = Field(default_factory=lambda: ["perfmon"])
    interval_s: int = 5
    servers: list[MonitorServer] = Field(default_factory=list)
    zabbix_url: str = ""                # VD http://zabbix.local/zabbix
    zabbix_user: str = ""               # để trống nếu dùng API token
    zabbix_items: dict[str, str] = Field(default_factory=dict)   # metric -> item key (ghi đè mặc định)
    prom_url: str = ""                  # Prometheus hoặc Grafana datasource proxy
    prom_queries: dict[str, str] = Field(default_factory=dict)   # metric -> PromQL (ghi đè mặc định)
    db: DBMonitor = Field(default_factory=DBMonitor)


class RunInfo(BaseModel):
    run_id: str
    tool: str
    uc_code: str = "ALL"
    scenario_type: str = "load"
    status: str = "pending"             # pending | running | done | failed | stopped
    started_at: str = ""
    finished_at: str = ""
    returncode: Optional[int] = None
    script_file: str = ""
    raw_file: str = ""
    summary_file: str = ""
    log_file: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class Project(BaseModel):
    id: str
    # loại ứng dụng được kiểm thử: "web" (quét bằng trình duyệt) | "winforms" (ứng dụng desktop gọi API – ghi qua proxy / HAR)
    kind: str = "web"
    info: ProjectInfo = Field(default_factory=ProjectInfo)
    login: LoginConfig = Field(default_factory=LoginConfig)
    use_cases: list[UseCase] = Field(default_factory=list)
    uc_source_file: str = ""
    modules: list[ModuleInfo] = Field(default_factory=list)
    # bước 3: tự quét tương tác (bấm nút Xem/Thêm/Sửa, menu ⋮ trên dòng, tab, Tìm kiếm, chuyển trang)
    crawl_popups: bool = False
    auth: AuthCapture = Field(default_factory=AuthCapture)
    scores: list[UCScore] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    caps_mode: str = "auto"             # auto: mức trần theo dữ liệu dự án | fixed: theo config/settings.yaml
    score_caps: dict[str, float] = Field(default_factory=dict)   # mức trần đã dùng ở lần chấm điểm gần nhất
    confirmed: bool = False
    confirmed_at: str = ""
    scenarios: list[TestScenario] = Field(default_factory=list)
    test_config: TestConfig = Field(default_factory=TestConfig)
    runs: list[RunInfo] = Field(default_factory=list)
    conclusions: str = ""               # nhận xét bổ sung do người dùng nhập
    excluded_rows: list[str] = Field(default_factory=list)   # bước 8: "run_id|uc_code" bị loại khỏi thống kê & báo cáo
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)   # bước 6: thu số liệu tài nguyên máy chủ (tuỳ chọn)
    updated_at: str = Field(default_factory=_now)

    # tiện ích
    def uc(self, code: str) -> Optional[UseCase]:
        return next((u for u in self.use_cases if u.code == code), None)

    def selected_scores(self) -> list[UCScore]:
        return [s for s in self.scores if s.selected]

    def all_pages(self) -> list[PageInfo]:
        return [p for m in self.modules for p in m.pages]
