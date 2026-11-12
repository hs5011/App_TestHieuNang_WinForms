"""2. accounts: danh sách dán / CSV / Excel có dòng trống, trùng, khoảng trắng, dấu phân cách; mật khẩu không lọt vào
project.json; keyring được mock; kiểm tra đăng nhập qua máy chủ localhost giả lập; escape file .properties cho JMeter."""
from __future__ import annotations

import http.server
import json
import sys
import threading
import types
from urllib.parse import parse_qs

import openpyxl
import pytest

from perftool import accounts as acc
from perftool import storage
from perftool.models import AuthCapture, Project
from perftool.runner import executor

SECRET = "S3cr€t-Mật-khẩu!\"\\"


# ------------------------------------------------------------------ parse_text
def test_parse_text_blank_whitespace_duplicates():
    text = "\n   \n  user01 ,  p1  \n\tuser02\t;\tp2\nuser01|p1-new\n  ,orphan\n#u3,x\n"
    assert acc.parse_text(text) == [("user01", "p1-new"), ("user02", "p2")]


def test_parse_text_separator_inside_password_and_quotes():
    got = dict(acc.parse_text('u1,a,b;c|d\n"u2","x,y"\nu3;;\n'))
    assert got == {"u1": "a,b;c|d", "u2": "x,y", "u3": ";"}


def test_parse_text_user_without_password_and_empty_input():
    assert acc.parse_text("lonely") == [("lonely", "")]
    assert acc.parse_text("") == [] and acc.parse_text(None) == []  # type: ignore[arg-type]


@pytest.mark.parametrize("hdr", ["Tài khoản;Mật khẩu", "Tên đăng nhập,Mật khẩu", "email|pass", "USER NAME\tPASSWORD"])
def test_parse_text_vietnamese_and_mixed_headers(hdr):
    assert acc.parse_text(f"{hdr}\nu1,p1") == [("u1", "p1")]


def test_parse_text_unicode_username_kept():
    assert acc.parse_text("nguyễn.văn.a@sở.gov.vn,Mk@2026") == [("nguyễn.văn.a@sở.gov.vn", "Mk@2026")]


# ------------------------------------------------------------------ parse_file
def test_parse_file_csv_blank_rows_duplicates_and_bom():
    data = "﻿username,password\n\n  u1 , p1 \n,,\nu2,p2\nu1,p1b\n".encode("utf-8")
    assert acc.parse_file(data, "a.csv") == [("u1", "p1b"), ("u2", "p2")]


def test_parse_file_reordered_columns_and_extra_cols():
    data = "Ghi chú;Mật khẩu;Họ tên;Tài khoản\nx;p1;A;u1\ny;p2;B;u2\n".encode("utf-8")
    assert acc.parse_file(data, "a.csv") == [("u1", "p1"), ("u2", "p2")]


def test_parse_file_short_rows_do_not_crash():
    data = b"username,password\nu1\nu2,p2\n"
    assert acc.parse_file(data, "a.csv") == [("u1", ""), ("u2", "p2")]


def test_parse_file_empty_and_whitespace_only():
    assert acc.parse_file(b"", "a.csv") == []
    assert acc.parse_file(b"  \n ,  \n", "a.csv") == []


def test_parse_file_cp1258_text():
    # "Tài khoản,Mật khẩu" theo kiểu Windows-1258: nguyên âm dựng sẵn + dấu thanh tổ hợp (U+0300, U+0309, U+0323)
    text = "Tài khỏn,Mật khẩu\nu1,p1\n"
    assert acc.parse_file(text.encode("cp1258"), "a.txt") == [("u1", "p1")]


