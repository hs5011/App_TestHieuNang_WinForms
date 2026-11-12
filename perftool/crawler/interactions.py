"""Quét tương tác trên 1 trang (bước 3, khi bật "Tự quét tương tác"):

  - tab trong trang, nút Tìm kiếm, nút chuyển sang trang 2 của bảng;
  - nút mở popup Xem / Thêm mới / Sửa / Chi tiết (theo `crawler.popup_triggers`);
  - menu ⋮ / "..." / nút thả xuống trên DÒNG ĐẦU TIÊN của bảng và trên thanh công cụ: mở menu, bấm lần lượt từng mục
    không thuộc `crawler.popup_blacklist` -> mục mở popup ghi thành popup, mục chuyển trang ghi thành trang con rồi quay lại.

An toàn: không bấm nút nào BÊN TRONG popup (Lưu/Gửi…), đóng popup bằng Esc / nút Đóng / tải lại trang; request ghi dữ liệu
phát sinh ngoài ý muốn bị bộ chặn ghi (guard.WriteGuard) huỷ trước khi tới máy chủ.
"""
from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Callable
from urllib.parse import parse_qsl, urljoin, urlparse

from playwright.sync_api import TimeoutError as PWTimeout

from .. import config
from ..models import CapturedRequest, PageInfo, PopupInfo

if TYPE_CHECKING:
    from .web_crawler import WebCrawler

# Các hàm JS dùng chung (vis, label, vùng điều hướng, vùng popup/menu) – ghép vào đầu mỗi đoạn JS bên dưới
_JS_COMMON = r"""
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const navs = Array.from(document.querySelectorAll('nav:not([aria-label*=pag i]), aside, header, [role=navigation], ' +
                          'mat-drawer, mat-sidenav, [class*=sidebar], [class*=side-menu], [class*=top-menu]'));
  const inNav = el => navs.some(n => n.contains(el));
  const inOverlay = el => el.closest('[role=dialog], [aria-modal=true], .modal, .ant-modal, mat-dialog-container, ' +
      '.el-dialog, .p-dialog, .cdk-overlay-pane, [role=menu], .dropdown-menu, .ant-dropdown, .mat-mdc-menu-panel, ' +
      '.mat-menu-panel, #perftool-rec-bar');
  const iconText = b => { const i = b.querySelector('mat-icon, .material-icons, .material-symbols-outlined, ' +
                                                   '.material-icons-outlined, i');
                          return i ? (i.innerText || '').trim().toLowerCase() : ''; };
  const plain = el => { const c = el.cloneNode(true);
                        c.querySelectorAll('mat-icon, .material-icons, .material-symbols-outlined, ' +
                                           '.material-icons-outlined, i, svg').forEach(x => x.remove());
                        return (c.innerText || c.textContent || '').trim().split('\n')[0].trim(); };
  // chuẩn hoá nhãn: NFC (Unicode tổ hợp), khoảng trắng đặc biệt (&nbsp;…) và '_' trong tên icon (delete_forever) -> dấu cách
  const clean = t => (t || '').normalize('NFC').replace(/[_\s  -​  　]+/g, ' ')
                              .trim().toLowerCase();
  // nhãn ĐẦY ĐỦ (không cắt) để so danh sách cấm – chỉ cắt khi hiển thị
  const label = b => ' ' + [b.innerText, b.value, b.getAttribute('aria-label'), b.title, b.getAttribute('mattooltip'),
                            b.getAttribute('ng-reflect-message'), b.getAttribute('data-original-title'), iconText(b)]
                     .map(clean).filter(Boolean).join(' ') + ' ';
  // so khớp NGUYÊN TỪ (vd "in" = In ấn không khớp "Xin gia hạn")
  const esc = w => w.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const banned = (t, bl) => { const x = clean(t); return bl.some(w => clean(w) &&
      new RegExp('(^|[^\\p{L}\\p{N}])' + esc(clean(w)) + '($|[^\\p{L}\\p{N}])', 'u').test(x)); };
  const disabled = b => b.disabled || b.getAttribute('aria-disabled') === 'true' ||
                        /(^|\s)(disabled|mat-mdc-button-disabled|ant-pagination-disabled)(\s|$)/.test((b.className || '').toString()) ||
                        !!b.closest('.disabled, [aria-disabled=true]');
"""

