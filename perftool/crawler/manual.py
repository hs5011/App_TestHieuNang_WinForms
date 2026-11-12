"""Ghi thao tác thủ công (bước 3): người dùng tự bấm qua các màn hình trên trình duyệt do PerfTool mở,
công cụ ghi lại từng "đoạn" màn hình (trang / popup) cùng các API gọi trong lúc đó.

Kết quả lưu (nối thêm) vào crawl/record.json:
  {"sessions": [{"started", "finished", "module", "guard", "start_url",
                 "segments": [{"kind": "page"|"popup", "url", "page_url", "title", "stats", "requests": [...]}]}]}
merge.apply_record() gộp dữ liệu này vào danh sách màn hình của dự án.

Kết thúc ghi khi: bấm "Kết thúc ghi" trên thanh nhỏ ở góc trình duyệt, bấm nút ở giao diện PerfTool (tạo file cờ
crawl/record.stop), đóng trình duyệt, hoặc hết `crawler.record_max_minutes`.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .. import config
from .interactions import route_of

if TYPE_CHECKING:
    from .web_crawler import WebCrawler

POLL_MS = 700
SHIFT_MS = 1200          # request bắt đầu trong khoảng này trước khi phát hiện đổi màn hình -> thuộc màn hình mới
CLICK_LAG_MS = 150       # độ trễ từ lúc bấm trên trình duyệt tới khi Python nhận được sự kiện
FLUSH_AFTER_MS = 2500    # request cũ hơn khoảng này được xử lý dần (tránh mất dữ liệu khi người dùng đóng trình duyệt)

# Thanh nhỏ ở góc dưới phải trình duyệt: báo đang ghi + nút Kết thúc ghi
TOOLBAR_JS = r"""
(() => {
  const add = () => {
    if (!document.body || document.getElementById('perftool-rec-bar')) return;
    const bar = document.createElement('div');
    bar.id = 'perftool-rec-bar';
    bar.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#b71c1c;color:#fff;' +
      'font:13px Segoe UI,Arial;padding:6px 10px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.3);opacity:.92';
    bar.innerHTML = '● PerfTool đang ghi thao tác &nbsp;<span id="perftool-rec-stop" style="background:#fff;color:#b71c1c;' +
      'padding:2px 8px;border-radius:4px;cursor:pointer;font-weight:600">Kết thúc ghi</span>';
    document.body.appendChild(bar);
    document.getElementById('perftool-rec-stop').addEventListener('click', e => {
      e.stopPropagation(); window.__perftoolStop = true;
      bar.innerHTML = '■ Đã kết thúc ghi – có thể đóng trình duyệt'; bar.style.background = '#1b5e20';
    }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
  setInterval(add, 1500);
})();
"""


# Ghi lại từng cú bấm của người dùng (gửi về Python qua binding __perftoolClick): nhãn nút, có nằm trong dòng bảng không,
# có phải nút mở menu ⋮ / … không, có phải mục trong menu vừa mở không. Dùng để đặt tên "Nút / mục đã bấm" ở 3d
# (vd "dòng 1 · ⋮ › Xin gia hạn") giống như khi tự quét.
CLICK_JS = r"""
(() => {
  if (window.__perftoolClickHooked) return;
  window.__perftoolClickHooked = true;
  const ICON = /^(more_vert|more_horiz|menu_open|arrow_drop_down|expand_more|keyboard_arrow_down|⋮|⋯|…|\.\.\.|•••|···)$/;
  const iconText = b => { const i = b.querySelector('mat-icon, .material-icons, .material-symbols-outlined, ' +
                                                   '.material-icons-outlined, i');
                          return i ? (i.innerText || '').trim().toLowerCase() : ''; };
  const plain = el => { const c = el.cloneNode(true);
                        c.querySelectorAll('mat-icon, .material-icons, .material-symbols-outlined, ' +
                                           '.material-icons-outlined, i, svg').forEach(x => x.remove());
                        return (c.innerText || c.textContent || '').trim().split('\n')[0].trim(); };
  const isKebab = b => {
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
  const MENU = '[role=menu], .mat-mdc-menu-panel, .mat-menu-panel, .dropdown-menu, .ant-dropdown, .el-dropdown-menu, ' +
               '.p-menu, .p-tieredmenu, .k-menu-popup, .MuiMenu-paper, .v-menu__content';
  window.addEventListener('popstate', () => {
    try { if (window.__perftoolClick) window.__perftoolClick({nav: 'back_forward', url: location.href}); } catch (err) {}
  }, true);
  document.addEventListener('click', e => {
    try {
      if (!window.__perftoolClick || !e.target || !e.target.closest || e.target.closest('#perftool-rec-bar')) return;
      const b = e.target.closest('button, [role=button], a, [role=menuitem], [role=tab], .mat-mdc-menu-item, ' +
                                 '.mat-menu-item, .dropdown-item, .ant-dropdown-menu-item, li, td, [role=cell]');
      if (!b) return;
      const row = b.closest('tbody tr, [role=row], mat-row, .mat-mdc-row, .ant-table-row, .el-table__row');
      let rowNo = 0;
      if (row && row.parentElement) {
        const rows = Array.from(row.parentElement.children).filter(r => !r.querySelector('th'));
        rowNo = rows.indexOf(row) + 1;
      }
      const kebab = isKebab(b);
      // nút ⋮ chỉ có biểu tượng -> ghi là '⋮' (như khi tự quét), không ghi tên biểu tượng 'more_vert'
      const lab = (kebab && !plain(b)) ? '⋮'
                : (plain(b) || (b.getAttribute('aria-label') || b.title || '').trim() || iconText(b));
      window.__perftoolClick({label: lab.slice(0, 60), kebab, row: rowNo, menu: !!b.closest(MENU),
                              url: location.href});
    } catch (err) { /* không ảnh hưởng thao tác của người dùng */ }
  }, true);
})();
"""
MENU_WINDOW_MS = 15000   # mục menu được bấm trong khoảng này sau nút ⋮ -> coi là mục của menu ⋮ đó
TRIGGER_WINDOW_MS = 8000  # cú bấm chỉ được coi là đã "mở" màn hình mới nếu xảy ra trong khoảng này trước khi đổi màn hình


class _Segment:
    def __init__(self, kind: str, url: str, page_url: str, key: tuple):
        self.kind, self.url, self.page_url, self.key = kind, url, page_url, key
        self.title = ""
        self.stats: dict = {}
        self.requests: list[dict] = []
        self.trigger = ""        # nút / mục đã bấm để mở ra đoạn này, vd "dòng 1 · ⋮ › Xin gia hạn"
        self.from_url = ""       # màn hình đang mở lúc bấm (trang con mở từ màn hình danh sách)
        self.started_ms = _ms()

    def to_dict(self) -> dict:
        return {"kind": self.kind, "url": self.url, "page_url": self.page_url, "title": self.title,
                "stats": self.stats, "requests": self.requests, "trigger": self.trigger, "from_url": self.from_url}


def load_record(path: Path, log=None) -> dict:
    """Đọc record.json. File hỏng / sai cấu trúc -> sao lưu thành record.json.bak-<giờ> (không ghi đè mất dữ liệu cũ)."""
    if not path.exists():
        return {"sessions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("sessions"), list):
            data["sessions"] = [x for x in data["sessions"] if isinstance(x, dict)]
            return data
        raise ValueError("sai cấu trúc")
    except Exception as e:  # noqa: BLE001
        bak = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        try:
            path.replace(bak)
            if log:
                log(f"  ⚠ record.json cũ bị hỏng ({str(e)[:80]}) – đã sao lưu thành {bak.name}, bắt đầu file mới.")
        except Exception:  # noqa: BLE001
            pass
    return {"sessions": []}


class ManualRecorder:
    def __init__(self, crawler: "WebCrawler", out_file: Path, stop_file: Path, module: str = "",
                 guard_level: str = "keyword"):
        self.c = crawler
        self.out_file, self.stop_file = out_file, stop_file
        self.module, self.guard_level = module, guard_level
        self.data = load_record(out_file, getattr(crawler, "log", None))
        self.session = {"started": _now(), "finished": "", "module": module, "guard": guard_level,
                        "start_url": "", "segments": [], "events": []}
        self.data["sessions"].append(self.session)
        self.seg: Optional[_Segment] = None
        self.n_pages = self.n_popups = 0
        self._kebab: Optional[dict] = None       # nút ⋮ vừa bấm (chờ người dùng chọn mục trong menu)
        self._recorders: dict = {}                # tab -> RequestRecorder gắn NGAY khi tab được mở (không mất request đầu)

    # ------------------------------------------------------------ cú bấm
    def _on_click(self, _source, data: dict) -> None:
        """Nhận 1 cú bấm từ trình duyệt; đặt tên thao tác kiểu tự quét: 'dòng 1 · ⋮ › Xin gia hạn'."""
        try:
            now = _ms()
            if data.get("nav"):          # Back/Forward: không phải cú bấm mở màn hình
                self.session["events"].append({"t": now, "time": _now(), "url": str(data.get("url") or ""),
                                               "nav": str(data.get("nav")), "trigger": ""})
                return
            label = str(data.get("label") or "").strip()
            row = int(data.get("row") or 0)
            ev = {"t": now, "time": _now(), "url": str(data.get("url") or ""), "label": label, "row": row,
                  "kebab": bool(data.get("kebab")), "menu": bool(data.get("menu")), "trigger": ""}
            if ev["kebab"]:
                self._kebab = ev
            elif ev["menu"] and self._kebab and now - self._kebab["t"] <= MENU_WINDOW_MS and label:
                k = self._kebab
                prefix = f"dòng {k['row']} · " if k["row"] else ""
                ev["trigger"] = f"{prefix}{k['label'] or '⋮'} › {label}"
                ev["from_url"] = k["url"]
                self._kebab = None
            elif label:
                ev["trigger"] = (f"dòng {row} · " if row else "") + label
            self.session["events"].append(ev)
        except Exception:  # noqa: BLE001 – không để lỗi ghi cú bấm làm dừng phiên ghi
            pass

    def _trigger_since(self, since_ms: float) -> tuple[str, str]:
        """(tên thao tác, URL màn hình lúc bấm) của cú bấm gần nhất sau mốc since_ms và trong TRIGGER_WINDOW_MS vừa qua.
        Gặp sự kiện Back/Forward trước -> không có cú bấm nào mở màn hình này."""
        since_ms = max(since_ms, _ms() - TRIGGER_WINDOW_MS)
        for ev in reversed(self.session["events"]):
            if ev["t"] < since_ms or ev.get("nav"):
                break
            if ev.get("trigger"):
                return ev["trigger"], ev.get("from_url") or ev.get("url", "")
        return "", ""

    def _boundary(self, last_poll: float) -> float:
        """Mốc chia request giữa đoạn cũ và đoạn mới: lúc người dùng bấm (cú bấm cuối trong đoạn cũ) – request bắt đầu trước
        cú bấm thuộc đoạn cũ (vd API tải trang khi mở popup ngay, API của popup trước khi bấm đóng). Không có cú bấm nào:
        request trong SHIFT_MS trước lần phát hiện đổi màn hình thuộc đoạn mới."""
        since = max(self.seg.started_ms if self.seg else 0, _ms() - TRIGGER_WINDOW_MS)
        clicks = [ev["t"] for ev in self.session["events"] if ev.get("t", 0) >= since and not ev.get("nav")]
        return clicks[-1] - CLICK_LAG_MS if clicks else last_poll - SHIFT_MS

    def _nav_type(self) -> str:
        """Kiểu điều hướng của trang hiện tại (navigate | reload | back_forward) – Back/gõ lại URL không có cú bấm."""
        try:
            # chỉ xét khi trang vừa tải lại cả trang (< 10 s) – đổi màn hình trong SPA giữ kiểu điều hướng của lần tải đầu
            return self.c.page.evaluate("() => { const n = performance.getEntriesByType('navigation')[0]; "
                                        "return (n && performance.now() < 10000) ? n.type : ''; }") or ""
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------ vòng ghi
    def run(self, start_url: str) -> None:
        c = self.c
        self.stop_file.unlink(missing_ok=True)
        if self.guard_level != "off":
            c.enable_write_guard(self.guard_level)
        c.context.add_init_script(TOOLBAR_JS)
        self._recorders[c.page] = c.recorder
        c.context.on("page", self._on_new_page)
        try:
            c.context.expose_binding("__perftoolClick", self._on_click)
            c.context.add_init_script(CLICK_JS)
        except Exception as e:  # noqa: BLE001 – không ghi được cú bấm vẫn ghi màn hình/popup như trước
            c.log(f"  (không theo dõi được cú bấm: {e})")
        c.recorder.reset()          # bỏ request của bước đăng nhập
        c.page.goto(start_url, wait_until="domcontentloaded")
        c._settle(500)
        self.session["start_url"] = c.page.url
        self._eval(TOOLBAR_JS)
        self._eval(CLICK_JS)
        c.log("Bắt đầu ghi. Hãy thao tác trên cửa sổ trình duyệt: mở từng màn hình, bấm menu ⋮, mở popup Xem/Xử lý/"
              "Phân công…; bấm 'Kết thúc ghi' ở góc dưới phải trình duyệt (hoặc trên PerfTool) khi xong.")
        if self.guard_level != "off":
            c.log("Bộ chặn ghi đang BẬT: bấm Lưu/Gửi/Duyệt… sẽ KHÔNG ghi dữ liệu thật (request bị huỷ nhưng vẫn được ghi nhận).")
        deadline = time.time() + 60 * float(config.get("crawler.record_max_minutes", 60))
        last_poll = _ms()
        last_save = time.time()
        reason = "hết thời gian ghi tối đa"
        while time.time() < deadline:
            page = self._current_page()
            if page is None:
                reason = "đã đóng trình duyệt"
                break
            if self.stop_file.exists():
                reason = "bấm Kết thúc ghi trên PerfTool"
                break
            if self._eval("() => window.__perftoolStop === true"):
                reason = "bấm Kết thúc ghi trên trình duyệt"
                break
            now_ms = _ms()
            try:
                url = page.url
            except Exception:  # noqa: BLE001
                reason = "đã đóng trình duyệt"
                break
            m = self.c.modal_stats()
            key = (route_of(url), (m.get("title") or "popup") if m else None)
            if self.seg is None:
                self._start(key, url, m)
            elif key != self.seg.key:
                self._finish(before_ms=self._boundary(last_poll))
                self._start(key, url, m)
            else:
                self._absorb(now_ms - FLUSH_AFTER_MS)
                self._snapshot(m)
            if time.time() - last_save > 10:
                self._save()
                last_save = time.time()
            last_poll = now_ms
            try:
                page.wait_for_timeout(POLL_MS)
            except Exception:  # noqa: BLE001
                reason = "đã đóng trình duyệt"
                break
        self._finish(before_ms=float("inf"))
        self.session["finished"] = _now()
        self._save()
        blocked = self.c.guard.count if self.c.guard else 0
        c.log(f"Kết thúc ghi ({reason}): {self.n_pages} màn hình trang, {self.n_popups} popup, "
              f"{sum(len(s['requests']) for s in self.session['segments'])} request"
              + (f", đã chặn {blocked} request ghi dữ liệu" if blocked else "") + ".")

    # ------------------------------------------------------------ tiện ích
    def _on_new_page(self, page) -> None:
        """Tab mới vừa mở: gắn bộ ghi request NGAY (request lúc tab tải trang không bị mất)."""
        from .web_crawler import RequestRecorder
        try:
            rec = RequestRecorder(page)
            rec.blocked = self.c.recorder.blocked
            rec.secrets = self.c.recorder.secrets
            self._recorders[page] = rec
        except Exception:  # noqa: BLE001
            pass

    def _current_page(self):
        """Theo trang mới nhất (người dùng mở tab mới) – dùng bộ ghi request đã gắn cho tab đó."""
        from .web_crawler import RequestRecorder
        try:
            pages = [p for p in self.c.context.pages if not p.is_closed()]
        except Exception:  # noqa: BLE001
            return None
        if not pages:
            return None
        newest = pages[-1]
        if newest is not self.c.page:
            self._finish(before_ms=float("inf"))
            self.seg = None
            blocked, secrets = self.c.recorder.blocked, self.c.recorder.secrets
            self.c.page = newest
            self.c.recorder = self._recorders.get(newest) or RequestRecorder(newest)
            self.c.recorder.blocked, self.c.recorder.secrets = blocked, secrets
            self.c.log(f"  (chuyển sang tab mới: {newest.url})")
        return newest

    def _eval(self, js: str):
        try:
            return self.c.page.evaluate(js)
        except Exception:  # noqa: BLE001 - trang đang chuyển
            return None

    def _start(self, key: tuple, url: str, m: Optional[dict]) -> None:
        kind = "popup" if m else "page"
        prev = self.seg
        page_url = prev.page_url if (m and prev) else url
        self.seg = _Segment(kind, url, page_url, key)
        if prev:
            # cú bấm trong lúc đang ở đoạn trước (trừ hao độ trễ phát hiện) đã mở ra đoạn này; bấm Back (tải lại cả trang)
            # thì không có cú bấm nào mở màn hình
            trig, from_url = ("", "") if (kind == "page" and self._nav_type() == "back_forward") \
                else self._trigger_since(prev.started_ms - 500)
            self.seg.trigger = trig
            self.seg.from_url = from_url or prev.page_url
        self._snapshot(m)

    def _snapshot(self, m: Optional[dict]) -> None:
        s = self.seg
        if s.kind == "popup":
            if m:
                s.stats = {k: m.get(k, 0) for k in ("inputs", "selects", "buttons", "tables", "text")}
                s.title = m.get("title") or s.title
            return
        st = self._eval_stats()
        if st:
            s.stats = {k: int(st.get(k) or 0) for k in ("forms", "inputs", "selects", "buttons", "tables", "table_rows")}
            s.stats["crud"] = st.get("crud") or []
            s.title = (st.get("h1") or st.get("title") or "").strip()[:200]

    def _eval_stats(self) -> Optional[dict]:
        try:
            return self.c.page.evaluate(self.c.JS_PAGE_STATS, self.c.crud_words)
        except Exception:  # noqa: BLE001
            return None

    def _absorb(self, before_ms: float) -> None:
        items = self.c.recorder.take(before_ms)
        if items and self.seg:
            reqs = self.c.recorder.collect(page_url=self.seg.page_url, items=items)
            for r in reqs:
                r.origin = "manual"
            self.seg.requests += [r.model_dump() for r in reqs]

    def _finish(self, before_ms: float) -> None:
        s = self.seg
        if not s:
            return
        self._absorb(before_ms)
        n_api = sum(1 for r in s.requests if r["resource_type"] in ("xhr", "fetch"))
        if s.kind == "page" and not s.requests and self.session["segments"] and \
                self.session["segments"][-1]["page_url"] == s.page_url:
            return          # quay lại trang cũ sau khi đóng popup, không có request mới -> bỏ
        self.session["segments"].append(s.to_dict())
        if s.kind == "popup":
            self.n_popups += 1
            n_in = s.stats.get("inputs", 0) + s.stats.get("selects", 0)
            self.c.log(f"  ▣ Popup '{s.title[:50]}': {n_in} trường nhập, {s.stats.get('tables', 0)} bảng, {n_api} API"
                       + (f" · mở bằng: {s.trigger}" if s.trigger else ""))
        else:
            self.n_pages += 1
            self.c.log(f"  ▭ Màn hình '{s.title[:50]}' {s.url[:90]}: {n_api} API"
                       + (f" · mở bằng: {s.trigger}" if s.trigger else ""))
        self._save()

    def _save(self) -> None:
        tmp = self.out_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(self.out_file)


def _ms() -> float:
    return time.time() * 1000


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
