"""Web demo cục bộ để thử PerfTool (chỉ dùng thư viện chuẩn).

    python samples/demo_server.py          -> http://127.0.0.1:8088
    Tài khoản: admin / Admin@123 và user01 … user10 / Test@123
    API /api/reports/summary giới hạn 4 request/giây cho mỗi tài khoản (mô phỏng rate-limit -> HTTP 429)
"""
from __future__ import annotations

import json
import random
import secrets
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

USERS = {"admin": "Admin@123", **{f"user{i:02d}": "Test@123" for i in range(1, 11)}}
TOKENS: dict[str, str] = {}            # token -> tài khoản
LOGINS: dict[str, int] = {}            # số lần đăng nhập theo tài khoản (để kiểm tra phân bổ tài khoản)
RATE_LIMIT = {"/api/reports/summary": 4}   # số request/giây tối đa cho MỖI tài khoản (mô phỏng rate-limit)
_hits: dict[tuple, list[float]] = {}
WRITES = {"save": 0, "delete": 0, "complete": 0, "assign": 0}   # đếm API ghi (kiểm tra crawler không ghi nhầm)

# Nút mở popup: nhãn -> (tiêu đề popup, API gọi khi mở, số trường nhập)
POPUPS = {"Thêm mới": ("Thêm mới công việc", "/api/departments", 4),
          "Sửa": ("Cập nhật công việc", "/api/tasks/detail?id=1", 4),
          "Xem chi tiết": ("Chi tiết công việc", "/api/tasks/detail?id=1", 0)}
MODAL_JS = """
function hdr(){return {'Authorization':'Bearer '+localStorage.getItem('token'),'Content-Type':'application/json'};}
function openModal(title, api, nFields){
  fetch(api,{headers:hdr()});
  let f=''; for(let i=1;i<=nFields;i++) f+='<p><label>Trường '+i+'</label> <input name="f'+i+'"></p>';
  if(nFields===0) f='<table><tr><td>Mã</td><td>CV-001</td></tr><tr><td>Nội dung</td><td>Chi tiết công việc mẫu dùng '+
                    'cho kiểm thử quét popup của PerfTool</td></tr><tr><td>Trạng thái</td><td>Đang xử lý</td></tr></table>';
  const m=document.createElement('div'); m.id='modal'; m.setAttribute('role','dialog');
  m.style='position:fixed;top:80px;left:30%;width:40%;background:#fff;border:1px solid #888;padding:16px;z-index:9';
  m.innerHTML='<h3>'+title+'</h3>'+f+'<button onclick="saveTask()">Lưu</button> <button class="close" onclick="closeModal()">Đóng</button>';
  document.body.appendChild(m);
}
function closeModal(){const m=document.getElementById('modal'); if(m) m.remove();}
function saveTask(){fetch('/api/tasks/save',{method:'POST',headers:hdr(),body:'{}'}); closeModal();}
function closeMenu(){const m=document.getElementById('rowmenu'); if(m) m.remove();}
function rowMenu(ev, id){
  // menu ⋮ của từng dòng (giống Angular Material: nằm trong cdk-overlay-pane, role=menu)
  ev.stopPropagation(); closeMenu();
  const r=ev.currentTarget.getBoundingClientRect();
  const m=document.createElement('div'); m.id='rowmenu'; m.className='cdk-overlay-pane';
  m.style='position:fixed;top:'+(r.bottom)+'px;left:'+(r.left-150)+'px;background:#fff;border:1px solid #ccc;z-index:10';
  const items=[['edit_note','Xử lý',"openModal('Xử lý công việc','/api/tasks/detail?id="+id+"&act=process',3)"],
               ['event','Xin gia hạn',"openModal('Xin gia hạn','/api/tasks/extend-info?id="+id+"',2)"],
               ['group_add','Phân công',"location.href='/cong-viec/phan-cong?id="+id+"'"],
               ['done','Hoàn thành',"completeTask("+id+")"],
               ['delete','Xóa',"deleteTask()"]];
  m.innerHTML='<div role="menu" class="mat-mdc-menu-panel">'+items.map(i=>'<button role="menuitem" class="mat-mdc-menu-item" '+
    'style="display:block;width:170px;text-align:left" onclick="closeMenu();'+i[2]+'"><mat-icon class="material-icons">'+i[0]+
    '</mat-icon> <span>'+i[1]+'</span></button>').join('')+'</div>';
  document.body.appendChild(m);
}
function completeTask(id){fetch('/api/tasks/complete?id='+id,{method:'POST',headers:hdr(),body:'{}'});}
function showTab(el, status){document.querySelectorAll('[role=tab]').forEach(t=>t.setAttribute('aria-selected','false'));
  el.setAttribute('aria-selected','true'); fetch('/api/tasks?page=1&size=20&status='+status,{headers:hdr()});}
function doSearch(){fetch('/api/tasks/search',{method:'POST',headers:hdr(),body:JSON.stringify({keyword:'',page:1})});}
function gotoPage(n){fetch('/api/tasks?page='+n+'&size=20',{headers:hdr()});}
document.addEventListener('click',()=>closeMenu());
function deleteTask(){fetch('/api/tasks/delete?id=1',{method:'POST',headers:hdr(),body:'{}'});}
document.addEventListener('keydown',e=>{if(e.key==='Escape'){ closeModal(); closeMenu(); }});
"""