# Đánh dấu các phần tử có thể bấm theo loại: tab | search | paging -> trả về danh sách nhãn
JS_MARK = r"""
([kind, bl, maxn, words]) => {
""" + _JS_COMMON + r"""
  const attr = 'data-perftool-' + kind;
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  let cands = [];
  if (kind === 'tab') {
    cands = Array.from(document.querySelectorAll('[role=tab], .mat-mdc-tab, .mat-tab-label, .nav-tabs .nav-link, ' +
                       '.ant-tabs-tab, .el-tabs__item, .p-tabview-nav li a, .k-tabstrip-item'))
      .filter(t => t.getAttribute('aria-selected') !== 'true' &&
                   !/(^|\s)(active|mdc-tab--active|mat-tab-label-active|ant-tabs-tab-active|is-active|p-highlight|k-active)(\s|$)/
                     .test((t.className || '').toString()));
  } else if (kind === 'search') {
    cands = Array.from(document.querySelectorAll('button, [role=button], input[type=submit], input[type=button], a.btn'))
      .filter(b => { const t = label(b); return words.some(w => t.includes(' ' + w) || t.includes(w + ' ')); });
  } else if (kind === 'paging') {
    const boxes = Array.from(document.querySelectorAll('.pagination, .ant-pagination, mat-paginator, .mat-mdc-paginator, ' +
                  '.mat-paginator, .p-paginator, .el-pagination, .k-pager, [class*=paginat], [class*=pager], nav[aria-label*=pag i]'));
    for (const box of boxes) {
      const els = Array.from(box.querySelectorAll('button, a, li, [role=button]')).filter(vis);
      const nxt = els.find(e => /next|trang sau|tiếp|sau/.test(((e.getAttribute('aria-label') || '') + ' ' + (e.title || '') +
                                  ' ' + (e.className || '').toString()).toLowerCase()) && !disabled(e));
      const two = els.find(e => (e.innerText || '').trim() === '2' && !disabled(e));
      const pick = two || nxt;
      if (pick) { cands = [pick.matches('li') ? (pick.querySelector('a, button') || pick) : pick]; break; }
    }
  }
  const out = []; const seen = new Set();
  for (const b of cands) {
    if (!vis(b) || disabled(b) || (kind !== 'paging' && (inNav(b) || inOverlay(b)))) continue;
    const t = (kind === 'paging') ? 'trang 2' : (plain(b) || label(b).trim());
    // tab / chuyển trang chỉ đổi dữ liệu hiển thị -> không áp danh sách cấm (vd tab "Đã hoàn thành")
    if (!t || (kind === 'search' && (banned(t, bl) || banned(label(b), bl))) || seen.has(t)) continue;
    seen.add(t); b.setAttribute(attr, String(out.length)); out.push(t.slice(0, 60));
    if (out.length >= maxn) break;
  }
  return out;
}
"""

# Nút mở menu (⋮, …, thả xuống): dòng đầu của bảng + thanh công cụ. Trả về [{key, label}]
JS_MENU_TRIGGERS = r"""
([bl, maxn]) => {
""" + _JS_COMMON + r"""
  document.querySelectorAll('[data-perftool-menu]').forEach(e => e.removeAttribute('data-perftool-menu'));
  const ICON = /^(more_vert|more_horiz|menu_open|arrow_drop_down|expand_more|keyboard_arrow_down|⋮|⋯|…|\.\.\.|•••|···)$/;
  const isTrigger = b => {
    const ah = (b.getAttribute('aria-haspopup') || '').toLowerCase();
    if (ah === 'menu' || ah === 'true') return true;
    if (b.matches('[matmenutriggerfor], [ng-reflect-menu], .mat-mdc-menu-trigger, .mat-menu-trigger, .dropdown-toggle, ' +
                  '.ant-dropdown-trigger, .el-dropdown-selfdefine, [data-toggle=dropdown], [data-bs-toggle=dropdown], ' +
                  '.p-splitbutton-menubutton, .k-menu-button')) return true;
    const txt = (b.innerText || '').trim().toLowerCase();
    if (ICON.test(iconText(b)) || ICON.test(txt)) return true;
    const cls = (b.className || '').toString().toLowerCase();
    if (/(^|[\s_-])(more|kebab|ellipsis|dots|row-menu|action-menu|actions-menu)([\s_-]|$)/.test(cls) && txt.length <= 2) return true;
    const i = b.querySelector('i, svg, span');
    const icls = i ? ((i.className && i.className.baseVal !== undefined) ? i.className.baseVal : (i.className || '')).toString().toLowerCase() : '';
    return /(ellipsis|more|dots|kebab)/.test(icls) && txt.length <= 2;
  };
  const rowOf = b => b.closest('tbody tr, [role=row], mat-row, .mat-mdc-row, .ant-table-row, .el-table__row, .p-datatable-tbody > tr');
  const cands = Array.from(document.querySelectorAll('button, [role=button], a, .mat-mdc-icon-button, mat-icon[role=img]'))
    .filter(vis).filter(b => !inNav(b) && !inOverlay(b) && !b.closest('[role=combobox], mat-select, select, .mat-mdc-select'))
    .filter(isTrigger);
  const out = []; const seen = new Set();
  for (const b of cands) {
    if (disabled(b)) continue;
    const t = label(b);
    if (banned(t, bl)) continue;
    const row = rowOf(b);
    let key;
    if (row) {
      if (row.closest('thead') || row.querySelector('th')) continue;          // dòng tiêu đề
      const cell = b.closest('td, [role=cell], [role=gridcell], mat-cell, .mat-mdc-cell');
      key = 'row:' + (cell ? Array.from(cell.parentElement.children).indexOf(cell) : 0);
    } else key = 'bar:' + t.trim();
    if (seen.has(key)) continue;                                              // chỉ lấy menu của dòng đầu tiên
    seen.add(key);
    const shown = plain(b) || (ICON.test(iconText(b)) || !iconText(b) ? '⋮' : iconText(b));
    b.setAttribute('data-perftool-menu', String(out.length));
    out.push({key, label: (row ? 'dòng 1 · ' : '') + shown.slice(0, 40)});
    if (out.length >= maxn) break;
  }
  return out;
}
"""

