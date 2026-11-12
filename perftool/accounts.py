"""Quản lý nhiều tài khoản kiểm thử: đọc danh sách, kiểm tra đăng nhập, ghi/dọn file tạm cho k6/JMeter.

Nguyên tắc: mật khẩu KHÔNG được lưu vào project.json. Danh sách tài khoản chỉ được ghi ra file tạm ngay trước khi
chạy test (accounts.json cho k6, accounts.csv cho JMeter) và bị xoá ngay sau đó – kể cả khi người dùng bấm Dừng.
"""
from __future__ import annotations

import csv
import io
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Union

from unidecode import unidecode

from . import config
from .models import AuthCapture

Account = tuple[str, str]

USER_ALIASES = {"username", "user", "user name", "login", "tai khoan", "ten dang nhap", "ten tai khoan", "email",
                "account", "userid", "user id", "tendangnhap", "taikhoan"}
PASS_ALIASES = {"password", "pass", "pwd", "mat khau", "matkhau", "passwd"}
SECRET_FILES = ("accounts.json", "accounts.csv", "secrets.properties")


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", unidecode(str(s or "")).lower())).strip()


def _dedupe(accounts: list[Account]) -> list[Account]:
    """Giữ lần xuất hiện cuối của mỗi tài khoản (cho phép cập nhật mật khẩu), bỏ dòng trống."""
    out: dict[str, str] = {}
    for u, p in accounts:
        u = (u or "").strip()
        if u:
            out[u] = p if p is not None else ""
    return list(out.items())


def parse_text(text: str) -> list[Account]:
    """Mỗi dòng: `tài_khoản,mật_khẩu` (chấp nhận dấu phẩy, chấm phẩy, tab hoặc |). Dòng tiêu đề bị bỏ qua."""
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.*?)\s*[,;\t|]\s*(.*)$", line)
        user, pwd = (m.group(1), m.group(2)) if m else (line, "")
        if _norm(user) in USER_ALIASES and _norm(pwd) in PASS_ALIASES:
            continue
        rows.append((user.strip().strip('"'), pwd.strip().strip('"') if pwd else ""))
    return _dedupe(rows)