MENU = [
    ("Quản lý công việc", [("Danh sách công việc", "/cong-viec/danh-sach"), ("Thống kê giao việc", "/cong-viec/thong-ke")]),
    ("Quản lý văn bản", [("Danh sách văn bản đến", "/van-ban/danh-sach"), ("Tra cứu văn bản", "/van-ban/tra-cuu")]),
    ("Báo cáo", [("Báo cáo tổng hợp", "/bao-cao/tong-hop")]),
]
PAGES = {
    "/cong-viec/danh-sach": ("Danh sách công việc", [("GET", "/api/tasks?page=1&size=20")],
                             ["Thêm mới", "Sửa", "Xem chi tiết", "Xóa"], 20),
    "/cong-viec/thong-ke": ("Thống kê giao việc theo phòng ban", [("GET", "/api/tasks/stats"), ("GET", "/api/departments")],
                            ["Xuất Excel"], 8),
    "/van-ban/danh-sach": ("Danh sách văn bản đến", [("GET", "/api/docs?page=1")], ["Thêm mới", "Chuyển xử lý"], 15),
    "/van-ban/tra-cuu": ("Tra cứu văn bản", [("POST", "/api/docs/search"), ("GET", "/api/categories")], ["Tìm kiếm"], 10),
    "/bao-cao/tong-hop": ("Báo cáo tổng hợp", [("GET", "/api/reports/summary"), ("GET", "/api/reports/chart"),
                                               ("GET", "/api/departments")], ["Xuất Excel", "In"], 12),
}
LATENCY = {"/api/tasks": (40, 120), "/api/tasks/stats": (150, 400), "/api/departments": (10, 40),
           "/api/docs": (60, 150), "/api/docs/search": (120, 350), "/api/categories": (10, 30),
           "/api/reports/summary": (300, 800), "/api/reports/chart": (200, 500), "/api/auth/login": (50, 120)}

CSS = """body{font-family:Segoe UI,Arial;margin:0;display:flex}nav{width:240px;background:#1f2d3d;color:#fff;min-height:100vh;padding:12px}
nav a{color:#cfd8e3;display:block;padding:6px 14px;text-decoration:none}nav .g{margin-top:12px;font-weight:bold}
main{padding:20px;flex:1}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px}
button{margin-right:6px;padding:6px 12px}"""


def layout(title: str, body: str) -> str:
    nav = "".join(f"<li class='sub'><span class='g'>{g}</span><ul>" +
                  "".join(f"<li><a href='{u}'>{t}</a></li>" for t, u in items) + "</ul></li>" for g, items in MENU)
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{CSS}</style></head><body>"
            f"<nav><b>DEMO ERP</b><ul style='list-style:none;padding:0'>{nav}</ul><a href='/logout'>Đăng xuất</a></nav>"
            f"<main><h1>{title}</h1>{body}</main></body></html>")