# Các mục trong menu vừa mở -> [{label, disabled}], đánh dấu data-perftool-item
JS_MENU_ITEMS = r"""
() => {
""" + _JS_COMMON + r"""
  document.querySelectorAll('[data-perftool-item]').forEach(e => e.removeAttribute('data-perftool-item'));
  const boxes = Array.from(document.querySelectorAll('[role=menu], .mat-mdc-menu-panel, .mat-menu-panel, .dropdown-menu.show, ' +
      '.dropdown-menu[style*=block], .ant-dropdown:not(.ant-dropdown-hidden), .el-dropdown-menu, .p-menu, .p-tieredmenu, ' +
      '.k-menu-popup, .MuiMenu-paper, .v-menu__content')).filter(vis).filter(b => !b.closest('#perftool-rec-bar'));
  if (!boxes.length) return [];
  const box = boxes[boxes.length - 1];
  let items = Array.from(box.querySelectorAll('[role=menuitem], .mat-mdc-menu-item, .mat-menu-item, .dropdown-item, ' +
      '.ant-dropdown-menu-item, .el-dropdown-menu__item, .p-menuitem-link, .k-item'));
  if (!items.length) items = Array.from(box.querySelectorAll('li > a, li > button, button, a'));
  const out = []; const seen = new Set();
  for (const it of items.filter(vis)) {
    const t = (plain(it) || label(it)).replace(/[\s ]+/g, ' ').trim();
    if (!t || seen.has(t)) continue;
    seen.add(t); it.setAttribute('data-perftool-item', String(out.length));
    // full: nhãn đầy đủ (kể cả title/aria-label/icon) để Python so danh sách cấm
    out.push({label: t.slice(0, 60), full: label(it) + ' ' + clean(t), disabled: disabled(it)});
  }
  return out;
}
"""

# Nút mở popup Xem/Thêm/Sửa/Chi tiết (theo nhãn) -> danh sách nhãn, đánh dấu data-perftool-trigger
JS_POPUP_TRIGGERS = r"""
([wl, bl, maxn]) => {
""" + _JS_COMMON + r"""
  document.querySelectorAll('[data-perftool-trigger]').forEach(e => e.removeAttribute('data-perftool-trigger'));
  const cands = Array.from(document.querySelectorAll('button, [role=button], a.btn, .ant-btn, a[mattooltip]'))
    .filter(vis).filter(b => !inNav(b) && !inOverlay(b));
  const seen = new Set(); const out = [];
  for (const b of cands) {
    if (disabled(b)) continue;
    if ((b.type === 'submit') && b.closest('form')) continue;                       // không submit form
    const href = b.tagName === 'A' ? (b.getAttribute('href') || '') : '';
    if (href && !href.startsWith('#') && !href.startsWith('javascript')) continue;  // link điều hướng -> bỏ
    const t = label(b);
    if (!t.trim() || !wl.some(w => t.includes(w)) || banned(t, bl)) continue;
    // tên hiển thị: chữ trên nút, không có -> tooltip/aria-label, không có -> tên icon (không ghép lặp "delete_forever xem delete_forever")
    const shown = clean(plain(b)) || clean(b.getAttribute('aria-label') || b.title || b.getAttribute('mattooltip') || '') ||
                  clean(iconText(b));
    const key = shown.replace(/\d+/g, '').trim().slice(0, 80);
    if (seen.has(key)) continue;                                                    // mỗi loại nút chỉ bấm 1 lần
    seen.add(key);
    b.setAttribute('data-perftool-trigger', String(out.length));
    out.push(key);
    if (out.length >= maxn) break;
  }
  return out;
}
"""


