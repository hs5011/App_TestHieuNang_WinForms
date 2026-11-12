"""Bước 3: quét tương tác, bộ chặn ghi, ghi thủ công 3c, gộp record.json.
Tạo bởi agent kiểm thử 2026-09-30 (mỗi test khẳng định hành vi MONG MUỐN; đã sửa code cho đạt cùng ngày)."""
import json
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from perftool import config, storage  # noqa: E402
from perftool.crawler import merge  # noqa: E402
from perftool.crawler.guard import is_write_request  # noqa: E402
from perftool.crawler.interactions import is_banned  # noqa: E402
from perftool.models import CapturedRequest, ModuleInfo, PageInfo, PopupInfo, Project  # noqa: E402

BL = [w.lower() for w in config.get("crawler.popup_blacklist", [])]
NFD = lambda s: unicodedata.normalize("NFD", s)  # noqa: E731


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    p = Project(id="qa")
    p.login.base_url = "https://a.vn"
    lst = PageInfo(url="https://a.vn/portal/cong-viec/can-xu-ly", requests=[_req("https://a.vn/api/tasks")])
    news = PageInfo(url="https://a.vn/portal/tin-tuc", requests=[_req("https://a.vn/api/news")])
    p.modules = [ModuleInfo(name="Giao việc", url=lst.url, pages=[lst]),
                 ModuleInfo(name="Tin tức", url=news.url, pages=[news])]
    return p


def _req(url, method="GET", **kw):
    return CapturedRequest(method=method, url=url, resource_type="xhr", **kw)


def _write(p, obj):
    merge.record_file(p).write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False),
                                    encoding="utf-8")


def _snap(p):
    return [m.model_dump() for m in p.modules]


# ------------------------------------------------------------------ blacklist (Python)
@pytest.mark.parametrize("label", ["Xoá", "Xóa", "XÓA", NFD("Xóa"), "Delete", "DELETE", "Duyệt", NFD("Duyệt"), "Gửi",
                                   "Lưu", "lưu nháp", "Xem & LƯU", "  Hoàn thành  ", "Ký số", "Xóa bản ghi"])
def test_py_blacklist_variants(label):
    assert is_banned(label, BL)


def test_py_blacklist_nbsp_space_variant():
    # BUG: nhãn dùng &nbsp; giữa 2 từ ("Hoàn&nbsp;thành") -> không khớp cụm "hoàn thành"
    assert is_banned("Hoàn thành", BL)


def test_py_blacklist_not_overmatching():
    for lb in ["Xin gia hạn", "Phân công", "Xử lý", "Xem chi tiết", "Thêm mới"]:
        assert not is_banned(lb, BL), lb


# ------------------------------------------------------------------ guard
def test_guard_write_in_query_string_keyword_level():
    # BUG/giới hạn: hành động ghi nằm trong query string (?action=delete) -> không chặn ở mức keyword
    assert is_write_request("POST", "http://127.0.0.1:8288/api/adv/handler?action=delete&id=1")


@pytest.mark.parametrize("url", ["http://x.vn/api/cong-viec/cap-nhat-trang-thai", "http://x.vn/api/van-ban/trinh-ky",
                                 "http://x.vn/api/van-ban/chuyen-xu-ly", "http://x.vn/api/ho-so/gui"])
def test_guard_vietnamese_write_paths(url):
    # BUG: nhiều động từ ghi tiếng Việt (cập nhật, trình, chuyển, gửi) không có trong write_keywords
    assert is_write_request("POST", url)


def test_guard_read_prefix_overrides_write_keyword():
    # đoạn cuối bắt đầu bằng read_prefix nhưng chứa từ ghi -> vẫn cho qua
    assert is_write_request("POST", "http://x.vn/api/Task/CheckAndApprove")
    assert is_write_request("POST", "http://x.vn/api/timesheet/timesheetDelete")


