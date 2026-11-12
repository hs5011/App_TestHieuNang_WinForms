"""Hợp nhất kết quả crawler (discover.json / analysis.json) vào Project."""
from __future__ import annotations

import json

from rapidfuzz import fuzz

from ..models import AuthCapture, CapturedRequest, ModuleInfo, PageInfo, PopupInfo, Project
from ..storage import hydrate_session, sub_dir
from .interactions import page_key, route_of, screen_key

MANUAL_MODULE = "Ghi thao tác thủ công"
from ..uc_import.importer import norm


def merge_discover(p: Project) -> Project:
    data = json.loads((sub_dir(p.id, "crawl") / "discover.json").read_text(encoding="utf-8"))
    old = {m.name: m for m in p.modules}
    uc_mods = {norm(u.module) for u in p.use_cases if u.module}
    mods = []
    for m in data.get("modules", []):
        if m["name"] in old:
            enabled = old[m["name"]].enabled
        elif uc_mods:
            # tự tick các phân hệ có tên gần với phân hệ trong danh sách UC
            enabled = max((fuzz.token_set_ratio(norm(m["name"]), um) for um in uc_mods), default=0) >= 60
        else:
            enabled = True
        mods.append(ModuleInfo(name=m["name"], url=m["url"], enabled=enabled,
                               pages=old[m["name"]].pages if m["name"] in old else []))
    names = {m.name for m in mods}
    mods += [m for m in p.modules if m.name not in names]   # giữ phân hệ người dùng tự thêm
    p.modules = mods
    p.auth = AuthCapture.model_validate(data.get("auth", {}))
    hydrate_session(p)
    return p


def merge_analysis(p: Project) -> Project:
    data = json.loads((sub_dir(p.id, "crawl") / "analysis.json").read_text(encoding="utf-8"))
    by_name = {m["name"]: m for m in data.get("modules", [])}
    for m in p.modules:
        if m.name in by_name:
            m.pages = [PageInfo.model_validate(x) for x in by_name[m.name]["pages"]]
    existing = {x.name for x in p.modules}
    for name, m in by_name.items():
        if name not in existing:
            p.modules.append(ModuleInfo(name=name, url=m["url"], enabled=True,
                                        pages=[PageInfo.model_validate(x) for x in m["pages"]]))
    _dedupe_pages(p)
    auth = AuthCapture.model_validate(data.get("auth", {}))
    if auth.login_url or auth.static_headers or auth.cookies:
        p.auth = auth
    hydrate_session(p)       # cookie / Authorization chỉ nằm ở crawl/auth.json
    apply_record(p)          # quét lại tự động không làm mất dữ liệu ghi thao tác thủ công
    p.scores = []
    return p


# ---------------------------------------------------------------- ghi thao tác thủ công
def record_file(p: Project):
    return sub_dir(p.id, "crawl") / "record.json"


def load_segments(p: Project) -> list[dict]:
    """Các đoạn màn hình đã ghi (mọi lần ghi), kèm 'module' của lần ghi. Phần sai cấu trúc bị bỏ qua."""
    return [{**seg, "module": str(s.get("module") or ""), "session": s.get("started", "")}
            for s in load_sessions(p) for seg in s["segments"]]


def _manual_enabled(p: Project):
    m = next((x for x in p.modules if x.name == MANUAL_MODULE), None)
    return m.enabled if m else None


def strip_manual(p: Project) -> None:
    """Gỡ toàn bộ dữ liệu ghi thủ công đã gộp trước đó (để gộp lại không bị trùng)."""
    for m in p.modules:
        m.pages = [pg for pg in m.pages if pg.origin != "manual"]
        for pg in m.pages:
            pg.requests = [r for r in pg.requests if r.origin != "manual"]
            pg.popups = [pp for pp in pg.popups if pp.origin != "manual"]
    p.modules = [m for m in p.modules if not (m.name == MANUAL_MODULE and not m.pages)]


def _find_module(p: Project, url: str, fixed: str, from_url: str = "") -> ModuleInfo:
    key = route_of(url)
    # màn hình đã có ở 1 phân hệ (kể cả khi người dùng chọn "Gán vào phân hệ X") -> gộp vào đó, không tạo trùng
    for m in p.modules:
        if any(route_of(pg.url) == key for pg in m.pages):
            return m
    if fixed:
        m = next((x for x in p.modules if x.name == fixed), None)
        if m:
            return m
    for m in p.modules:
        if route_of(m.url) == key:
            return m
    if from_url:        # trang con mở từ màn hình khác (vd ⋮ › Phân công) -> cùng phân hệ với màn hình đó
        src = _page_at(p, from_url)
        if src:
            m = next((x for x in p.modules if src in x.pages), None)
            if m and m.name != MANUAL_MODULE:
                return m
    # phân hệ có URL (menu / màn hình) chung tiền tố dài nhất với URL đã ghi
    best, best_len = None, 0
    for m in p.modules:
        for u in [m.url] + [pg.url for pg in m.pages]:
            a, b = route_of(u), key
            n = 0
            while n < min(len(a), len(b)) and a[n] == b[n]:
                n += 1
            if a[:n].split("/", 1)[-1].count("/") >= 2 and n > best_len:   # chung ít nhất 2 đoạn đường dẫn sau tên miền
                best, best_len = m, n
    if best:
        return best
    m = next((x for x in p.modules if x.name == MANUAL_MODULE), None)
    if not m:
        m = ModuleInfo(name=MANUAL_MODULE, url=url, enabled=True)
        p.modules.append(m)
    return m


