"""Đăng nhập & phân tích giao diện Web bằng Playwright.

Nguyên tắc an toàn: crawler điều hướng (GET) qua các link/menu và đọc DOM, KHÔNG bấm nút lưu/xoá, KHÔNG submit form
nghiệp vụ -> không làm thay đổi dữ liệu. Khi bật quét tương tác (interactions.py) crawler có bấm thử tab / Tìm kiếm /
chuyển trang / nút mở popup / mục menu ⋮ an toàn, kèm bộ chặn ghi (guard.py) huỷ request ghi dữ liệu ngoài ý muốn.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

from playwright.sync_api import BrowserContext, Page, Request, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from .. import config
from ..models import AuthCapture, CapturedRequest, LoginConfig, PageInfo
from .guard import WriteGuard, _compact, get_has_write, has_write_keyword, page_has_write
from .interactions import host_key, is_banned, page_key
from .login_capture import body_has_secret, has_secret_field, login_template, redact_body

Log = Callable[[str], None]

SESSION_LOST = "Bị chuyển về trang đăng nhập (hết phiên?)"
TOKEN_KEYS = re.compile(r"^(access_?token|token|jwt|id_?token|auth_?token|accesstoken|bearer)$", re.IGNORECASE)

# ------------------------------------------------------------------ JS helpers
JS_MENU_LINKS = r"""
() => {
  const containers = Array.from(document.querySelectorAll(
    'nav, aside, [role=navigation], [role=menu], .menu, .sidebar, .side-menu, .navbar, .ant-menu, .el-menu, .mat-sidenav, ' +
    'mat-drawer, mat-sidenav, [class*=sidebar], [class*=side-menu], [class*=top-menu], header, #sidebar, #menu'));
  const scope = containers.length ? containers : [document.body];
  function groupOf(a){
    let el = a.parentElement;
    while (el && el !== document.body){
      if (el.tagName === 'LI' || el.getAttribute('role') === 'menuitem' || el.classList.contains('ant-menu-submenu')){
        const sub = el.querySelector(':scope > ul, :scope > div > ul, :scope > [role=menu], :scope > .ant-menu-sub, :scope > div');
        if (sub && sub.contains(a)){
          const t = el.querySelector(':scope > a, :scope > span, :scope > div:first-child, :scope > button, :scope > .ant-menu-submenu-title');
          const tt = t ? (t.innerText || t.textContent || '').trim().split('\n')[0].trim() : '';
          if (t && !t.contains(a) && tt) return tt;
        }
      }
      el = el.parentElement;
    }
    return '';
  }
  const seen = new Set(); const out = [];
  for (const c of scope){
    for (const a of c.querySelectorAll('a[href]')){
      const href = a.href;
      // menu thu gọn chỉ còn icon: innerText rỗng -> lấy aria-label / title / tooltip / textContent ẩn
      const text = [a.innerText, a.getAttribute('aria-label'), a.title, a.getAttribute('mattooltip'),
                    a.getAttribute('data-original-title'), a.textContent]
                   .map(t => (t || '').trim().split('\n')[0].trim()).find(t => t) || '';
      if (!href || href.startsWith('javascript') || href.endsWith('#') || !text) continue;
      if (seen.has(href)) continue; seen.add(href);
      out.push({text, href, group: groupOf(a)});
    }
  }
  return out;
}
"""

JS_EXPAND_MENUS = r"""
() => {
  const sels = ['.ant-menu-submenu-title', 'nav [aria-expanded="false"]', 'aside [aria-expanded="false"]',
                '.el-submenu__title', '.treeview > a', '.has-sub > a', '.nav-item.dropdown > a', 'li.menu-item-has-children > a'];
  let n = 0;
  for (const s of sels){ for (const el of document.querySelectorAll(s)){
    const txt = (el.innerText||'').toLowerCase();
    if (/(log ?out|log ?off|sign ?out|sign ?off|đăng xuất|dang xuat|thoát|thoat)/.test(txt.normalize('NFC'))) continue;
    try { el.click(); n++; } catch(e){}
  }}
  return n;
}
"""

JS_PAGE_STATS = r"""
(crudWords) => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const q = s => Array.from(document.querySelectorAll(s)).filter(vis);
  const inputs = q('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea');
  const buttons = q('button, input[type=submit], input[type=button], a.btn, .ant-btn, [role=button]');
  const crud = [];
  for (const b of buttons){
    const t = (b.innerText || b.value || b.title || b.getAttribute('aria-label') || '').trim().toLowerCase();
    if (t && t.length < 40 && crudWords.some(w => t === w || t.startsWith(w + ' '))) crud.push(t.split('\n')[0]);
  }
  const tables = q('table, .ant-table, .el-table, [role=grid], .dataTables_wrapper');
  const rows = q('table tbody tr, [role=row]').length;
  const nav = performance.getEntriesByType('navigation')[0];
  return {
    title: document.title || '',
    h1: (document.querySelector('h1,h2,.page-title,.breadcrumb')||{}).innerText || '',
    forms: q('form').length, inputs: inputs.length, selects: q('select, .ant-select, .el-select').length,
    buttons: buttons.length, tables: tables.length, table_rows: rows,
    crud: Array.from(new Set(crud)).slice(0, 20),
    links: q('a[href]').length,
    nav_ms: nav ? nav.duration : null,
  };
}
"""

JS_CONTENT_LINKS = r"""
() => {
  const navs = Array.from(document.querySelectorAll('nav, aside, header, footer, [role=navigation], [role=menu], ' +
    '.sidebar, .menu, mat-drawer, mat-sidenav, [class*=sidebar], [class*=side-menu], [class*=top-menu]'));
  const out = []; const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')){
    if (navs.some(n => n.contains(a))) continue;
    const href = a.href;
    const text = (a.innerText || a.getAttribute('aria-label') || a.title || a.textContent || '').trim().split('\n')[0].trim();
    if (!href || href.startsWith('javascript') || href.endsWith('#') || seen.has(href)) continue;
    seen.add(href); out.push({text, href});
  }
  return out.slice(0, 50);
}
"""


JS_MODAL_STATS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const sels = '[role=dialog], [aria-modal=true], .modal.show, .modal.in, .ant-modal, mat-dialog-container, .el-dialog, ' +
               '.p-dialog, .swal2-popup, .k-window, .cdk-overlay-pane';
  // bỏ menu thả xuống / tooltip / danh sách chọn (cũng nằm trong cdk-overlay-pane) và thanh ghi thao tác của PerfTool
  const notPopup = '[role=menu], .mat-mdc-menu-panel, .mat-menu-panel, [role=tooltip], .mat-mdc-tooltip, .mat-tooltip, ' +
                   '[role=listbox], .mat-mdc-select-panel, .mat-mdc-autocomplete-panel, #perftool-rec-bar';
  const ms = Array.from(document.querySelectorAll(sels)).filter(vis)
    .filter(m => !m.matches(notPopup) && !m.querySelector(notPopup) && (m.innerText || '').trim());
  if (!ms.length) return null;
  const m = ms.sort((a, b) => (b.innerText || '').length - (a.innerText || '').length)[0];
  const q = s => Array.from(m.querySelectorAll(s)).filter(vis);
  const t = m.querySelector('h1, h2, h3, h4, .modal-title, .ant-modal-title, .mat-dialog-title, [mat-dialog-title], ' +
                            '.mat-mdc-dialog-title, .el-dialog__title, .p-dialog-title');
  return {title: ((t && t.innerText) || '').trim().slice(0, 120),
          inputs: q('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea').length,
          selects: q('select, .ant-select, mat-select, .mat-select, .el-select').length,
          buttons: q('button, [role=button]').length,
          tables: q('table, [role=grid], .ant-table, .mat-table, mat-table').length,
          text: (m.innerText || '').trim().length};
}
"""

