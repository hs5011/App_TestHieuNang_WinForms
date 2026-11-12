"""Khám phá "màn hình" của ứng dụng WinForms (thick client gọi API) từ lưu lượng HTTP(S).

WinForms không có DOM/URL nên không quét tự động được như Web. Thay vào đó:
  1. Người dùng thao tác ứng dụng WinForms (đi qua proxy ghi của PerfTool, hoặc dùng Fiddler/Charles/mitmproxy rồi nhập HAR);
  2. PerfTool chia các request thành "màn hình" (theo mốc người dùng đánh dấu, hoặc theo khoảng nghỉ giữa các thao tác);
  3. Mỗi màn hình được quy đổi sang số liệu "giả lập giao diện" (số trường nhập, số bảng, số dòng, thao tác CRUD)
     từ chính nội dung API, để bước 4 chấm điểm độ phức tạp như với Web;
  4. Kết quả ghi vào crawl/record.json (cùng định dạng ghi thao tác thủ công của Web) -> merge.apply_record() gộp vào dự án,
     các bước 4–10 dùng chung, không cần sửa.

Mô-đun này chỉ gồm hàm thuần (dễ kiểm thử); phần chạy proxy ở winforms_cli.py và mitm_addon.py.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlparse

from ..models import AuthCapture
from .guard import classify

WINFORMS_MODULE = "WinForms"
SCREEN_HOST_PREFIX = "winforms.local"          # URL ảo của màn hình: http://winforms.local/<module>/<màn hình>
GAP_DEFAULT_S = 8.0                             # khoảng nghỉ (giây) coi là chuyển sang màn hình khác khi không có mốc
STATIC_EXT = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".map",
              ".html", ".htm", ".zip", ".exe", ".dll", ".msi", ".cab")
TOKEN_KEYS = re.compile(r"^(access_?token|token|jwt|id_?token|auth_?token|accesstoken|bearer)$", re.IGNORECASE)
_STD_HEADERS = {
    "accept", "accept-encoding", "accept-language", "authorization", "cookie", "content-length", "content-type",
    "host", "connection", "user-agent", "referer", "priority", "pragma", "cache-control", "expect", "proxy-connection",
    "if-none-match", "if-modified-since", "range", "dnt", "te", "keep-alive", "transfer-encoding", "date",
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- chuẩn hoá flow
def slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", (text or "").strip().lower(), flags=re.UNICODE).strip("-")
    return s or "man-hinh"


def screen_url(module: str, screen: str) -> str:
    """URL ảo, ổn định cho 1 màn hình WinForms (merge dùng route_of(url) làm khoá màn hình)."""
    return f"http://{SCREEN_HOST_PREFIX}/{slug(module)}/{slug(screen)}"


def is_custom_header(name: str) -> bool:
    n = name.lower()
    return not (n.startswith(":") or n.startswith("sec-") or n in _STD_HEADERS)


def find_token_path(obj: Any, prefix: str = "") -> str:
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, str) and TOKEN_KEYS.match(k) and len(v) > 15:
                return p
            found = find_token_path(v, p)
            if found:
                return found
    return ""


def json_rows(obj: Any) -> int:
    """Số phần tử của danh sách lớn nhất trong phản hồi JSON (để ước lượng 'bảng' trên màn hình)."""
    best = 0
    stack = [obj]
    n = 0
    while stack and n < 2000:
        o = stack.pop()
        n += 1
        if isinstance(o, list):
            best = max(best, len(o))
            stack.extend(o[:3])
        elif isinstance(o, dict):
            stack.extend(o.values())
    return best


def count_fields(obj: Any, depth: int = 0) -> int:
    """Số trường lá trong body JSON (= số ô nhập ước lượng của biểu mẫu)."""
    if depth > 6:
        return 0
    if isinstance(obj, dict):
        return sum(count_fields(v, depth + 1) if isinstance(v, (dict, list)) else 1 for v in obj.values())
    if isinstance(obj, list):
        return count_fields(obj[0], depth + 1) if obj else 0
    return 1


def body_fields(body: Optional[str], ctype: str = "") -> int:
    if not body:
        return 0
    try:
        data = json.loads(body)
        return count_fields(data)
    except ValueError:
        pass
    if "x-www-form-urlencoded" in (ctype or "") or ("=" in body and "<" not in body[:5]):
        return len(parse_qsl(body, keep_blank_values=True))
    if "<" in body:                                  # SOAP / XML: đếm phần tử lá
        return len(re.findall(r"<([A-Za-z_][\w:.-]*)[^>/]*>[^<]+</", body))
    return 0


def is_api_flow(f: dict) -> bool:
    path = urlparse(f.get("url", "")).path.lower()
    if path.endswith(STATIC_EXT):
        return False
    ct = (f.get("ctype") or "").lower()
    if "text/html" in ct or ct.startswith(("image/", "font/", "text/css", "application/javascript")):
        return False
    return True


# ---------------------------------------------------------------- đọc nguồn
def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("url"):
            out.append(d)
    return out


def flows_from_har(har: dict, secrets: Iterable[str] = (), user: str = "", pwd: str = "") -> list[dict]:
    """HAR (Fiddler, Charles, mitmproxy, DevTools…) -> danh sách flow cùng định dạng với bộ ghi proxy."""
    from .login_capture import has_secret_field, login_template, redact_body
    out: list[dict] = []
    entries = ((har or {}).get("log") or {}).get("entries") or []
    secrets = [s for s in secrets if s]
    for e in entries:
        try:
            req, resp = e.get("request") or {}, e.get("response") or {}
            t = _har_time(e.get("startedDateTime", ""))
            hdr_req = {h.get("name", "").lower(): h.get("value", "") for h in req.get("headers", [])}
            ctype = (resp.get("content") or {}).get("mimeType", "") or ""
            text = (resp.get("content") or {}).get("text") or ""
            body = (req.get("postData") or {}).get("text")
            req_ct = hdr_req.get("content-type", "") or (req.get("postData") or {}).get("mimeType", "")
            f = {"t": t, "method": (req.get("method") or "GET").upper(), "url": req.get("url", ""),
                 "status": resp.get("status") or None, "duration_ms": round(float(e.get("time") or 0), 1) or None,
                 "ctype": ctype, "req_ctype": req_ct, "resp_size": (resp.get("content") or {}).get("size"),
                 "headers": {k: v for k, v in hdr_req.items() if is_custom_header(k)}, "blocked": False}
            auth = hdr_req.get("authorization", "")
            if auth:
                f["auth_sig"] = _sig(auth.split(" ", 1)[-1])
                f["_authorization"] = auth          # chỉ trong bộ nhớ; không ghi xuống đĩa (xem extract_auth)
            if text and "json" in ctype.lower():
                try:
                    j = json.loads(text)
                    f["rows"] = json_rows(j)
                    tp = find_token_path(j)
                    if tp and (f["method"] == "POST" or "token" in urlparse(f["url"]).path.lower()):
                        f["token_path"] = tp
                except ValueError:
                    pass
            if body:
                if f["method"] in ("POST", "PUT") and (has_secret_field(body, req_ct) or any(s in body for s in secrets)):
                    tpl, notes = login_template(body, req_ct, user, pwd)
                    f["login"] = {"template": tpl, "ctype": req_ct, "notes": notes, "token_path": f.get("token_path", "")}
                red, hit = redact_body(body, secrets)
                f["post_data"] = (red or "")[:4000]
                f["sensitive"] = bool(hit)
            out.append(f)
        except Exception:  # noqa: BLE001 - dòng hỏng không làm hỏng cả file
            continue
    return out


def _har_time(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _sig(value: str) -> str:
    import hashlib
    return hashlib.sha1((value or "").encode("utf-8")).hexdigest()[:10] if value else ""


# ---------------------------------------------------------------- chia màn hình
def read_markers(path: Path) -> list[dict]:
    """Mốc 'bắt đầu màn hình' do người dùng bấm trên PerfTool: {"t", "screen", "module"}."""
    return sorted((m for m in read_jsonl_any(path) if m.get("screen")), key=lambda m: m.get("t", 0))


def read_jsonl_any(path: Path) -> list[dict]:
    out: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def add_marker(path: Path, screen: str, module: str = "", t: Optional[float] = None) -> dict:
    import time
    m = {"t": time.time() if t is None else t, "screen": screen.strip(), "module": (module or "").strip()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    return m


def split_screens(flows: list[dict], markers: Optional[list[dict]] = None, gap_s: float = GAP_DEFAULT_S,
                  default_module: str = WINFORMS_MODULE) -> list[dict]:
    """Chia flow thành màn hình: [{"screen", "module", "flows": [...]}].

    Có mốc: request từ mốc i tới mốc i+1 thuộc màn hình i (request trước mốc đầu = đăng nhập/khởi động, bỏ).
    Không mốc: tách khi nghỉ hơn gap_s giây; đặt tên 'Màn hình N'. Các mốc cùng tên liên tiếp được gộp."""
    flows = sorted((f for f in flows if is_api_flow(f)), key=lambda f: f.get("t") or 0)
    if not flows:
        return []
    groups: list[dict] = []
    if markers:
        ms = sorted(markers, key=lambda m: m.get("t", 0))
        for i, m in enumerate(ms):
            lo = m.get("t", 0)
            hi = ms[i + 1].get("t", float("inf")) if i + 1 < len(ms) else float("inf")
            fl = [f for f in flows if lo <= (f.get("t") or 0) < hi]
            mod = (m.get("module") or default_module).strip() or default_module
            if groups and groups[-1]["screen"] == m["screen"] and groups[-1]["module"] == mod:
                groups[-1]["flows"] += fl
            else:
                groups.append({"screen": m["screen"], "module": mod, "flows": fl})
    else:
        cur: list[dict] = []
        last = None
        for f in flows:
            t = f.get("t") or 0
            if last is not None and t - last > gap_s and cur:
                groups.append({"screen": f"Màn hình {len(groups) + 1}", "module": default_module, "flows": cur})
                cur = []
            cur.append(f)
            last = t
        if cur:
            groups.append({"screen": f"Màn hình {len(groups) + 1}", "module": default_module, "flows": cur})
    return [g for g in groups if g["flows"]]


# ---------------------------------------------------------------- quy đổi sang số liệu "giao diện"
CRUD_LABELS = (("delete", "Xoá"), ("remove", "Xoá"), ("xoa", "Xoá"), ("create", "Thêm mới"), ("insert", "Thêm mới"),
               ("add", "Thêm mới"), ("them", "Thêm mới"), ("update", "Cập nhật"), ("edit", "Cập nhật"),
               ("capnhat", "Cập nhật"), ("sua", "Cập nhật"), ("save", "Lưu"), ("luu", "Lưu"))


_ID_SEG = re.compile(r"/[0-9a-f-]{6,}|/\d+")


def _op_label(method: str, path: str) -> str:
    p = re.sub(r"[^a-z0-9]+", "", path.lower())
    for key, label in CRUD_LABELS:
        if key in p:
            return label
    return {"POST": "Thêm mới", "PUT": "Cập nhật", "PATCH": "Cập nhật", "DELETE": "Xoá"}.get(method, "")


def screen_stats(flows: list[dict]) -> dict:
    """Ước lượng 'giao diện' từ nội dung API (không có DOM): trường nhập = trường lá trong body ghi / tham số truy vấn lọc;
    bảng = phản hồi dạng danh sách; ô chọn = danh sách nhỏ (danh mục); nút = thao tác ghi khác nhau + nút xem."""
    write_fields, filters, lists = 0, 0, []
    ops: dict[str, str] = {}
    for f in flows:
        method = (f.get("method") or "GET").upper()
        path = urlparse(f.get("url", "")).path
        if method in ("POST", "PUT", "PATCH", "DELETE") and classify(method, f.get("url", "")) != "read":
            write_fields = max(write_fields, body_fields(f.get("post_data"), f.get("req_ctype", "")))
            ops[f"{method} {_ID_SEG.sub('/{id}', path)}"] = _op_label(method, path)
        else:
            q = urlparse(f.get("url", "")).query
            if q:
                filters = max(filters, len([k for k, _ in parse_qsl(q) if k.lower() not in
                                            ("page", "pageindex", "pagesize", "skip", "take", "limit", "offset", "_", "t")]))
            if method in ("POST", "PUT") and f.get("post_data"):
                filters = max(filters, body_fields(f.get("post_data"), f.get("req_ctype", "")))
            n = int(f.get("rows") or 0)
            if n > 0:
                lists.append(n)
    lists.sort(reverse=True)
    small = [n for n in lists[1:] if n <= 50]
    crud = sorted({lab for lab in ops.values() if lab})
    return {"forms": 1 if (ops or write_fields) else 0,
            "inputs": min(60, write_fields + filters),
            "selects": min(10, len(small)),
            "buttons": len(ops) + (1 if lists else 0),
            "tables": (1 if lists else 0) + len([n for n in lists[1:] if n > 50]),
            "table_rows": lists[0] if lists else 0,
            "crud": crud}


def flow_to_request(f: dict, page_url: str) -> dict:
    """flow -> dict tương thích models.CapturedRequest (resource_type luôn 'xhr' để bước 5 coi là API)."""
    return {"method": (f.get("method") or "GET").upper(), "url": f["url"], "resource_type": "xhr",
            "status": f.get("status"), "duration_ms": f.get("duration_ms"), "content_type": f.get("ctype") or "",
            "post_data": f.get("post_data"), "response_size": f.get("resp_size"), "page_url": page_url,
            "auth_sig": f.get("auth_sig", ""), "token_path": f.get("token_path", ""), "token_sig": f.get("token_sig", ""),
            "headers": f.get("headers") or {}, "blocked": bool(f.get("blocked")), "origin": "manual",
            "sensitive": bool(f.get("sensitive"))}


def build_sessions(groups: list[dict], started: str = "", source: str = "winforms") -> list[dict]:
    """Nhóm màn hình -> các 'lần ghi' (mỗi phân hệ 1 lần) theo định dạng crawl/record.json của Web."""
    by_mod: dict[str, list[dict]] = {}
    for g in groups:
        flows = [f for f in g["flows"] if not f.get("login")]       # request đăng nhập không phải màn hình nghiệp vụ
        if not flows:
            continue
        url = screen_url(g["module"], g["screen"])
        seg = {"kind": "page", "url": url, "page_url": url, "title": g["screen"], "stats": screen_stats(flows),
               "requests": [flow_to_request(f, url) for f in flows], "trigger": "", "from_url": ""}
        by_mod.setdefault(g["module"], []).append(seg)
    t = started or now()
    return [{"started": t if i == 0 else f"{t}#{i}", "finished": now(), "module": mod, "guard": source,
             "start_url": "", "segments": segs, "events": []} for i, (mod, segs) in enumerate(by_mod.items())]


def append_record(record_file: Path, sessions: list[dict]) -> None:
    """Nối các lần ghi vào record.json (không ghi đè lần ghi cũ)."""
    from .manual import load_record
    data = load_record(record_file)
    data["sessions"].extend(sessions)
    tmp = record_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(record_file)


# ---------------------------------------------------------------- xác thực
def extract_auth(flows: list[dict], base_url: str = "") -> AuthCapture:
    """Request đăng nhập (POST có mật khẩu) -> mẫu body an toàn + đường dẫn token; Authorization cuối cùng -> header tĩnh
    (chỉ trả về trong bộ nhớ; caller ghi vào crawl/auth.json riêng tư, không vào project.json)."""
    auth = AuthCapture()
    for f in sorted(flows, key=lambda x: x.get("t") or 0):
        lg = f.get("login")
        if lg and lg.get("template") and not auth.login_url:
            auth.login_method = f.get("method", "POST")
            auth.login_url = f["url"]
            auth.login_content_type = lg.get("ctype", "")
            auth.login_body_template = lg["template"]
            auth.token_json_path = lg.get("token_path", "") or ""
    last = ""
    for f in sorted(flows, key=lambda x: x.get("t") or 0):
        if f.get("_authorization"):
            last = f["_authorization"]
    if last:
        auth.static_headers = {"Authorization": last}
    return auth


def find_login_notes(flows: list[dict]) -> list[str]:
    return [n for f in flows for n in ((f.get("login") or {}).get("notes") or [])]


def api_hosts(flows: Iterable[dict]) -> dict[str, int]:
    """Máy chủ nào nhận nhiều request nhất (gợi ý URL API cho bước 1)."""
    cnt: dict[str, int] = {}
    for f in flows:
        if is_api_flow(f):
            u = urlparse(f.get("url", ""))
            if u.scheme and u.netloc:
                k = f"{u.scheme}://{u.netloc}"
                cnt[k] = cnt.get(k, 0) + 1
    return dict(sorted(cnt.items(), key=lambda kv: -kv[1]))