def parse_file(data: bytes, filename: str) -> list[Account]:
    """CSV/TXT/Excel. Tự tìm cột tài khoản & mật khẩu theo tên cột; nếu không có tiêu đề dùng 2 cột đầu."""
    name = filename.lower()
    delim = ""          # Excel: không tách chuỗi
    if name.endswith((".xlsx", ".xls")):
        import pandas as pd
        df = pd.read_excel(io.BytesIO(data), header=None, dtype=str).fillna("")
        rows = df.values.tolist()
    else:
        text = None
        # Excel "Unicode Text (.txt)" lưu UTF-16 có BOM FF FE
        encs = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1258", "latin1")
        for enc in encs:
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("Không đọc được file")
        delim = _first_delim(text)
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [[str(c).strip() for c in r] for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return []
    header = [_norm(c) for c in rows[0]]
    ui = next((i for i, h in enumerate(header) if h in USER_ALIASES), None)
    pi = next((i for i, h in enumerate(header) if h in PASS_ALIASES), None)
    body = rows[1:] if ui is not None or pi is not None else rows
    ncol = len(rows[0])
    ui = 0 if ui is None else ui
    pi = (1 if ui != 1 else 0) if pi is None else pi

    def pwd_of(r: list[str]) -> str:
        if pi >= len(r):
            return ""
        # mật khẩu ở cột cuối chứa dấu phân cách mà không đặt trong "…" -> ghép lại phần bị tách
        if delim and pi == ncol - 1 and len(r) > ncol:
            return delim.join(r[pi:])
        return r[pi]
    return _dedupe([(r[ui] if ui < len(r) else "", pwd_of(r)) for r in body])


def _first_delim(text: str) -> str:
    """Dấu phân cách = ký tự phân cách xuất hiện ĐẦU TIÊN trên dòng đầu (tên tài khoản không chứa dấu phân cách;
    csv.Sniffer đoán sai khi mật khẩu chứa ',' hoặc file dùng '|')."""
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    pos = {d: first.find(d) for d in ("\t", ";", "|", ",") if d in first}
    return min(pos, key=pos.get) if pos else ","


# ------------------------------------------------------------------ kiểm tra đăng nhập
def _get_path(obj, path: str):
    for k in path.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def check_login(auth: AuthCapture, username: str, password: str, timeout: int = 30, insecure: bool = False) -> dict:
    """Gửi 1 request đăng nhập theo mẫu đã bắt được ở bước 3. Trả về {ok, status, ms, message}."""
    if not auth.login_url or not auth.login_body_template:
        return {"ok": False, "status": None, "ms": None, "message": "Chưa nhận diện được API đăng nhập (chạy bước 3)"}
    ctype = auth.login_content_type or "application/json"
    if "x-www-form-urlencoded" in ctype:
        from urllib.parse import quote_plus
        u, p = quote_plus(username), quote_plus(password)
    elif "json" in ctype:
        u, p = json.dumps(username)[1:-1], json.dumps(password)[1:-1]   # escape ký tự đặc biệt trong JSON
    else:
        u, p = username, password
    body = auth.login_body_template.replace("{{USERNAME}}", u).replace("{{PASSWORD}}", p).encode("utf-8")
    req = urllib.request.Request(auth.login_url, data=body, method=auth.login_method or "POST",
                                 headers={"Content-Type": ctype, "User-Agent": "PerfTool-AccountCheck"})
    ctx = ssl.create_default_context()
    if insecure or config.insecure_tls():
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE    # giống insecureSkipTLSVerify của script k6 (môi trường kiểm thử)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            ms = round((time.perf_counter() - t0) * 1000)
            status = resp.status
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "ms": round((time.perf_counter() - t0) * 1000),
                "message": f"HTTP {e.code} – sai tài khoản/mật khẩu hoặc tài khoản bị khoá"
                           if e.code in (400, 401, 403) else f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        hint = (" – chứng chỉ HTTPS không hợp lệ; với máy chủ kiểm thử dùng chứng chỉ tự ký, tick 'Bỏ qua kiểm tra "
                "chứng chỉ HTTPS' ở Tuỳ chọn nâng cao") if "CERTIFICATE" in str(e).upper() else ""
        return {"ok": False, "status": None, "ms": None, "message": (f"Không kết nối được: {e}"[:200] + hint)}
    if auth.token_json_path:
        try:
            token = _get_path(json.loads(raw), auth.token_json_path)
        except json.JSONDecodeError:
            token = None
        if not token:
            return {"ok": False, "status": status, "ms": ms,
                    "message": "Máy chủ trả 2xx nhưng không có token – có thể sai mật khẩu"}
    return {"ok": 200 <= status < 400, "status": status, "ms": ms, "message": "Đăng nhập thành công"}


# ------------------------------------------------------------------ file tạm cho công cụ test
def write_secret_files(run_dir: Path, tool: str, accounts: list[Account]) -> list[Path]:
    """Ghi danh sách tài khoản ra file tạm trong thư mục lượt chạy. Trả về danh sách file cần xoá sau khi chạy."""
    files: list[Path] = []
    if not accounts:
        return files
    if tool == "k6":
        f = run_dir / "accounts.json"
        f.write_text(json.dumps([{"u": u, "p": p} for u, p in accounts], ensure_ascii=False), encoding="utf-8")
        files.append(f)
    else:
        f = run_dir / "accounts.csv"
        with open(f, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh, quoting=csv.QUOTE_ALL).writerows(accounts)
        files.append(f)
    return files


def cleanup_secret_files(root: Union[str, Path]) -> int:
    """Xoá mọi file tạm chứa tài khoản còn sót trong thư mục runs (vd. khi tiến trình bị dừng đột ngột)."""
    n = 0
    for name in SECRET_FILES:
        for f in Path(root).rglob(name):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n
