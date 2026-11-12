"""Quét tương tác (menu ⋮, tab…), bộ chặn ghi dữ liệu và gộp dữ liệu ghi thao tác thủ công."""
import json

from perftool import storage
from perftool.crawler import merge
from perftool.crawler.guard import is_write_request
from perftool.crawler.interactions import is_banned, route_of
from perftool.models import CapturedRequest, ModuleInfo, PageInfo, PopupInfo, Project

API = "https://ntqlcqs.example.vn/api/services/app"


def test_write_guard_keyword_level():
    # truy vấn (kể cả POST) -> cho qua
    for m, u in [("GET", f"{API}/Task/GetAll?id=1"), ("POST", f"{API}/Task/GetAll"),
                 ("POST", f"{API}/Task/GetForEdit"), ("POST", f"{API}/DRViewer/PostData"),
                 ("POST", "https://x.vn/notify/negotiate"), ("POST", "https://x.vn/api/tasks/search")]:
        assert not is_write_request(m, u), (m, u)
    # ghi dữ liệu -> chặn (kể cả GET xhr có từ ghi: nhiều hệ thống cũ ghi bằng GET)
    for m, u in [("GET", f"{API}/Task/Delete?id=1"), ("POST", f"{API}/Task/CreateOrUpdate"), ("POST", f"{API}/Task/Delete"),
                 ("POST", "https://x.vn/api/tasks/complete?id=1"), ("POST", "https://x.vn/FileManager/UploadFile"),
                 ("PUT", "https://x.vn/api/tasks/1"), ("DELETE", "https://x.vn/api/tasks/1"),
                 ("POST", f"{API}/GiaoViec/PhanCong")]:
        assert is_write_request(m, u), (m, u)
    assert is_write_request("POST", "https://x.vn/form", "document")            # form POST cả trang
    assert not is_write_request("POST", "https://x.vn/api/tasks", level="keyword")   # không rõ -> cho qua ở mức keyword
    assert not is_write_request("POST", f"{API}/Task/Delete", level="off")


def test_write_guard_strict_level():
    assert is_write_request("POST", "https://x.vn/api/tasks", level="strict")
    assert not is_write_request("POST", f"{API}/Task/GetAll", level="strict")
    assert not is_write_request("POST", f"{API}/DRViewer/PostData", level="strict")         # PostData: đọc
    assert not is_write_request("POST", f"{API}/Report/ExecuteStoreWithParam", level="strict")
    assert is_write_request("POST", f"{API}/Task/PostCreateTask", level="strict")            # Post + create -> ghi


def test_banned_whole_word():
    bl = ["in ", "xoá", "xóa", "hoàn thành", "ký"]
    assert not is_banned("Xin gia hạn", bl)          # "in" không khớp trong "Xin"
    assert is_banned("In danh sách", bl)
    assert is_banned("Xóa", bl) and is_banned("Đánh dấu hoàn thành", bl)
    assert not is_banned("Phân công", bl) and not is_banned("Xử lý", bl)


def test_route_of():
    assert route_of("https://a.vn/app/task?id=1") == route_of("https://a.vn/app/task/?id=2")
    assert route_of("https://a.vn/#/task?id=1") == "a.vn#/task"
    assert route_of("https://a.vn/#/task") != route_of("https://a.vn/#/news")


def _req(url, method="GET", **kw):
    return CapturedRequest(method=method, url=url, resource_type="xhr", **kw)


def _proj(tmp_path, monkeypatch) -> Project:
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    p = Project(id="rec")
    p.login.base_url = "https://a.vn"
    list_pg = PageInfo(url="https://a.vn/portal/cong-viec/can-xu-ly", menu_text="Việc cần xử lý",
                       requests=[_req("https://a.vn/api/tasks")],
                       popups=[PopupInfo(trigger="thêm mới", title="Thêm mới", inputs=3)])
    news = PageInfo(url="https://a.vn/portal/tin-tuc", menu_text="Tin tức", requests=[_req("https://a.vn/api/news")])
    p.modules = [ModuleInfo(name="Giao việc", url=list_pg.url, pages=[list_pg]),
                 ModuleInfo(name="Tin tức", url=news.url, pages=[news])]
    return p


def _write_record(p: Project, sessions: list[dict]) -> None:
    merge.record_file(p).write_text(json.dumps({"sessions": sessions}, ensure_ascii=False), encoding="utf-8")