ASSIGN_HTML = """<form onsubmit='return false'><p><label>Người xử lý</label> <select id='u'></select></p>
<p><label>Hạn xử lý</label> <input type='date'></p><p><label>Ghi chú</label> <textarea></textarea></p>
<button onclick="fetch('/api/tasks/assign',{method:'POST',headers:hdr(),body:'{}'})">Lưu phân công</button></form>
<table><tr><th>Cán bộ</th><th>Đơn vị</th></tr><tr><td>Nguyễn Văn A</td><td>Phòng 1</td></tr></table>"""


def page_html(path: str) -> str:
    if path == "/cong-viec/phan-cong":          # trang con chỉ mở từ menu ⋮ › Phân công (không có trên menu trái)
        return layout("Phân công xử lý công việc", ASSIGN_HTML + f"<script>{MODAL_JS}"
                      "fetch('/api/users',{headers:hdr()});fetch('/api/tasks/detail?id=1',{headers:hdr()});</script>")
    title, apis, buttons, rows = PAGES[path]
    def _btn(b: str) -> str:
        if b in POPUPS:
            t, api, n = POPUPS[b]
            return f"<button onclick=\"openModal('{t}','{api}',{n})\">{b}</button>"
        if b == "Xóa":
            return "<button onclick=\"deleteTask()\">Xóa</button>"      # xoá ngay, không hỏi lại (nguy hiểm)
        return f"<button>{b}</button>"
    btns = "".join(_btn(b) for b in buttons)
    filters = "<form onsubmit='return false'><input placeholder='Từ khoá'><select><option>Tất cả</option></select></form>"
    table = "<table><thead><tr><th>STT</th><th>Nội dung</th><th>Trạng thái</th></tr></thead><tbody>" + "".join(
        f"<tr><td>{i}</td><td>Bản ghi {i}</td><td>Đang xử lý</td></tr>" for i in range(1, rows + 1)) + "</tbody></table>"
    if path == "/cong-viec/danh-sach":
        # giống màn hình "Việc cần xử lý": tab trạng thái, nút Tìm kiếm, menu ⋮ mỗi dòng, phân trang
        filters = (filters + "<div role='tablist'><button role='tab' aria-selected='true' onclick=\"showTab(this,'all')\">"
                   "Tất cả</button><button role='tab' aria-selected='false' onclick=\"showTab(this,'overdue')\">Quá hạn"
                   "</button><button role='tab' aria-selected='false' onclick=\"showTab(this,'done')\">Đã hoàn thành"
                   "</button></div><p><button type='button' onclick='doSearch()'>🔍 Tìm kiếm</button></p>")
        table = ("<table><thead><tr><th>STT</th><th>Nội dung</th><th>Trạng thái</th><th></th></tr></thead><tbody>" + "".join(
            f"<tr><td>{i}</td><td>Bản ghi {i}</td><td>Đang xử lý</td><td><button class='mat-mdc-icon-button' "
            f"aria-haspopup='menu' onclick='rowMenu(event,{i})'><mat-icon class='material-icons'>more_vert</mat-icon>"
            f"</button></td></tr>" for i in range(1, rows + 1)) + "</tbody></table>"
            "<div class='pagination'><button class='active'>1</button><button onclick='gotoPage(2)'>2</button>"
            "<button aria-label='Next page' onclick='gotoPage(2)'>›</button></div>")
    post_body = ',body:JSON.stringify({keyword:"",page:1})'    # tách khỏi f-string: Python < 3.12 cấm \ trong biểu thức
    calls = "".join(
        f"fetch('{u}',{{method:'{m}',headers:{{'Authorization':'Bearer '+localStorage.getItem('token'),"
        f"'Content-Type':'application/json'}}{post_body if m == 'POST' else ''}}});"
        for m, u in apis)
    return layout(title, f"{filters}<p>{btns}</p>{table}<script>{MODAL_JS}{calls}</script>")