# ------------------------------------------------------------------ merge / record.json
def test_old_record_without_events_trigger(proj):
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    _write(proj, {"sessions": [{"started": "s", "module": "", "segments": [
        {"kind": "popup", "url": lst, "page_url": lst, "title": "Xử lý", "stats": {"inputs": 2},
         "requests": [_req("https://a.vn/api/x").model_dump()]}]}]})
    merge.apply_record(proj)
    a = _snap(proj)
    merge.apply_record(proj)
    assert a == _snap(proj)
    assert [pp.trigger for pp in proj.modules[0].pages[0].popups] == ["(ghi thủ công)"]


@pytest.mark.parametrize("content", ["", "{", '{"sessions": [', "null", "[]", '{"sessions": null}',
                                     '{"sessions": [{"started": "s"}]}', '{"sessions": [null]}'])
def test_corrupt_or_partial_record_does_not_crash(proj, content):
    _write(proj, content)
    before = _snap(proj)
    merge.apply_record(proj)                          # không được ném ngoại lệ
    assert _snap(proj) == before
    merge.load_sessions(proj)
    merge.delete_session(proj, "x")


def test_segment_request_missing_fields_does_not_crash(proj):
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    _write(proj, {"sessions": [{"started": "s", "segments": [
        {"kind": "page", "url": lst, "page_url": lst, "requests": [{"url": "https://a.vn/api/y", "resource_type": "xhr"}]}]}]})
    merge.apply_record(proj)


def test_empty_sessions_and_duplicate_urls(proj):
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    seg = lambda u: {"kind": "page", "url": u, "page_url": u, "title": "T", "stats": {},  # noqa: E731
                     "requests": [_req("https://a.vn/api/new").model_dump()]}
    _write(proj, {"sessions": [{"started": "e", "segments": []},
                               {"started": "s", "segments": [seg(lst + "?a=1"), seg(lst + "/"), seg(lst + "?b=2")]}]})
    merge.apply_record(proj)
    assert len(proj.modules[0].pages) == 1
    assert [r.url for r in proj.modules[0].pages[0].requests].count("https://a.vn/api/new") == 1
    a = _snap(proj)
    merge.apply_record(proj)
    assert _snap(proj) == a


def test_delete_session_then_remerge(proj):
    sub = "https://a.vn/portal/cong-viec/can-xu-ly/chi-tiet"
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    seg = {"kind": "page", "url": sub, "page_url": sub, "title": "CT", "stats": {},
           "requests": [_req("https://a.vn/api/ct").model_dump()], "trigger": "dòng 1 · ⋮ › Chi tiết", "from_url": lst}
    _write(proj, {"sessions": [{"started": "s1", "segments": [seg]}, {"started": "s2", "segments": [seg]}]})
    merge.apply_record(proj)
    one = _snap(proj)
    assert merge.delete_session(proj, "s1")
    assert _snap(proj) == one                 # s2 cùng nội dung -> kết quả như cũ
    assert merge.delete_session(proj, "s2")
    merge.apply_record(proj)
    assert all(pg.origin != "manual" for m in proj.modules for pg in m.pages)
    assert all(pp.origin != "manual" for m in proj.modules for pg in m.pages for pp in pg.popups)


def test_manual_module_disabled_state_survives_reapply(proj):
    # BUG: bỏ tick phân hệ "Ghi thao tác thủ công" -> gộp lại (ghi thêm/xoá 1 lần ghi/phân tích lại 3b) tự tick lại
    _write(proj, {"sessions": [{"started": "s", "segments": [
        {"kind": "page", "url": "https://b.vn/khac", "page_url": "https://b.vn/khac", "title": "K", "stats": {},
         "requests": [_req("https://b.vn/api/k").model_dump()]}]}]})
    merge.apply_record(proj)
    m = next(x for x in proj.modules if x.name == merge.MANUAL_MODULE)
    m.enabled = False
    merge.apply_record(proj)
    m = next(x for x in proj.modules if x.name == merge.MANUAL_MODULE)
    assert m.enabled is False