JS_CLOSE_MODAL = r"""
() => {
  // chỉ bấm nút ĐÓNG thật sự (icon X / nhãn Đóng/Close) – không bấm nút Lưu/Huỷ/Đồng ý...
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const sels = '.close, .btn-close, .ant-modal-close, .el-dialog__headerbtn, .p-dialog-header-close, ' +
               'button[aria-label*="lose" i], button[aria-label*="đóng" i], [role=dialog] button, mat-dialog-container button';
  for (const b of document.querySelectorAll(sels)) {
    if (!vis(b)) continue;
    const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).trim().toLowerCase();
    const cls = (b.className || '').toString();
    if (/(^|\s)(close|btn-close|ant-modal-close|el-dialog__headerbtn|p-dialog-header-close)(\s|$)/.test(cls) ||
        ['đóng', 'close', '×', 'x', '✕', 'clear'].includes(t) || /^(close|đóng)$/.test(t)) { b.click(); return true; }
  }
  return false;
}
"""


class RequestRecorder:
    """Ghi nhận các request phát sinh trên trang, xử lý sau khi trang ổn định."""

    def __init__(self, page: Page):
        self.items: list[tuple[Request, bool]] = []
        self.blocked: set[tuple[str, str]] = set()     # (method, url) bị bộ chặn ghi huỷ – gán từ WriteGuard.blocked
        self.static_ext = tuple(config.get("crawler.static_extensions", []))
        self.types = set(config.get("crawler.capture_resource_types", ["xhr", "fetch", "document"]))
        self.secrets: list[str] = []      # mật khẩu đã biết -> che trong body trước khi lưu (vd đăng nhập lại khi ghi 3c)
        page.on("requestfinished", lambda r: self.items.append((r, True)))
        page.on("requestfailed", lambda r: self.items.append((r, False)))

    def reset(self) -> None:
        self.items.clear()

    def take(self, before_ms: float) -> list[tuple[Request, bool]]:
        """Lấy ra (và xoá khỏi hàng đợi) các request bắt đầu trước thời điểm before_ms (ms từ epoch)."""
        out, keep = [], []
        for it in list(self.items):
            try:
                start = (it[0].timing or {}).get("startTime") or 0
            except Exception:  # noqa: BLE001
                start = 0
            (out if start < before_ms else keep).append(it)
        self.items[:] = keep
        return out

    def collect(self, page_url: str = "", items: Optional[list[tuple[Request, bool]]] = None) -> list[CapturedRequest]:
        out: list[CapturedRequest] = []
        for req, ok in list(self.items if items is None else items):
            try:
                if req.resource_type not in self.types:
                    continue
                path = urlparse(req.url).path.lower()
                if path.endswith(self.static_ext):
                    continue
                status, ctype, size, resp = None, "", None, None
                if ok:
                    resp = req.response()
                    if resp:
                        status = resp.status
                        ctype = resp.headers.get("content-type", "")
                        cl = resp.headers.get("content-length")
                        size = int(cl) if cl and cl.isdigit() else None
                try:
                    all_h = req.all_headers()
                except Exception:  # noqa: BLE001
                    all_h = req.headers
                custom_h = {k: v for k, v in all_h.items() if _is_custom_header(k)}
                auth = all_h.get("authorization", "")
                auth_sig = _sig(auth.split(" ", 1)[-1]) if auth else ""
                token_path = token_sig = ""
                if (ok and resp and req.resource_type in ("xhr", "fetch") and "json" in ctype
                        and "token" in urlparse(req.url).path.lower()):
                    try:
                        body = resp.json()
                        token_path = _find_token_path(body)
                        if token_path:
                            val = _get_path(body, token_path)
                            token_sig = _sig(val) if isinstance(val, str) else ""
                    except Exception:  # noqa: BLE001
                        pass
                t = req.timing or {}
                dur = t.get("responseEnd", -1)
                try:
                    post = req.post_data
                except Exception:  # noqa: BLE001 - dữ liệu nhị phân
                    post = None
                post, secret_hit = redact_body(post, self.secrets)
                out.append(CapturedRequest(
                    method=req.method, url=req.url, resource_type=req.resource_type, status=status,
                    duration_ms=round(dur, 1) if dur and dur > 0 else None, content_type=ctype,
                    post_data=(post[:4000] if post else None), response_size=size, page_url=page_url,
                    auth_sig=auth_sig, token_path=token_path, token_sig=token_sig,
                    headers=custom_h if req.resource_type in ("xhr", "fetch") else {},
                    blocked=(not ok) and (req.method, req.url) in self.blocked, sensitive=secret_hit))
            except Exception:  # noqa: BLE001
                continue
        return out