def test_apply_record_merges_and_is_idempotent(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    segs = [
        {"kind": "page", "url": lst + "?page=1", "page_url": lst + "?page=1", "title": "Việc cần xử lý",
         "stats": {}, "requests": [_req("https://a.vn/api/tasks").model_dump(),
                                   _req("https://a.vn/api/tasks/count").model_dump()]},
        {"kind": "popup", "url": lst, "page_url": lst, "title": "Xử lý công việc",
         "stats": {"inputs": 3, "selects": 1, "buttons": 2, "tables": 0, "text": 300},
         "requests": [_req("https://a.vn/api/tasks/detail?id=1").model_dump()]},
        {"kind": "page", "url": "https://a.vn/portal/cong-viec/phan-cong?id=1",
         "page_url": "https://a.vn/portal/cong-viec/phan-cong?id=1", "title": "Phân công",
         "stats": {"inputs": 3, "tables": 1, "crud": ["lưu phân công"]},
         "requests": [_req("https://a.vn/api/tasks/assign", "POST", blocked=True).model_dump()]},
        {"kind": "page", "url": "https://b.vn/khac", "page_url": "https://b.vn/khac", "title": "Khác", "stats": {},
         "requests": [_req("https://b.vn/api/khac").model_dump()]},
        {"kind": "page", "url": "https://a.vn/home", "page_url": "https://a.vn/home", "title": "Trang chủ", "stats": {},
         "requests": []},                                  # trang mới không gọi API -> không tạo màn hình
    ]
    _write_record(p, [{"started": "2026-09-25 10:00:00", "module": "", "segments": segs}])
    assert merge.apply_record(p) == 5
    assert not any("home" in pg.url for pg in p.all_pages())
    gv = p.modules[0]
    lst_pg = gv.pages[0]
    # trang đã có: chỉ thêm API mới (không trùng), popup ghi thủ công được thêm và tính màn hình
    assert [r.url for r in lst_pg.requests] == ["https://a.vn/api/tasks", "https://a.vn/api/tasks/count",
                                                 "https://a.vn/api/tasks/detail?id=1"]
    manual_pop = [pp for pp in lst_pg.popups if pp.origin == "manual"]
    assert len(manual_pop) == 1 and manual_pop[0].counted and manual_pop[0].title == "Xử lý công việc"
    # trang mới cùng nhánh đường dẫn -> gán vào phân hệ Giao việc, giữ request ghi bị chặn
    new = next(pg for pg in gv.pages if "phan-cong" in pg.url)
    assert new.origin == "manual" and new.tables == 1 and new.requests[0].blocked
    assert "lưu phân công" in new.crud_buttons
    # URL không khớp phân hệ nào -> phân hệ "Ghi thao tác thủ công"
    assert p.modules[-1].name == merge.MANUAL_MODULE
    # gộp lại lần nữa không bị trùng
    merge.apply_record(p)
    assert len(lst_pg.requests) == 3 and len([pp for pp in lst_pg.popups if pp.origin == "manual"]) == 1
    assert len([pg for pg in gv.pages if "phan-cong" in pg.url]) == 1
    # xoá dữ liệu ghi -> trả về như trước khi gộp
    merge.clear_record(p)
    assert [len(m.pages) for m in p.modules] == [1, 1]
    assert [r.url for r in p.modules[0].pages[0].requests] == ["https://a.vn/api/tasks"]
    assert [pp.title for pp in p.modules[0].pages[0].popups] == ["Thêm mới"]


def test_apply_record_fixed_module(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    _write_record(p, [{"started": "s1", "module": "Tin tức", "segments": [
        {"kind": "page", "url": "https://a.vn/portal/cong-viec/phan-cong", "page_url": "https://a.vn/portal/cong-viec/phan-cong",
         "title": "Phân công", "stats": {}, "requests": [_req("https://a.vn/api/users").model_dump()]}]}])
    merge.apply_record(p)
    assert [pg.url for pg in p.modules[1].pages][-1] == "https://a.vn/portal/cong-viec/phan-cong"


def test_delete_one_record_session_keeps_others(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    pop = lambda t: {"kind": "popup", "url": lst, "page_url": lst, "title": t, "stats": {"inputs": 2},  # noqa: E731
                     "requests": []}
    _write_record(p, [
        {"started": "2026-09-25 14:09:46", "module": "", "segments": [
            pop("Xin gia hạn"), pop("Xin gia hạn"),
            {"kind": "page", "url": "https://a.vn/home", "page_url": "https://a.vn/home", "title": "Trang chủ",
             "stats": {}, "requests": []}]},
        {"started": "2026-09-25 14:14:06", "module": "", "segments": [pop("Báo cáo")]}])
    merge.apply_record(p)
    manual = lambda: [pp.title for pp in p.modules[0].pages[0].popups if pp.origin == "manual"]  # noqa: E731
    assert manual() == ["Xin gia hạn", "Báo cáo"]
    s1 = merge.load_sessions(p)[0]
    assert merge.segment_status(p, s1) == ["✅ Đã đưa vào kết quả", "↺ Trùng popup đã ghi ở trên",
                                           "⛔ Bỏ qua – trang mới không gọi API"]
    assert merge.delete_session(p, "2026-09-25 14:09:46")
    assert manual() == ["Báo cáo"] and [s["started"] for s in merge.load_sessions(p)] == ["2026-09-25 14:14:06"]
    assert not merge.delete_session(p, "không có")
    assert merge.delete_session(p, "2026-09-25 14:14:06")
    assert not merge.record_file(p).exists() and manual() == []


def test_manual_clicks_name_kebab_menu_items():
    from perftool.crawler.manual import ManualRecorder
    rec = ManualRecorder.__new__(ManualRecorder)
    rec.session, rec._kebab = {"events": []}, None
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    rec._on_click(None, {"label": "⋮", "kebab": True, "row": 1, "menu": False, "url": lst})
    rec._on_click(None, {"label": "Xin gia hạn", "kebab": False, "row": 0, "menu": True, "url": lst})
    rec._on_click(None, {"label": "Tìm kiếm", "kebab": False, "row": 0, "menu": False, "url": lst})
    rec._on_click(None, {"label": "Mục lẻ", "kebab": False, "row": 0, "menu": True, "url": lst})   # không có ⋮ trước đó
    assert [e["trigger"] for e in rec.session["events"]] == ["", "dòng 1 · ⋮ › Xin gia hạn", "Tìm kiếm", "Mục lẻ"]
    assert rec.session["events"][1]["from_url"] == lst


def test_apply_record_links_kebab_items_to_list_page(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    sub = "https://a.vn/portal/cong-viec/can-xu-ly/tien-do?id=586"
    _write_record(p, [{"started": "s1", "module": "", "segments": [
        {"kind": "popup", "url": lst, "page_url": lst, "title": "Xin gia hạn", "stats": {"inputs": 2}, "requests": [],
         "trigger": "dòng 1 · ⋮ › Xin gia hạn", "from_url": lst},
        {"kind": "page", "url": sub, "page_url": sub, "title": "Phân công", "stats": {},
         "requests": [_req("https://a.vn/api/tasks/586").model_dump()],
         "trigger": "dòng 1 · ⋮ › Phân công", "from_url": lst}],
        "events": [{"t": 1, "url": lst, "label": "Hoàn thành", "trigger": "dòng 1 · ⋮ › Hoàn thành", "from_url": lst},
                   {"t": 2, "url": lst, "label": "Tìm kiếm", "trigger": "Tìm kiếm"}]}])
    merge.apply_record(p)
    lst_pg = p.modules[0].pages[0]
    got = {(pp.kind, pp.trigger, pp.counted) for pp in lst_pg.popups if pp.origin == "manual"}
    assert got == {("popup", "dòng 1 · ⋮ › Xin gia hạn", True), ("page", "dòng 1 · ⋮ › Phân công", False),
                   ("action", "dòng 1 · ⋮ › Hoàn thành", False)}           # "Tìm kiếm" (không phải mục ⋮) không thêm
    link = next(pp for pp in lst_pg.popups if pp.kind == "page")
    assert link.url == sub and link.api_count == 1
    assert any(pg.url == sub for pg in p.modules[0].pages)                 # trang con vẫn là 1 màn hình riêng
    merge.apply_record(p)                                                  # gộp lại không trùng
    assert len([pp for pp in lst_pg.popups if pp.origin == "manual"]) == 3