def is_banned(text: str, words: list[str]) -> bool:
    """Nhãn có chứa từ/cụm từ cấm (so khớp nguyên từ, không phân biệt hoa thường)."""
    # chuẩn hoá NFC: trang dùng "Unicode tổ hợp" (Xo + dấu sắc rời) vẫn khớp từ cấm "xóa" dựng sẵn
    # + mọi khoảng trắng đặc biệt (&nbsp;…) và '_' (tên icon delete_forever) -> 1 dấu cách
    def norm(s: str) -> str:
        return re.sub(r"[\s_]+", " ", unicodedata.normalize("NFC", s or "")).strip().casefold()
    t = norm(text)
    return any(norm(w) and re.search(rf"(?<![^\W_]){re.escape(norm(w))}(?![^\W_])", t) for w in words)


def route_of(url: str) -> str:
    """Khoá 'màn hình' của URL: host + path (+ đường dẫn trong # với SPA dùng hash), bỏ query."""
    u = urlparse(url)
    frag = u.fragment.split("?")[0] if u.fragment.startswith(("/", "!/")) else ""
    return f"{u.netloc}{u.path.rstrip('/')}" + (f"#{frag}" if frag else "")


_PAGING_PARAMS = {"page", "pageindex", "pagenumber", "pageno", "pagesize", "p", "sort", "sortby", "order", "orderby",
                  "skip", "take", "limit", "offset", "size", "_", "t", "ts"}