class WebCrawler:
    JS_PAGE_STATS = JS_PAGE_STATS

    def __init__(self, login: LoginConfig, password: str, work_dir: Path, log: Log = print):
        self.cfg = login
        self.password = password
        self.work_dir = work_dir
        self.log = log
        self.timeout = int(config.get("crawler.timeout_ms", 30000))
        self.wait_after = int(config.get("crawler.wait_after_load_ms", 1500))
        self.exclude = [_compact(k) for k in config.get("crawler.exclude_keywords", []) if _compact(k)]
        self.blacklist = [w.lower() for w in config.get("crawler.popup_blacklist", [])]
        self._hosts: set[str] = {host_key(login.base_url)}   # + máy chủ sau khi đăng nhập (vd tự chuyển sang www.)
        self.crud_words = [w.lower() for w in config.get("scoring.keywords.crud", [])]
        self.auth = AuthCapture()
        self._pw = None
        self.browser = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.recorder: Optional[RequestRecorder] = None
        self.state_file = work_dir / "storage_state.json"
        self.scan_popups = False            # bật từ CLI khi project.crawl_popups = True (quét tương tác)
        self.guard: Optional[WriteGuard] = None
        self._login_bodies: dict[str, str] = {}
        self._login_reqs: list[Request] = []
        self.state_meta = work_dir / "storage_state.meta.json"   # tài khoản + máy chủ của phiên đã lưu

    # ------------------------------------------------------------ lifecycle
    def __enter__(self):
        self._pw = sync_playwright().start()
        self.headless = self.cfg.headless and not self.cfg.manual_login
        self.browser = self._pw.chromium.launch(headless=self.headless, args=["--start-maximized"])
        return self

    def __exit__(self, *exc):
        try:
            for obj in (self.context, self.browser):
                try:
                    if obj:
                        obj.close()
                except Exception:  # noqa: BLE001 - người dùng đã tự đóng trình duyệt
                    pass
        finally:
            if self._pw:
                self._pw.stop()

    def _new_context(self, use_state: bool) -> None:
        kwargs: dict[str, Any] = {"ignore_https_errors": config.insecure_tls(self.cfg)}
        vp = str(config.get("crawler.window_size", "maximized")).lower().replace(" ", "")
        if vp == "maximized" and not self.headless:
            kwargs["no_viewport"] = True     # trang co giãn theo cửa sổ trình duyệt (đã mở toàn màn hình)
        else:
            w, _, h = vp.partition("x")
            kwargs["viewport"] = ({"width": int(w), "height": int(h)} if w.isdigit() and h.isdigit()
                                  else {"width": 1920, "height": 1080})   # chạy ẩn: giả lập màn hình Full HD
        if use_state and self.state_file.exists():
            kwargs["storage_state"] = str(self.state_file)
        self.context = self.browser.new_context(**kwargs)
        self.context.set_default_timeout(self.timeout)
        self.page = self.context.new_page()
        self.recorder = RequestRecorder(self.page)
        self.recorder.secrets = [x for x in (self.password,) if x]
        self.page.on("request", self._sniff_auth_header)

    def enable_write_guard(self, level: str = "keyword") -> WriteGuard:
        """Bật bộ chặn ghi dữ liệu cho mọi trang của phiên (gọi SAU khi đăng nhập)."""
        self.guard = WriteGuard(level, self.log)
        self.guard.attach(self.context)
        self.recorder.blocked = self.guard.blocked
        return self.guard

    def _sniff_auth_header(self, req: Request) -> None:
        try:
            if req.resource_type in ("xhr", "fetch"):
                h = req.headers
                if "authorization" in h and "Authorization" not in self.auth.static_headers:
                    self.auth.static_headers["Authorization"] = h["authorization"]
        except Exception:  # noqa: BLE001
            pass

    def _login_route(self, route) -> None:
        """Trong lúc đăng nhập: lấy phản hồi POST (XHR/fetch) để đọc body trước khi trang chuyển hướng."""
        req = route.request
        if req.method not in ("POST", "PUT") or req.resource_type not in ("xhr", "fetch"):
            route.continue_()
            return
        self._login_reqs.append(req)      # giữ lại cả khi trang chuyển hướng ngay (không chờ đọc phản hồi)
        try:
            resp = route.fetch()
            if "json" in resp.headers.get("content-type", ""):
                self._login_bodies[req.url] = resp.text()
            route.fulfill(response=resp)
        except Exception:  # noqa: BLE001
            try:
                route.continue_()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ helpers
    def _settle(self, extra_ms: Optional[int] = None) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=min(self.timeout, 15000))
        except PWTimeout:
            pass
        self.page.wait_for_timeout(self.wait_after if extra_ms is None else extra_ms)

    def _password_visible(self) -> bool:
        try:
            loc = self.page.locator("input[type=password]")
            return loc.count() > 0 and loc.first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    def _same_origin(self, url: str) -> bool:
        """Cùng hệ thống: so tên máy chủ không phân biệt hoa thường, bỏ cổng và tiền tố www."""
        return urlparse(url).scheme in ("http", "https") and host_key(url) in self._hosts

    def _excluded(self, url: str, text: str = "", content: bool = False) -> bool:
        """Link không được mở: đăng xuất / thao tác nguy hiểm (exclude_keywords, so khớp không dấu – LogOff, Đăng xuất,
        Thoát). content=True (link trong nội dung trang, vd trên từng dòng bảng): loại thêm link có từ ghi dữ liệu
        (/cv/approve?id=1 – nhiều hệ thống cũ ghi dữ liệu bằng GET) và link có nhãn thuộc popup_blacklist (Duyệt…)."""
        u, t = _compact(url), _compact(text)
        if any(k in u or k in t for k in self.exclude):
            return True
        if content:
            return has_write_keyword(url) or get_has_write(url) or bool(text and is_banned(text, self.blacklist))
        # link menu: trang danh sách "/HoSo/XuLy", "/viec-can-xu-ly" vẫn mở; link mà mở ra là đã ghi dữ liệu
        # (/inbox/read-all, /ho-so/xu-ly?id=1, /VanBan/XoaVanBan?id=5) thì không
        return page_has_write(url)

    # ------------------------------------------------------------ login
    def login(self, reuse_state: bool = True) -> bool:
        if reuse_state and self.state_file.exists() and not self._state_matches():
            self.log("Phiên đăng nhập đã lưu thuộc tài khoản/máy chủ khác -> đăng nhập lại.")
            reuse_state = False
        if reuse_state and self.state_file.exists():
            self._new_context(use_state=True)
            self.log("Thử dùng lại phiên đăng nhập đã lưu...")
            self.page.goto(self.cfg.base_url, wait_until="domcontentloaded")
            self._settle()
            if not self._password_visible():
                self.log("Phiên đăng nhập còn hiệu lực.")
                self._load_auth_cache()
                self._hosts.add(host_key(self.page.url))
                return True
            self.context.close()
        self._new_context(use_state=False)
        url = self.cfg.login_url or self.cfg.base_url
        self.log(f"Mở trang đăng nhập: {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            if "CERT" in str(e).upper() or "SSL" in str(e).upper():
                self.log("Chứng chỉ HTTPS của máy chủ không hợp lệ (tự ký / hết hạn / sai tên miền). Nếu đây là máy chủ "
                         "kiểm thử nội bộ, tick 'Bỏ qua kiểm tra chứng chỉ HTTPS' ở bước 1 › Tuỳ chọn nâng cao.")
            raise
        self._settle()
        self.recorder.reset()
        self.page.route("**/*", self._login_route)

        if self.cfg.manual_login:
            self.log("CHẾ ĐỘ ĐĂNG NHẬP THỦ CÔNG: hãy đăng nhập trên cửa sổ trình duyệt (tối đa 5 phút)...")
            self._try_fill_credentials(submit=False)
            deadline = time.time() + 300
            while time.time() < deadline:
                self.page.wait_for_timeout(1500)
                if not self._password_visible() and self._is_logged_in():
                    break
            else:
                self.log("Hết thời gian chờ đăng nhập thủ công.")
                return False
        else:
            if not self._try_fill_credentials(submit=True):
                self.log("Không tìm thấy ô nhập tài khoản/mật khẩu. Hãy khai báo selector hoặc dùng đăng nhập thủ công.")
                return False
            self._settle(2500)

        ok = self._is_logged_in()
        try:
            self.page.unroute("**/*", self._login_route)
        except Exception:  # noqa: BLE001
            pass
        self._capture_login_request()
        if ok:
            self.context.storage_state(path=str(self.state_file))
            from ..storage import private_file
            private_file(self.state_file)
            self.state_meta.write_text(json.dumps(self._state_id(), ensure_ascii=False), encoding="utf-8")
            self._hosts.add(host_key(self.page.url))
            self.auth.cookies = self.context.cookies()
            self._save_auth_cache()
            self.log(f"Đăng nhập thành công. URL hiện tại: {self.page.url}")
        else:
            self.log("Đăng nhập có vẻ KHÔNG thành công (vẫn thấy ô mật khẩu hoặc không khớp URL thành công).")
        return ok

    def _state_id(self) -> dict:
        return {"username": self.cfg.username or "", "host": host_key(self.cfg.base_url)}

    def _state_matches(self) -> bool:
        try:
            return json.loads(self.state_meta.read_text(encoding="utf-8")) == self._state_id()
        except Exception:  # noqa: BLE001 - phiên cũ chưa có thông tin tài khoản -> không dùng lại
            return False

    def relogin(self) -> bool:
        """Đăng nhập lại khi mất phiên giữa chừng (giữ nguyên mức bộ chặn ghi)."""
        level = self.guard.level if self.guard else None
        try:
            if self.context:
                self.context.close()
        except Exception:  # noqa: BLE001
            pass
        ok = self.login(reuse_state=False)
        if ok and level:
            self.enable_write_guard(level)
        return ok

    def _is_logged_in(self) -> bool:
        if self.cfg.success_url_contains:
            return self.cfg.success_url_contains in self.page.url
        return not self._password_visible()

    def _try_fill_credentials(self, submit: bool) -> bool:
        p = self.page
        try:
            sel = self.cfg.password_selector or "input[type=password]"
            try:   # form đăng nhập có thể hiện chậm (SPA tải xong mới vẽ form)
                p.wait_for_selector(sel, state="visible", timeout=min(self.timeout, 15000))
            except PWTimeout:
                pass
            pwd = p.locator(sel)
            if pwd.count() == 0:
                return False
            pwd = pwd.first
            if self.cfg.username_selector:
                user = p.locator(self.cfg.username_selector).first
            else:
                user = p.locator(
                    "input:not([type=hidden]):not([type=password]):not([type=checkbox]):not([type=radio])"
                    ":not([type=submit]):not([type=button])").filter(has_not=p.locator("[readonly]")).first
            if self.cfg.username:
                user.fill(self.cfg.username)
            if self.password:
                pwd.fill(self.password)
            if not submit:
                return True
            if self.cfg.submit_selector:
                p.locator(self.cfg.submit_selector).first.click()
            else:
                btn = p.locator("button[type=submit], input[type=submit]")
                if btn.count() == 0:
                    btn = p.get_by_role("button", name=re.compile(r"đăng nhập|login|sign in|đăng nhập", re.I))
                if btn.count() > 0:
                    btn.first.click()
                else:
                    pwd.press("Enter")
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"Lỗi khi điền thông tin đăng nhập: {e}")
            return False

    def _capture_login_request(self) -> None:
        """Tìm request đăng nhập (POST chứa mật khẩu) để sinh bước login trong script.

        Chỉ lưu mẫu body khi đã gỡ được mật khẩu thật (xem login_capture) – kể cả khi người dùng tự đăng nhập mà
        không nhập mật khẩu ở bước 1, mật khẩu/OTP gõ trên trình duyệt không bao giờ bị lưu xuống đĩa."""
        user, pwd = self.cfg.username, self.password
        cands = self._login_reqs + [r for r, _ in self.recorder.items if r not in self._login_reqs]
        for req in cands:
            try:
                if req.method not in ("POST", "PUT"):
                    continue
                body = req.post_data or ""
                ctype = req.headers.get("content-type", "")
            except Exception:  # noqa: BLE001
                continue
            if not body:
                continue
            if pwd and not body_has_secret(body, pwd):
                continue
            if not pwd and not has_secret_field(body, ctype):
                continue
            tpl, notes = login_template(body, ctype, user, pwd)
            if tpl is None:
                self.log(f"Thấy request đăng nhập {req.method} {req.url} nhưng không tạo được mẫu body an toàn "
                         f"({'; '.join(notes)}) – script sẽ dùng cookie/header tĩnh.")
                return
            for n in notes:
                self.log(f"Lưu ý: {n}.")
            self.auth.login_method = req.method
            self.auth.login_url = req.url
            self.auth.login_content_type = ctype
            self.auth.login_body_template = tpl
            try:
                text = self._login_bodies.get(req.url)
                if text is None:
                    resp = req.response()
                    text = resp.text() if resp else ""
                path = _find_token_path(json.loads(text)) if text else ""
                if path:
                    self.auth.token_json_path = path
            except Exception:  # noqa: BLE001
                pass
            self.log(f"Đã nhận diện request đăng nhập: {req.method} {req.url}"
                     + (f" (token: {self.auth.token_json_path})" if self.auth.token_json_path else ""))
            return
        self.log("Không bắt được request đăng nhập dạng API; script sẽ dùng cookie/header tĩnh.")

    def _save_auth_cache(self) -> None:
        from ..storage import private_file
        f = self.work_dir / "auth.json"
        f.write_text(self.auth.model_dump_json(indent=2), encoding="utf-8")
        private_file(f)

    def save_session(self) -> None:
        """Ghi lại auth.json khi kết thúc (header Authorization có thể bắt được giữa lúc quét); bỏ qua nếu chưa đăng nhập."""
        if self.auth.login_url or self.auth.cookies or self.auth.static_headers:
            try:
                self._save_auth_cache()
            except OSError:
                pass

    def _load_auth_cache(self) -> None:
        f = self.work_dir / "auth.json"
        if f.exists():
            try:
                self.auth = AuthCapture.model_validate_json(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ discover
    def discover_modules(self) -> list[dict]:
        p = self.page
        home = p.url
        self.log("Mở rộng menu và thu thập link điều hướng...")
        for _ in range(2):
            try:
                p.evaluate(JS_EXPAND_MENUS)
            except Exception:  # noqa: BLE001
                pass
            p.wait_for_timeout(600)
        links = p.evaluate(JS_MENU_LINKS)
        # menu nằm trong iframe / frameset (hệ thống cũ): đọc thêm link của từng khung con
        seen = {ln["href"] for ln in links}
        for fr in p.frames[1:]:
            try:
                links += [ln for ln in fr.evaluate(JS_MENU_LINKS) if ln["href"] not in seen]
                seen.update(ln["href"] for ln in links)
            except Exception:  # noqa: BLE001 - khung khác nguồn / đã đóng
                pass
        groups: dict[str, dict] = {}
        for ln in links:
            href, text = ln["href"], ln["text"]
            if not self._same_origin(href) or self._excluded(href, text) or href.rstrip("/") == home.rstrip("/"):
                continue
            g = ln.get("group") or text
            mod = groups.setdefault(g, {"name": g, "url": href, "links": []})
            mod["links"].append({"text": text, "href": href})
        self.log(f"Tìm thấy {len(links)} link, gom thành {len(groups)} phân hệ.")
        return list(groups.values())

    # ------------------------------------------------------------ analyze
    def analyze_page(self, url: str, module: str, menu_text: str = "", depth: int = 0,
                     shot_dir: Optional[Path] = None, idx: int = 0) -> PageInfo:
        info = PageInfo(url=url, module=module, menu_text=menu_text, depth=depth)
        self.recorder.reset()
        t0 = time.perf_counter()
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded")
            t_dom = (time.perf_counter() - t0) * 1000
            self._settle()
            # bỏ thời gian chờ thêm (wait_after) và khoảng 500 ms "yên lặng mạng" của networkidle
            info.load_time_ms = round(max(t_dom, (time.perf_counter() - t0) * 1000 - self.wait_after - 500), 1)
            if resp is not None and resp.status >= 400:
                info.error = f"HTTP {resp.status}"
            st = self.page.evaluate(JS_PAGE_STATS, self.crud_words)
            info.title = (st.get("h1") or st.get("title") or "").strip()[:200]
            for k in ("forms", "inputs", "selects", "buttons", "tables", "table_rows", "links"):
                setattr(info, k, int(st.get(k) or 0))
            info.crud_buttons = st.get("crud") or []
            if self._password_visible():
                info.error = SESSION_LOST
            if shot_dir:
                fn = shot_dir / f"{_safe(module)[:40]}_{idx:02d}.png"
                self.page.screenshot(path=str(fn), full_page=False)
                info.screenshot = str(fn)
        except Exception as e:  # noqa: BLE001
            info.error = str(e)[:300]
        info.requests = self.recorder.collect(page_url=url)
        if self.scan_popups and not info.error:
            from .interactions import InteractionScanner
            try:
                InteractionScanner(self).scan(url, info)
            except Exception as e:  # noqa: BLE001 - quét tương tác lỗi không làm hỏng kết quả trang
                self.log(f"      (bỏ qua quét tương tác: {str(e)[:120]})")
        return info

    # ------------------------------------------------------------ popup / modal
    def modal_stats(self) -> Optional[dict]:
        try:
            return self.page.evaluate(JS_MODAL_STATS)
        except Exception:  # noqa: BLE001
            return None

    def _modal_open(self) -> bool:
        return self.modal_stats() is not None

    def _close_modal(self, url: str) -> None:
        """Đóng popup an toàn: Esc -> nút Đóng/X -> tải lại trang."""
        for _ in range(2):
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
            if not self._modal_open():
                return
        try:
            if self.page.evaluate(JS_CLOSE_MODAL):
                self.page.wait_for_timeout(600)
                if not self._modal_open():
                    return
        except Exception:  # noqa: BLE001
            pass
        self.page.goto(url, wait_until="domcontentloaded")
        self._settle()

    def analyze_module(self, name: str, links: list[dict], shot_dir: Path,
                       max_pages: Optional[int] = None) -> list[PageInfo]:
        max_pages = max_pages or int(config.get("crawler.max_pages_per_module", 8))
        max_depth = int(config.get("crawler.max_depth", 1))
        queue = [(ln["href"], ln.get("text", ""), 0) for ln in links]
        visited: set[str] = set()
        pages: list[PageInfo] = []
        relogins = 0
        while queue and len(pages) < max_pages:
            url, text, depth = queue.pop(0)
            # khử trùng: /list, /list/, /list#top là 1 màn hình; link trong nội dung detail?id=1, id=2… chỉ mở 1 đại diện
            key = page_key(url, collapse_values=depth > 0)
            if key in visited or self._excluded(url, text, content=depth > 0):
                continue
            visited.add(key)
            self.log(f"  [{name}] ({len(pages) + 1}/{max_pages}) {text or ''} -> {url}")
            pi = self.analyze_page(url, name, text, depth, shot_dir, len(pages))
            if pi.error == SESSION_LOST and relogins < 2:
                relogins += 1
                self.log("      Mất phiên đăng nhập -> đăng nhập lại và quét lại trang này.")
                if self.relogin():
                    pi = self.analyze_page(url, name, text, depth, shot_dir, len(pages))
            pages.append(pi)
            self.log(f"      {len(pi.api_requests)} API, {pi.inputs} input, {pi.tables} bảng, "
                     f"CRUD={pi.crud_buttons[:5]} {('LỖI: ' + pi.error) if pi.error else ''}")
            if depth < max_depth and not pi.error:
                try:
                    for ln in self.page.evaluate(JS_CONTENT_LINKS):
                        if (self._same_origin(ln["href"]) and page_key(ln["href"], True) not in visited
                                and not self._excluded(ln["href"], ln["text"], content=True)):
                            queue.append((ln["href"], ln["text"], depth + 1))
                except Exception:  # noqa: BLE001
                    pass
        return pages


# ------------------------------------------------------------------ utils
# Header do trình duyệt/HTTP tự sinh -> không cần sao chép vào script
_STD_HEADERS = {
    "accept", "accept-encoding", "accept-language", "authorization", "cookie", "content-length", "content-type",
    "host", "connection", "user-agent", "referer", "priority", "pragma", "cache-control", "upgrade-insecure-requests",
    "dnt", "te", "if-none-match", "if-modified-since", "range", "purpose", "x-requested-with",
}


def _is_custom_header(name: str) -> bool:
    n = name.lower()
    return not (n.startswith(":") or n.startswith("sec-") or n in _STD_HEADERS)


def _sig(value: str) -> str:
    """Chữ ký ngắn của token để so khớp (không lưu giá trị thật)."""
    import hashlib
    return hashlib.sha1((value or "").encode("utf-8")).hexdigest()[:10] if value else ""


def _get_path(obj: Any, path: str) -> Any:
    for k in path.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def _safe(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE)


def _url_quote(s: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(s or "")


def _find_token_path(obj: Any, prefix: str = "") -> str:
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, str) and TOKEN_KEYS.match(k) and len(v) > 15:
                return p
            found = _find_token_path(v, p)
            if found:
                return found
    return ""


def absolute(base: str, href: str) -> str:
    return urljoin(base, href)


def dump(path: Path, data: Any) -> None:
    """Ghi JSON nguyên tử (file tạm rồi đổi tên) – tiến trình bị dừng giữa chừng không để lại file dở."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