def apply_record(p: Project) -> int:
    """Gộp crawl/record.json vào các màn hình của dự án. Trả về số đoạn màn hình đã gộp."""
    was_enabled = _manual_enabled(p)
    strip_manual(p)
    segs = load_segments(p)
    for seg in segs:
        try:
            _apply_segment(p, seg)
        except Exception:  # noqa: BLE001 - đoạn hỏng không làm hỏng các đoạn khác / bước 3b
            continue
    try:
        _link_manual_clicks(p)
    except Exception:  # noqa: BLE001
        pass
    if was_enabled is not None:          # giữ lựa chọn bỏ tick phân hệ "Ghi thao tác thủ công" của người dùng
        for m in p.modules:
            if m.name == MANUAL_MODULE:
                m.enabled = was_enabled
    p.scores = []
    return len(segs)


def _subpage_host(p: Project, url: str):
    """Màn hình có tương tác 'Trang con' (tự quét, được tính màn hình) trỏ tới url – trang con đó đã được tính."""
    key = route_of(url)
    return next((pg for m in p.modules for pg in m.pages
                 if any(pp.kind == "page" and pp.counted and pp.url and route_of(pp.url) == key for pp in pg.popups)), None)


def _apply_segment(p: Project, seg: dict) -> None:
    url = seg.get("page_url") or seg.get("url") or ""
    if not url:
        return
    reqs_in = [d for d in (seg.get("requests") or []) if isinstance(d, dict)]
    has_api = any(r.get("resource_type") in ("xhr", "fetch") for r in reqs_in)
    if seg.get("kind") == "page" and not has_api and not any(
            route_of(pg.url) == route_of(url) for m in p.modules for pg in m.pages):
        return          # trang mới không gọi API nào (vd trang chủ lúc mở trình duyệt) -> không tạo màn hình
    mod = _find_module(p, url, seg.get("module", ""), seg.get("from_url") or "")
    page = next((pg for pg in mod.pages if route_of(pg.url) == route_of(url)), None)
    if page is None and seg.get("kind") == "page":
        # trang con đã được tự quét ghi nhận & tính màn hình (⋮ › Phân công) -> gộp API vào màn hình đó, không đếm 2 lần
        page = _subpage_host(p, url)
    if page is None:
        st = seg.get("stats", {}) if seg.get("kind") == "page" else {}
        page = PageInfo(url=url, module=mod.name, origin="manual", title=seg.get("title", "") if seg.get("kind") == "page" else "",
                        menu_text=(seg.get("title") or "")[:80] if seg.get("kind") == "page" else "",
                        **{k: int(st.get(k) or 0) for k in ("forms", "inputs", "selects", "buttons", "tables",
                                                             "table_rows")},
                        crud_buttons=list(st.get("crud") or []))
        mod.pages.append(page)
    have = {(r.method, r.url) for r in page.requests}
    reqs = []
    for d in reqs_in:
        try:
            r = CapturedRequest.model_validate({"method": "GET", **d, "origin": "manual"})
        except Exception:  # noqa: BLE001 - request thiếu/sai trường -> bỏ qua
            continue
        if (r.method, r.url) not in have:
            have.add((r.method, r.url))
            reqs.append(r)
    page.requests.extend(reqs)
    if seg.get("kind") == "popup":
        st = seg.get("stats", {})
        title = seg.get("title") or "Popup"
        if not any(pp.title == title for pp in page.popups):
            counted = bool((st.get("inputs", 0) + st.get("selects", 0)) or st.get("tables", 0) or st.get("text", 0) > 150)
            page.popups.append(PopupInfo(trigger=seg.get("trigger") or "(ghi thủ công)", kind="popup", origin="manual",
                                         title=title, inputs=st.get("inputs", 0), selects=st.get("selects", 0),
                                         buttons=st.get("buttons", 0), tables=st.get("tables", 0), counted=counted,
                                         api_count=len([r for r in reqs if r.resource_type in ("xhr", "fetch")])))
    elif page.origin == "manual" and not page.title:
        page.title = page.menu_text = (seg.get("title") or "")[:200]


def _page_at(p: Project, url: str):
    key = route_of(url)
    return next((pg for m in p.modules for pg in m.pages if route_of(pg.url) == key), None) if url else None