def test_parse_file_excel_numbers_blanks(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Tài khoản", "Mật khẩu"])
    ws.append(["u1", 123456])            # mật khẩu kiểu số trong Excel
    ws.append([None, None])
    ws.append(["  u2  ", " p2 "])
    ws.append(["u1", "moi"])
    f = tmp_path / "a.xlsx"
    wb.save(f)
    assert acc.parse_file(f.read_bytes(), "a.xlsx") == [("u1", "moi"), ("u2", "p2")]
    wb2 = openpyxl.Workbook()
    wb2.active.append(["u9", "123"])
    wb2.save(tmp_path / "b.xlsx")
    assert acc.parse_file((tmp_path / "b.xlsx").read_bytes(), "b.xlsx") == [("u9", "123")]


def test_parse_file_excel_numeric_password_keeps_digits(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append(["username", "password"])
    wb.active.append(["u1", 123456])
    f = tmp_path / "n.xlsx"
    wb.save(f)
    got = dict(acc.parse_file(f.read_bytes(), "n.xlsx"))
    assert got["u1"] == "123456"                 # không thành "123456.0"


# ------------------------------------------------------------------ mật khẩu không lưu vào project.json
def test_password_never_serialized_to_project_json(tmp_path):
    p = Project(id="secret-check")
    p.login.base_url = "http://127.0.0.1:9"
    p.login.username = "admin"
    p.login.extra_accounts = ["u1", "u2"]
    assert "password" not in p.login.model_dump()          # LoginConfig không có trường mật khẩu
    for field in ("password", "pwd", "passwd"):
        with pytest.raises(Exception):
            p.login.__setattr__(field, SECRET)          # pydantic chặn gán trường lạ
    # cố tình đưa mật khẩu qua dict dữ liệu nhập -> trường lạ bị bỏ qua khi validate
    p2 = Project.model_validate({"id": "x", "login": {"username": "a", "password": SECRET}})
    storage.save_project(p)
    storage.save_project(p2)
    for pid in ("secret-check", "x"):
        raw = (storage.PROJECTS_DIR / pid / "project.json").read_text(encoding="utf-8")
        assert SECRET not in raw and json.dumps(SECRET)[1:-1] not in raw
    assert str(tmp_path) in str(storage.PROJECTS_DIR)         # đúng thư mục tạm, không đụng workspace thật


# ------------------------------------------------------------------ keyring (mock)
@pytest.fixture
def fake_keyring(monkeypatch):
    store: dict = {}
    mod = types.ModuleType("keyring")
    mod.set_password = lambda s, k, v: store.__setitem__((s, k), v)
    mod.get_password = lambda s, k: store.get((s, k))

    def _del(s, k):
        if (s, k) not in store:
            raise KeyError(k)
        del store[(s, k)]
    mod.delete_password = _del
    monkeypatch.setitem(sys.modules, "keyring", mod)
    return store


def test_keyring_roundtrip_is_scoped_per_project(fake_keyring):
    assert storage.save_password("p1", "u1", SECRET)
    assert fake_keyring == {("PerfTool", "p1:u1"): SECRET}
    assert storage.load_password("p1", "u1") == SECRET
    assert storage.load_password("p2", "u1") == ""          # dự án khác không đọc được
    storage.delete_password("p1", "u1")
    storage.delete_password("p1", "u1")                     # xoá lần 2 không lỗi
    assert storage.load_password("p1", "u1") == ""


def test_keyring_failures_are_swallowed(monkeypatch):
    mod = types.ModuleType("keyring")

    def boom(*a, **k):
        raise RuntimeError("no backend")
    mod.set_password = mod.get_password = mod.delete_password = boom
    monkeypatch.setitem(sys.modules, "keyring", mod)
    assert storage.save_password("p", "u", "x") is False
    assert storage.load_password("p", "u") == ""
    storage.delete_password("p", "u")


# ------------------------------------------------------------------ check_login qua máy chủ localhost
class _Handler(http.server.BaseHTTPRequestHandler):
    received: list = []
    reply: tuple = (200, b'{"data":{"token":"T"}}')

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Handler.received.append((self.headers.get("Content-Type"), body))
        code, payload = _Handler.reply
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Handler.received = []
    _Handler.reply = (200, b'{"data":{"token":"T"}}')
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_check_login_json_escapes_special_chars(local_server):
    auth = AuthCapture(login_url=local_server + "/login", login_content_type="application/json",
                       login_body_template='{"u":"{{USERNAME}}","p":"{{PASSWORD}}"}', token_json_path="data.token")
    res = acc.check_login(auth, 'ad"min', SECRET, timeout=5)
    assert res["ok"], res
    body = json.loads(_Handler.received[-1][1].decode("utf-8"))
    assert body == {"u": 'ad"min', "p": SECRET}


def test_check_login_form_urlencoded(local_server):
    auth = AuthCapture(login_url=local_server + "/login", login_content_type="application/x-www-form-urlencoded",
                       login_body_template="u={{USERNAME}}&p={{PASSWORD}}")
    res = acc.check_login(auth, "a&b=c", "p+q& r", timeout=5)
    assert res["ok"]
    q = parse_qs(_Handler.received[-1][1].decode())
    assert q == {"u": ["a&b=c"], "p": ["p+q& r"]}


def test_check_login_2xx_without_token_and_http_401(local_server):
    auth = AuthCapture(login_url=local_server + "/login", login_content_type="application/json",
                       login_body_template='{"u":"{{USERNAME}}"}', token_json_path="data.token")
    _Handler.reply = (200, b"<html>login page</html>")
    assert acc.check_login(auth, "u", "p", timeout=5)["ok"] is False
    _Handler.reply = (401, b"{}")
    r = acc.check_login(auth, "u", "p", timeout=5)
    assert r["ok"] is False and r["status"] == 401


def test_check_login_without_captured_api():
    r = acc.check_login(AuthCapture(), "u", "p")
    assert r["ok"] is False and r["status"] is None


# ------------------------------------------------------------------ file secrets.properties (JMeter, 1 tài khoản)
def _java_load_value(line: str) -> str:
    """Mô phỏng java.util.Properties.load(InputStream): đọc ISO-8859-1, bỏ khoảng trắng đầu giá trị, xử lý escape."""
    key, _, rest = line.partition("=")
    rest = rest.lstrip(" \t\f")
    out, i = [], 0
    while i < len(rest):
        c = rest[i]
        if c == "\\" and i + 1 < len(rest):
            n = rest[i + 1]
            if n == "u":
                out.append(chr(int(rest[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append({"n": "\n", "t": "\t", "r": "\r", "f": "\f"}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


@pytest.mark.parametrize("pwd", ["P@ss=word:1\\x", "abc#!def"])
def test_properties_escape_ascii_roundtrip(pwd):
    line = f"PERF_PASSWORD={executor._prop(pwd)}"
    assert _java_load_value(line.encode("utf-8").decode("latin-1")) == pwd


@pytest.mark.parametrize("pwd", ["Mật khẩu@2026", "  leading-space", "€uro"])
def test_properties_escape_non_ascii_and_leading_space(pwd):
    """[Hồi quy – lỗi đã sửa 2026-09-25] executor ghi secrets.properties bằng UTF-8, nhưng JMeter (-q) nạp bằng Properties.load(InputStream)
    = ISO-8859-1 và bỏ khoảng trắng đầu giá trị -> mật khẩu tiếng Việt/khoảng trắng đầu bị sai khi chạy JMeter 1 tài khoản."""
    line = f"PERF_PASSWORD={executor._prop(pwd)}"
    assert _java_load_value(line.encode("utf-8").decode("latin-1")) == pwd