LOGIN_HTML = """<!doctype html><html><head><meta charset='utf-8'><title>Đăng nhập</title></head><body style='font-family:Segoe UI'>
<h2>Đăng nhập hệ thống</h2><form id='f'><input name='username' placeholder='Tên đăng nhập'><br><br>
<input type='password' name='password' placeholder='Mật khẩu'><br><br><button type='submit'>Đăng nhập</button><p id='e' style='color:red'></p></form>
<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();const fd=new FormData(e.target);
const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({username:fd.get('username'),password:fd.get('password')})});
if(r.ok){const j=await r.json();localStorage.setItem('token',j.data.accessToken);location.href='/home';}
else document.getElementById('e').innerText='Sai tài khoản';};</script></body></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # im lặng
        pass

    def _send(self, code: int, body: str | dict, ctype: str = "text/html; charset=utf-8", headers: dict | None = None):
        data = (json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else body).encode("utf-8")
        if isinstance(body, dict):
            ctype = "application/json; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _user(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] in TOKENS:
            return TOKENS[auth[7:]]
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return TOKENS.get(c["sid"].value) if "sid" in c else None

    def _authed(self) -> bool:
        return self._user() is not None

    def _rate_limited(self, path: str) -> bool:
        limit = RATE_LIMIT.get(path)
        if not limit:
            return False
        now = time.time()
        key = (path, self._user())
        win = [t for t in _hits.get(key, []) if now - t < 1.0]
        win.append(now)
        _hits[key] = win
        return len(win) > limit

    def _delay(self, path: str):
        lo, hi = LATENCY.get(path, (5, 20))
        time.sleep(random.uniform(lo, hi) / 1000)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/login":
            return self._send(200, LOGIN_HTML)
        if path == "/logout":
            return self._send(302, "", headers={"Location": "/login", "Set-Cookie": "sid=; Max-Age=0; Path=/"})
        if path == "/api/_stats":            # thống kê cho kiểm thử (không cần đăng nhập)
            return self._send(200, {"logins": LOGINS, "writes": WRITES})
        if path.startswith("/api/"):
            self._delay(path)
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            if self._rate_limited(path):
                return self._send(429, {"error": "too many requests"})
            if random.random() < 0.003:
                return self._send(500, {"error": "random failure"})
            n = int(parse_qs(u.query).get("size", ["20"])[0])
            return self._send(200, {"data": [{"id": i, "name": f"Item {i}"} for i in range(n)], "path": path})
        if not self._authed():
            return self._send(302, "", headers={"Location": "/login"})
        self._delay(path)
        if path in ("/", "/home"):
            return self._send(200, layout("Trang chủ", "<p>Chào mừng!</p>"))
        if path in PAGES or path == "/cong-viec/phan-cong":
            return self._send(200, page_html(path))
        return self._send(404, layout("Không tìm thấy", ""))

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        self._delay(u.path)
        if u.path == "/api/auth/login":
            try:
                d = json.loads(raw or "{}")
            except json.JSONDecodeError:
                d = {k: v[0] for k, v in parse_qs(raw).items()}
            if USERS.get(d.get("username")) == d.get("password"):
                tok = secrets.token_hex(24)
                TOKENS[tok] = d["username"]
                LOGINS[d["username"]] = LOGINS.get(d["username"], 0) + 1
                return self._send(200, {"data": {"accessToken": tok, "user": d["username"]}},
                                  headers={"Set-Cookie": f"sid={tok}; Path=/; HttpOnly"})
            return self._send(401, {"error": "invalid credentials"})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        if u.path == "/api/tasks/save":
            WRITES["save"] += 1
        if u.path == "/api/tasks/delete":
            WRITES["delete"] += 1
        if u.path == "/api/tasks/complete":
            WRITES["complete"] += 1
        if u.path == "/api/tasks/assign":
            WRITES["assign"] += 1
        return self._send(200, {"data": [], "total": 0})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 8088), H)
    srv.daemon_threads = True
    print("Demo server: http://127.0.0.1:8088  (admin / Admin@123, user01..user10 / Test@123)")
    srv.serve_forever()