def _link_manual_clicks(p: Project) -> None:
    """Gắn các cú bấm đã ghi vào màn hình lúc bấm (bảng 'Popup / trang con / tương tác' ở 3d), giống kết quả tự quét:
    - trang con mở từ màn hình khác (vd 'dòng 1 · ⋮ › Phân công') -> tương tác 'Trang con' của màn hình danh sách;
    - mục menu ⋮ không mở popup/trang nào -> tương tác 'Thao tác'.
    Trang con đã là 1 màn hình riêng nên không tính thêm lần nữa (counted=False)."""
    for s in load_sessions(p):
        used = set()
        opened: set[str] = set()       # màn hình đã mở trước đó trong lần ghi -> quay lại (Back) không phải trang con
        for seg in s["segments"]:
            trig, src, url = seg.get("trigger") or "", seg.get("from_url") or "", seg.get("url") or ""
            if seg.get("kind") == "popup" and trig:
                used.add(trig)
            back = route_of(url) in opened
            if seg.get("kind") == "page" and url:
                opened.add(route_of(url))
            if src:
                opened.add(route_of(src))
            if seg.get("kind") != "page" or not trig or not src or route_of(src) == route_of(url) or back:
                continue
            used.add(trig)
            origin_pg = _page_at(p, src)
            if origin_pg and not any(pp.kind == "page" and pp.trigger == trig and route_of(pp.url) == route_of(url)
                                     for pp in origin_pg.popups):
                origin_pg.popups.append(PopupInfo(
                    trigger=trig, kind="page", url=url, origin="manual", title=(seg.get("title") or "")[:200],
                    counted=False, api_count=sum(1 for r in seg.get("requests", [])
                                                 if r.get("resource_type") in ("xhr", "fetch"))))
        for ev in s["events"]:
            trig = ev.get("trigger") or ""
            if "›" not in trig or trig in used:       # chỉ mục menu ⋮ chưa gắn với popup/trang con nào
                continue
            used.add(trig)
            origin_pg = _page_at(p, ev.get("from_url") or ev.get("url") or "")
            if origin_pg and not any(pp.trigger == trig for pp in origin_pg.popups):
                origin_pg.popups.append(PopupInfo(trigger=trig, kind="action", origin="manual", counted=False))


def clear_record(p: Project) -> None:
    record_file(p).unlink(missing_ok=True)
    strip_manual(p)
    p.scores = []


def load_sessions(p: Project) -> list[dict]:
    """Các lần ghi (theo thứ tự thời gian), mỗi lần gồm started/finished/module/segments."""
    f = record_file(p)
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:  # noqa: BLE001
        return []
    sessions = data.get("sessions") if isinstance(data, dict) else None
    out = []
    for s in sessions if isinstance(sessions, list) else []:
        if not isinstance(s, dict):
            continue
        segs = s.get("segments")
        evs = s.get("events")
        out.append({**s, "segments": [x for x in segs if isinstance(x, dict)] if isinstance(segs, list) else [],
                    "events": [x for x in evs if isinstance(x, dict)] if isinstance(evs, list) else []})
    return out


def segment_status(p: Project, s: dict) -> list[str]:
    """Kết quả gộp của từng đoạn trong 1 lần ghi (cùng quy tắc apply_record) – hiện ở 3c để biết đoạn nào được đưa vào 3d."""
    out, seen = [], set()
    for seg in s.get("segments", []):
        url = seg.get("page_url") or seg.get("url") or ""
        has_api = any(r.get("resource_type") in ("xhr", "fetch") for r in seg.get("requests", []))
        title = seg.get("title") or "Popup"
        if not url:
            out.append("⛔ Bỏ qua – không có URL")
        elif seg.get("kind") == "page" and not has_api and not any(
                route_of(pg.url) == route_of(url) for m in p.modules for pg in m.pages):
            out.append("⛔ Bỏ qua – trang mới không gọi API")
        elif seg.get("kind") == "popup" and title in seen:
            out.append("↺ Trùng popup đã ghi ở trên")
        else:
            if seg.get("kind") == "popup":
                seen.add(title)
            out.append("✅ Đã đưa vào kết quả")
    return out


def delete_session(p: Project, started: str) -> bool:
    """Xoá 1 lần ghi (khoá = giờ bắt đầu) khỏi record.json rồi gộp lại các lần còn lại. False nếu không tìm thấy."""
    f = record_file(p)
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"sessions": []}
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        return False
    sessions = [x for x in data["sessions"] if isinstance(x, dict)]
    keep = [s for s in sessions if s.get("started") != started]
    if len(keep) == len(sessions):
        return False
    if keep:
        data["sessions"] = keep
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(f)
    else:
        f.unlink(missing_ok=True)
    apply_record(p)          # gỡ toàn bộ phần đã gộp rồi gộp lại các lần ghi còn lại
    return True


def _dedupe_pages(p: Project) -> None:
    """Mỗi màn hình chỉ gán cho 1 phân hệ: ưu tiên phân hệ có URL menu trùng URL màn hình, sau đó lần xuất hiện đầu."""
    owner: dict[str, str] = {screen_key(pg): m.name for m in p.modules for pg in m.pages
                             if page_key(pg.url) == page_key(m.url)}
    for m in p.modules:
        for pg in m.pages:
            owner.setdefault(screen_key(pg), m.name)
    for m in p.modules:
        kept, seen = [], set()
        for pg in m.pages:
            k = screen_key(pg)
            if owner.get(k) == m.name and k not in seen:
                pg.module = m.name
                kept.append(pg)
                seen.add(k)
        m.pages = kept