def host_key(url: str) -> str:
    """Tên máy chủ để so "cùng hệ thống": chữ thường, bỏ cổng và tiền tố www. (LocalHost:443 == localhost)."""
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def page_key(url: str, collapse_values: bool = False) -> str:
    """Khoá màn hình để khử trùng: route (bỏ '/' cuối, fragment không phải route, cổng mặc định) + tham số query trừ tham số
    phân trang/sắp xếp. collapse_values=True: chỉ giữ TÊN tham số (detail?id=1, id=2… là cùng 1 màn hình)."""
    u = urlparse(url)
    params = [(k.lower(), v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in _PAGING_PARAMS]
    q = "&".join(sorted({k for k, _ in params}) if collapse_values else sorted(f"{k}={v}" for k, v in params))
    port = u.port if u.port and u.port not in (80, 443) else None
    frag = u.fragment.split("?")[0] if u.fragment.startswith(("/", "!/")) else ""
    return (f"{host_key(url)}{':' + str(port) if port else ''}{u.path.rstrip('/')}" + (f"#{frag}" if frag else "")
            + (f"?{q}" if q else ""))


def screen_key(page) -> str:
    """Khoá màn hình của 1 PageInfo: trang từ menu giữ nguyên query (report?id=1 ≠ report?id=2); trang mở từ link trong
    nội dung (depth > 0, vd tin tức detail?id=…) chỉ xét tên tham số."""
    return page_key(page.url, collapse_values=(page.depth or 0) > 0)


def join_url(base: str, rel: str) -> str:
    """URL gợi ý / URL phân hệ tương đối: '/x' tính từ GỐC máy chủ (không nối sau đường dẫn của base_url), 'x' tính
    tương đối theo base_url."""
    rel = (rel or "").strip()
    if rel.startswith(("http://", "https://")):
        return rel
    u = urlparse(base)
    if rel.startswith("/"):
        return f"{u.scheme}://{u.netloc}{rel}"
    return urljoin(base if base.endswith("/") else base + "/", rel)


def _api_count(reqs: list[CapturedRequest]) -> int:
    return len([r for r in reqs if r.resource_type in ("xhr", "fetch")])


class InteractionScanner:
    def __init__(self, crawler: "WebCrawler"):
        self.c = crawler
        self.bl = [w.lower() for w in config.get("crawler.popup_blacklist", [])]
        self.wl = [w.lower() for w in config.get("crawler.popup_triggers", [])]

    # ------------------------------------------------------------ tiện ích
    @property
    def page(self):
        return self.c.page

    def _wait(self) -> None:
        self.page.wait_for_timeout(800)
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except PWTimeout:
            pass

    def _click(self, selector: str) -> None:
        loc = self.page.locator(selector).first
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=5000)
        except Exception:  # noqa: BLE001 - nút chỉ hiện khi rê chuột / bị phủ -> rê chuột rồi bấm cưỡng bức
            try:
                loc.hover(timeout=2000, force=True)
            except Exception:  # noqa: BLE001
                pass
            loc.click(timeout=5000, force=True)

    def _banned(self, text: str) -> bool:
        return is_banned(text, self.bl)

    def _reload(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        self.c._settle()

    def _observe(self, url: str, info: PageInfo, trigger: str, kind: str, click: Callable[[], None]) -> None:
        """Bấm (click()), chờ, rồi ghi nhận kết quả: trang con / popup / chỉ gọi API. Sau đó đưa trang về trạng thái cũ."""
        self.c.recorder.reset()
        before = self.page.url
        click()
        self._wait()
        now = self.page.url
        reqs = self.c.recorder.collect(page_url=url)
        n_api, n_blocked = _api_count(reqs), sum(1 for r in reqs if r.blocked)
        tail = f"{n_api} API" + (f", 🛡 chặn {n_blocked} request ghi" if n_blocked else "")
        if route_of(now) != route_of(before):
            st = self.page.evaluate(self.c.JS_PAGE_STATS, self.c.crud_words)
            n_in = int(st.get("inputs") or 0) + int(st.get("selects") or 0)
            counted = bool(n_in or st.get("tables"))
            pop = PopupInfo(trigger=trigger[:80], kind="page", url=now, title=(st.get("h1") or st.get("title") or "")[:120],
                            inputs=int(st.get("inputs") or 0), selects=int(st.get("selects") or 0),
                            buttons=int(st.get("buttons") or 0), tables=int(st.get("tables") or 0),
                            counted=counted, api_count=n_api)
            for r in reqs:
                r.page_url = now
            info.popups.append(pop)
            info.requests.extend(reqs)
            self.c.log(f"      ↪ [{trigger[:50]}] mở trang con {urlparse(now).path[:60]}: {n_in} trường nhập, "
                       f"{pop.tables} bảng, {tail}{'' if counted else ' (không tính màn hình)'}")
            self._reload(url)
            return
        m = self.c.modal_stats()
        if m:
            counted = bool(m["inputs"] + m["selects"] or m["tables"] or m["text"] > 150)
            pop = PopupInfo(trigger=trigger[:80], kind="popup", title=m["title"], inputs=m["inputs"], selects=m["selects"],
                            buttons=m["buttons"], tables=m["tables"], counted=counted, api_count=n_api)
            info.popups.append(pop)
            info.requests.extend(reqs)
            self.c.log(f"      ▣ [{trigger[:50]}] popup '{m['title'][:40]}': {m['inputs'] + m['selects']} trường nhập, "
                       f"{m['tables']} bảng, {tail}{'' if counted else ' (không tính màn hình)'}")
            self.c._close_modal(url)
            return
        if n_api or n_blocked or kind == "tab":
            info.popups.append(PopupInfo(trigger=trigger[:80], kind=kind, title=trigger[:120], counted=False,
                                         api_count=n_api))
            info.requests.extend(reqs)
            self.c.log(f"      • [{trigger[:50]}] {tail}")
        # đóng menu còn mở (nếu có)
        self.page.keyboard.press("Escape")

    # ------------------------------------------------------------ các loại tương tác
    def scan(self, url: str, info: PageInfo) -> None:
        # trong lúc bấm thử: bộ chặn ghi mức "strict" – chỉ cho qua request truy vấn rõ ràng (GetAll/Search/…)
        g = self.c.guard
        old = g.level if g else None
        if g:
            g.level = "strict"
        try:
            self._scan(url, info)
        finally:
            if g:
                g.level = old

    def _scan(self, url: str, info: PageInfo) -> None:
        for kind, maxn in (("tab", int(config.get("crawler.max_tabs_per_page", 4))), ("search", 1), ("paging", 1)):
            try:
                self._scan_marked(url, info, kind, maxn)
            except Exception as e:  # noqa: BLE001
                self.c.log(f"      (bỏ qua {kind}: {str(e)[:100]})")
        if any(pp.kind in ("tab", "search", "paging") for pp in info.popups):
            self._reload(url)          # đưa trang về trạng thái ban đầu trước khi mở popup/menu
        for fn in (self._scan_buttons, self._scan_menus):
            try:
                fn(url, info)
            except Exception as e:  # noqa: BLE001
                self.c.log(f"      (bỏ qua: {str(e)[:120]})")
                try:
                    self._reload(url)
                except Exception:  # noqa: BLE001
                    pass

    def _scan_marked(self, url: str, info: PageInfo, kind: str, maxn: int) -> None:
        words = [w.lower() for w in config.get("crawler.search_words", [])]
        labels = self.page.evaluate(JS_MARK, [kind, self.bl, maxn, words])
        names = {"tab": "Tab", "search": "Tìm kiếm", "paging": "Chuyển trang"}
        for lb in labels:
            marks = self.page.evaluate(JS_MARK, [kind, self.bl, maxn, words])
            if lb not in marks:
                continue
            sel = f'[data-perftool-{kind}="{marks.index(lb)}"]'
            trig = f"{names[kind]}: {lb}" if kind != "search" else lb
            self._observe(url, info, trig, kind, lambda s=sel: self._click(s))

    def _scan_buttons(self, url: str, info: PageInfo) -> None:
        """Nút mở popup Xem/Thêm mới/Sửa/Chi tiết theo nhãn."""
        maxn = int(config.get("crawler.max_popups_per_page", 5))
        keys = self.page.evaluate(JS_POPUP_TRIGGERS, [self.wl, self.bl, maxn])
        for key in keys:
            marks = self.page.evaluate(JS_POPUP_TRIGGERS, [self.wl, self.bl, maxn])
            if key not in marks:
                continue
            sel = f'[data-perftool-trigger="{marks.index(key)}"]'
            self._observe(url, info, key, "action", lambda s=sel: self._click(s))

    def _open_menu(self, key: str) -> list[dict]:
        maxn = int(config.get("crawler.max_menus_per_page", 3))
        trigs = self.page.evaluate(JS_MENU_TRIGGERS, [self.bl, maxn])
        idx = next((i for i, t in enumerate(trigs) if t["key"] == key), None)
        if idx is None:
            return []
        self._click(f'[data-perftool-menu="{idx}"]')
        self.page.wait_for_timeout(500)
        return self.page.evaluate(JS_MENU_ITEMS)

    def _scan_menus(self, url: str, info: PageInfo) -> None:
        """Menu ⋮ / thả xuống: mở menu, đọc các mục, bấm từng mục an toàn."""
        maxn = int(config.get("crawler.max_menus_per_page", 3))
        maxi = int(config.get("crawler.max_menu_items", 8))
        trigs = self.page.evaluate(JS_MENU_TRIGGERS, [self.bl, maxn])
        for trig in trigs:
            items = self._open_menu(trig["key"])
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(300)
            if not items:
                continue
            labels = [it["label"] for it in items]
            allowed = [it["label"] for it in items if not it["disabled"] and not self._banned(it["label"])
                       and not self._banned(it.get("full", ""))][:maxi]
            skipped = [lb for lb in labels if lb not in allowed]
            self.c.log(f"      ☰ menu [{trig['label']}]: {', '.join(labels)}"
                       + (f" · không bấm: {', '.join(skipped)}" if skipped else ""))
            # nhãn các mục menu phản ánh thao tác nghiệp vụ của màn hình (kể cả mục không bấm như Xoá)
            info.crud_buttons = list(dict.fromkeys(info.crud_buttons + [lb.lower() for lb in labels]))[:30]
            for lb in allowed:
                def click(k=trig["key"], want=lb) -> None:
                    its = self._open_menu(k)
                    names = [x["label"] for x in its]
                    if want not in names:
                        raise RuntimeError(f"không mở lại được mục '{want}'")
                    self._click(f'[data-perftool-item="{names.index(want)}"]')
                try:
                    self._observe(url, info, f"{trig['label']} › {lb}", "action", click)
                except Exception as e:  # noqa: BLE001
                    self.c.log(f"      (bỏ qua mục '{lb}': {str(e)[:100]})")
                    self._reload(url)