def test_fixed_module_duplicates_existing_crawled_page(proj):
    # BUG: chọn "Gán vào phân hệ Tin tức" nhưng ghi trên màn hình đã quét của phân hệ Giao việc
    # -> cùng 1 URL xuất hiện 2 lần (2 màn hình) ở 2 phân hệ
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    _write(proj, {"sessions": [{"started": "s", "module": "Tin tức", "segments": [
        {"kind": "page", "url": lst, "page_url": lst, "title": "Việc", "stats": {},
         "requests": [_req("https://a.vn/api/tasks/count").model_dump()]}]}]})
    merge.apply_record(proj)
    urls = [pg.url for m in proj.modules for pg in m.pages]
    assert urls.count(lst) == 1, urls


def test_back_navigation_not_linked_as_subpage(proj):
    # BUG: đoạn trang có trigger là cú bấm cũ (vd 'Lưu phân công' trên trang con, rồi người dùng bấm Back / gõ URL)
    # -> _link_manual_clicks tạo 'Trang con' giả: trang phân công mở màn hình danh sách bằng nút 'Lưu phân công'
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    sub = "https://a.vn/portal/cong-viec/can-xu-ly/phan-cong?id=1"
    _write(proj, {"sessions": [{"started": "s", "segments": [
        {"kind": "page", "url": sub, "page_url": sub, "title": "Phân công", "stats": {},
         "requests": [_req("https://a.vn/api/users").model_dump()], "trigger": "dòng 1 · ⋮ › Phân công", "from_url": lst},
        {"kind": "page", "url": lst, "page_url": lst, "title": "Việc", "stats": {},
         "requests": [_req("https://a.vn/api/tasks").model_dump()], "trigger": "Lưu phân công", "from_url": sub}]}]})
    merge.apply_record(proj)
    sub_pg = next(pg for m in proj.modules for pg in m.pages if "phan-cong" in pg.url)
    assert not any(pp.trigger == "Lưu phân công" for pp in sub_pg.popups), [pp.trigger for pp in sub_pg.popups]


def test_manual_subpage_double_counted_with_auto_subpage(proj):
    # BUG: tự quét đã ghi trang con (kind=page, counted=True) trên trang danh sách; ghi thủ công mở lại trang con đó
    # -> thêm PageInfo riêng => trang con được tính 2 màn hình
    lst = "https://a.vn/portal/cong-viec/can-xu-ly"
    sub = "https://a.vn/portal/cong-viec/phan-cong?id=1"
    proj.modules[0].pages[0].popups.append(PopupInfo(trigger="dòng 1 · ⋮ › Phân công", kind="page", url=sub,
                                                      inputs=3, tables=1, counted=True))
    _write(proj, {"sessions": [{"started": "s", "segments": [
        {"kind": "page", "url": sub, "page_url": sub, "title": "Phân công", "stats": {"inputs": 3},
         "requests": [_req("https://a.vn/api/users").model_dump()], "trigger": "dòng 1 · ⋮ › Phân công",
         "from_url": lst}]}]})
    merge.apply_record(proj)
    n_screens = sum(len(m.pages) for m in proj.modules) + sum(pp.counted for m in proj.modules for pg in m.pages
                                                               for pp in pg.popups)
    assert n_screens == 3, n_screens        # danh sách + tin tức + phân công


def test_recorder_does_not_overwrite_corrupt_record(tmp_path):
    # BUG: record.json hỏng -> ManualRecorder coi như rỗng và _save() ghi đè => mất mọi lần ghi cũ, không cảnh báo
    from perftool.crawler.manual import ManualRecorder
    f = tmp_path / "record.json"
    f.write_text('{"sessions": [{"started": "old", "segments": [{"kind": "page"', encoding="utf-8")
    rec = ManualRecorder.__new__(ManualRecorder)
    ManualRecorder.__init__(rec, type("C", (), {})(), f, tmp_path / "record.stop")
    rec._save()
    assert "old" in f.read_text(encoding="utf-8") or list(tmp_path.glob("record*.bak*")), "dữ liệu cũ bị mất"
